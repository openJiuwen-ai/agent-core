# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Free search must not select a reader forbidden by host egress policy."""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.security.outbound import policy
from openjiuwen.harness.tools.web import free_search


@pytest.fixture(autouse=True)
def isolate_policy_and_search_env(monkeypatch):
    monkeypatch.setattr(policy, "_state", policy.get_host_exit_state())
    policy.reset_host_exit_policy()
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_DEBUG", "false")


def _publish(mode):
    if mode != "unset":
        section = {
            "enabled": mode != "disabled",
            "enforce_host_exit": mode != "not_enforced",
            "defaults": "allow",
            "urls": {},
        }
        if mode == "error":
            section["max_request_mb"] = 0
        policy.publish_host_exit_policy({"net_guard": section})
    assert policy.get_host_exit_state().mode == mode


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,reader_allowed", [
    ("unset", True), ("disabled", True), ("not_enforced", True),
    ("active", False), ("error", False),
])
async def test_reader_selection_respects_host_policy(monkeypatch, mode, reader_allowed):
    _publish(mode)
    sent = []

    async def request(_session, _method, url, **_kwargs):
        sent.append(url)
        if "r.jina.ai" in url:
            return 200, {}, b"[test query](https://example.com/result)", url, False
        if "duckduckgo.com" in url:
            return 500, {}, b"unavailable", url, False
        body = b'<li class="b_algo"><h2><a href="https://example.com/result">test query</a></h2></li>'
        return 200, {}, body, url, False

    monkeypatch.setattr(free_search._http, "request", request)
    engine, rows = await free_search.WebFreeSearchTool._search_free(None, "test query", 5, 10)

    assert engine == ("duckduckgo-jina" if reader_allowed else "bing")
    assert rows[0]["url"] == "https://example.com/result"
    assert len(sent) == 2
    assert "duckduckgo.com" in sent[0]
    assert any("r.jina.ai" in url for url in sent) is reader_allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["active", "error"])
async def test_skipped_reader_is_not_reported_as_failed_engine(monkeypatch, mode):
    _publish(mode)
    sent = []

    async def request(_session, _method, url, **_kwargs):
        sent.append(url)
        raise RuntimeError("synthetic engine failure")

    monkeypatch.setattr(free_search._http, "request", request)
    with pytest.raises(BaseError) as exc:
        await free_search.WebFreeSearchTool._search_free(None, "test query", 5, 10)
    assert len(sent) == 2
    assert all("r.jina.ai" not in url for url in sent)
    assert "duckduckgo-jina" not in str(exc.value)
    assert "duckduckgo:" in str(exc.value)
    assert "bing:" in str(exc.value)
