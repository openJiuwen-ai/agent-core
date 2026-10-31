# coding: utf-8

"""Unit tests for the endpoint reachability cache used by the web tools."""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.web import _reachability
from openjiuwen.harness.tools.web.free_search import (
    WebFreeSearchTool,
    _FreeSearchRequest,
    _engine_host,
)


class _FakeClock:
    """Controllable stand-in for ``time.monotonic``."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def reach_state(monkeypatch):
    """Isolate the process-local cache and drive it with a fake clock."""
    _reachability._dead_until.clear()
    _reachability._attempts.clear()
    clock = _FakeClock()
    monkeypatch.setattr(_reachability.time, "monotonic", clock.monotonic)
    yield clock
    _reachability._dead_until.clear()
    _reachability._attempts.clear()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://HTML.DuckDuckGo.com/html/?q=x", "html.duckduckgo.com"),
        ("https://user:pw@Example.COM:8443/path", "example.com"),
        ("http://[::1]:8080/", "::1"),
        ("https://www.bing.com/search?q=x", "www.bing.com"),
        ("not a url", ""),
        ("", ""),
        ("http://[::1", ""),  # unterminated IPv6 literal raises ValueError
    ],
)
def test_host_from_extracts_lowercased_host(url, expected):
    assert _reachability.host_from(url) == expected


def test_mark_dead_follows_backoff_ladder(reach_state):
    host = "dead.example"
    for step in (300, 900, 3600, 21600):
        _reachability.mark_dead(host)
        assert _reachability.is_dead(host)
        reach_state.advance(step - 1)
        assert _reachability.is_dead(host)
        reach_state.advance(2)
        assert not _reachability.is_dead(host)

    # Beyond the ladder the backoff stays capped at the last step.
    _reachability.mark_dead(host)
    _reachability.mark_dead(host)
    reach_state.advance(21600 - 1)
    assert _reachability.is_dead(host)
    reach_state.advance(2)
    assert not _reachability.is_dead(host)


def test_mark_alive_clears_state_and_resets_ladder(reach_state):
    host = "healing.example"
    _reachability.mark_dead(host)
    _reachability.mark_dead(host)  # second failure would back off 900s
    _reachability.mark_alive(host)
    assert not _reachability.is_dead(host)

    # A success resets the ladder, so the next failure backs off 300s only.
    _reachability.mark_dead(host)
    reach_state.advance(301)
    assert not _reachability.is_dead(host)


def test_empty_host_and_case_normalization(reach_state):
    _reachability.mark_dead("")
    assert not _reachability.is_dead("")

    _reachability.mark_dead("Example.COM")
    assert _reachability.is_dead("example.com")
    assert _reachability.is_dead("EXAMPLE.com")


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("duckduckgo", "html.duckduckgo.com"),
        ("duckduckgo-jina", "r.jina.ai"),
        ("bing", "www.bing.com"),
        ("baidu-scholar", "xueshu.baidu.com"),
        ("baidu-web", "www.baidu.com"),
        ("cnki", "kns.cnki.net"),
        ("wanfang", "s.wanfangdata.com.cn"),
        ("unknown-engine", ""),
    ],
)
def test_engine_host_mapping(engine, expected):
    assert _engine_host(engine) == expected


class _EngineRecorder:
    """Records which free-search engines a fallback pass actually attempts."""

    def __init__(self):
        self.attempted: list[str] = []

    def stub(self, name: str):
        async def _run(session, query, max_results, timeout_seconds, **kwargs):
            self.attempted.append(name)
            return []

        return _run


@pytest.mark.asyncio
async def test_dead_engines_skipped_and_all_dead_guardrail(monkeypatch, reach_state):
    recorder = _EngineRecorder()
    monkeypatch.setattr(WebFreeSearchTool, "_search_duckduckgo", recorder.stub("duckduckgo"))
    monkeypatch.setattr(
        WebFreeSearchTool, "_search_duckduckgo_via_jina", recorder.stub("duckduckgo-jina")
    )
    monkeypatch.setattr(WebFreeSearchTool, "_search_bing", recorder.stub("bing"))

    request = _FreeSearchRequest(
        session=None,
        query="example query",
        max_results=5,
        timeout_seconds=10,
        enabled_engines=("duckduckgo", "bing"),
    )

    # Nothing marked dead: every configured engine is attempted.
    with pytest.raises(BaseError):
        await WebFreeSearchTool._search_free(request)
    assert recorder.attempted == ["duckduckgo", "duckduckgo-jina", "bing"]

    # One dead host: only its engine is skipped, the rest still run.
    recorder.attempted.clear()
    _reachability.mark_dead("html.duckduckgo.com")
    with pytest.raises(BaseError):
        await WebFreeSearchTool._search_free(request)
    assert recorder.attempted == ["duckduckgo-jina", "bing"]

    # All dead: the guardrail retries everything so the state can self-heal.
    recorder.attempted.clear()
    _reachability.mark_dead("r.jina.ai")
    _reachability.mark_dead("www.bing.com")
    with pytest.raises(BaseError):
        await WebFreeSearchTool._search_free(request)
    assert recorder.attempted == ["duckduckgo", "duckduckgo-jina", "bing"]
