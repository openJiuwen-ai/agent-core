# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retrieval regressions through tool entry points and the real HTTP parser."""

import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from openjiuwen.core.common.run_context import RunContext
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.web_research_rail import WebResearchBudgetRail
from openjiuwen.harness.tools.web import WebFetchWebpageTool, WebFreeSearchTool, is_free_search_enabled
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._budget import WEB_RESEARCH_BUDGET_KEY, WebResearchBudget
from openjiuwen.harness.tools.web._quality import WebContentError
from openjiuwen.harness.tools.web.free_search import _select_search_candidates


@pytest.fixture(autouse=True)
def clean_web_environment(monkeypatch, scripted_search_model):
    scripted_search_model.labels.update({
        "Unrelated device tutorial": "rejected", "Unrelated page": "rejected", "Asia news": "uncertain",
    })
    for name in (
        "FREE_SEARCH_DDG_ENABLED", "FREE_SEARCH_BING_ENABLED",
        "FREE_SEARCH_DEBUG", "WEB_PROXY_URL", "FREE_SEARCH_PROXY_URL",
        "FREE_SEARCH_BING_MARKET", "FREE_SEARCH_BING_FALLBACK_MARKET",
        "WEB_RESEARCH_SEARCHES_PER_ENGINE", "WEB_RESEARCH_MAX_ENGINES",
        "WEB_RESEARCH_MAX_FETCHES", "WEB_RESEARCH_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


def fake_http(monkeypatch, handler):
    calls = []

    async def request(session, method, url, **kwargs):
        calls.append((url, kwargs))
        status, text = handler(url)
        return status, {"Content-Type": "text/html; charset=utf-8"}, text.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    return calls


def bing_page(title, snippet="", url="https://source.invalid/story"):
    return (
        f'<li class="b_algo"><h2><a href="{url}">{title}</a></h2>'
        f'<div class="b_caption"><p>{snippet}</p></div></li>'
    )


def test_dates_and_keywords_without_a_model_are_not_proof_of_relevance():
    for query in ("Asia news 2026", "arbitrary task"):
        selected, _ = _select_search_candidates(query, [
            {"title": "Asia news 2009", "url": "https://source.invalid/article", "date": "2026-11-03"},
        ])
        assert selected[0]["relevance"] == "uncertain"


@pytest.mark.asyncio
async def test_explicit_bing_does_not_call_other_engines_or_return_rejected_urls(monkeypatch):
    tool = WebFreeSearchTool(enabled_engines=("bing", "duckduckgo"))
    calls = fake_http(monkeypatch, lambda url: (200, bing_page("Unrelated device tutorial")))
    result = await tool.invoke({"query": "Hangzhou weather", "provider": "bing"})
    assert "Content status: low_relevance" in result
    assert "https://source.invalid/story" not in result
    assert len(calls) == 2 and all("bing.com/search" in url for url, _ in calls)


@pytest.mark.asyncio
async def test_one_relevant_result_is_enough_and_unlinked_card_cannot_displace_it(monkeypatch):
    page = '<div class="b_card"><h2>Hangzhou weather</h2><p>Search widget</p></div>'
    page += bing_page("Hangzhou weather forecast", "Local observations")
    calls = fake_http(monkeypatch, lambda url: (200, page))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({
        "query": "Hangzhou weather", "max_results": 1,
    })
    assert "Content status: ok" in result and "https://source.invalid/story" in result
    assert "Search widget" not in result and len(calls) == 1
    assert "second fetch is not mandatory" in result


@pytest.mark.asyncio
async def test_ddg_challenge_skips_reader_but_can_use_independent_enabled_engine(monkeypatch):
    def response(url):
        if "duckduckgo.com" in url:
            return 202, '<form id="challenge-form" action="/anomaly.js"></form>'
        return 200, bing_page("Hangzhou weather forecast")

    calls = fake_http(monkeypatch, response)
    result = await WebFreeSearchTool(enabled_engines=("duckduckgo", "bing")).invoke({"query": "Hangzhou weather"})
    assert "Free search results (Bing)" in result
    assert len(calls) == 2 and all("r.jina.ai" not in url for url, _ in calls)


@pytest.mark.asyncio
async def test_removed_provider_cannot_be_enabled_by_legacy_configuration(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_360_ENABLED", "true")
    assert not is_free_search_enabled()
    calls = fake_http(monkeypatch, lambda url: (200, bing_page("Hangzhou weather")))
    disabled = await WebFreeSearchTool().invoke({"query": "Hangzhou weather"})
    assert "Content status: disabled" in disabled and not calls
    scoped = await WebFreeSearchTool(enabled_engines=("360",)).invoke({"query": "Hangzhou weather"})
    assert "Content status: disabled" in scoped and not calls
    tool = WebFreeSearchTool(enabled_engines=("bing", "duckduckgo"))
    assert tool.card.input_params["properties"]["provider"]["enum"] == ["auto", "bing", "duckduckgo"]
    invalid = await tool.invoke({"query": "Hangzhou weather", "provider": "360"})
    assert "Content status: invalid_input" in invalid and not calls


@pytest.mark.asyncio
async def test_bing_reads_past_navigation_and_uncertain_news_before_limiting_results(monkeypatch):
    fake_http(monkeypatch, lambda url: (200, '''
        <li class="b_algo"><h2><a href="https://bing.com/search?q=asia">Asia news</a></h2></li>
        <li class="b_algo"><h2><a href="https://source.invalid/old">Asia news</a></h2>
        <p><span class="news_dt">2009年1月6日</span>Historical report</p></li>
        <li class="b_algo"><h2><a href="https://source.invalid/current">Asia news briefing</a></h2>
        <p><span class="news_dt">2026年11月2日</span>Regional developments</p></li>
    '''))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({
        "query": "Asia news 2026-11-02", "max_results": 1,
    })
    assert "Content status: ok" in result and "https://source.invalid/current" in result
    assert "Search-reported date (unverified): 2026年11月2日" in result
    assert "https://bing.com/search" not in result and "https://source.invalid/old" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "page"),
    [
        (200, '<title>Just a moment...</title><p>Checking your browser</p>'),
        (200, '{"_waf_bd8ce2ce37": "challenge"}'),
        (200, '<html><body><pre>{"_waf_test": "challenge"}</pre>' + 'AbCd0123' * 2500 + '</body></html>'),
        (200, '<body>\ufeff{&quot;_waf_test&quot;: &quot;challenge&quot;}</body>'),
        (200, '<script src="https://geo.captcha-delivery.com/captcha/"></script>Please enable JS and disable any ad blocker'),
        (403, 'Access denied'),
        (429, 'Rate limited'),
    ],
)
async def test_fetch_block_is_failure_even_with_http_200_and_never_reader_retried(monkeypatch, status, page):
    calls = fake_http(monkeypatch, lambda url: (status, page))
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/story"})
    assert "Content status: blocked" in result and "Content status: ok" not in result
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reader_content", "expected"),
    [
        ("Title: Source\nURL Source: https://source.invalid/\nMarkdown Content:\n", "empty"),
        ("Title: Source\nWarning: This page contains CAPTCHA\nMarkdown Content:\nCheck your browser", "blocked"),
        ('Title: Source\nMarkdown Content:\n{"_waf_test":"challenge"}\nAbCd0123', "blocked"),
        ("Title: Source\nMarkdown Content:\n%PDF-1.4 binary payload", "unsupported_content"),
        ("Title: Not Found\nWarning: Target URL returned error 404: Not Found\nMarkdown Content:\nPage not found", "source_error"),
        ("Title: Source\nMarkdown Content:\nThe observed temperature is 21 C.", "ok"),
    ],
)
async def test_js_shell_reader_result_is_validated_and_shares_timeout(monkeypatch, reader_content, expected):
    def response(url):
        if "r.jina.ai" in url:
            return 200, reader_content
        return 200, '<title>Source</title><body><div id="app"></div><script src="app.js"></script></body>'

    calls = fake_http(monkeypatch, response)
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/story", "timeout_seconds": 10})
    assert f"Content status: {expected}" in result
    assert len(calls) == 2 and 0 < calls[1][1]["timeout_seconds"] <= calls[0][1]["timeout_seconds"]


@pytest.mark.asyncio
async def test_scoped_js_shell_does_not_escape_through_reader(monkeypatch):
    calls = fake_http(monkeypatch, lambda url: (200, '<body><script src="app.js"></script></body>'))
    result = await WebFetchWebpageTool(allowed_domains=("source.invalid",)).invoke({"url": "https://source.invalid/"})
    assert "Content status: dynamic_shell" in result and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("content_type", "body"), [
    ("application/pdf", b"%PDF-1.4\n1 0 obj << /Type /Catalog >>"),
    ("text/plain", b"\xef\xbb\xbf  %PDF-1.7\nraw-pdf-sentinel"),
    ("text/html", b"%PDF-1.4\nraw-pdf-sentinel"),
    ("application/octet-stream", b"binary-sentinel"),
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("", b"PK\x03\x04zip-sentinel"),
])
async def test_binary_documents_never_reach_model_as_success_or_trigger_reader(monkeypatch, content_type, body):
    request = AsyncMock(return_value=(200, {"content-type": content_type}, body, "https://source.invalid/file", False))
    monkeypatch.setattr(_http, "request", request)
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/file"})
    assert "Content status: unsupported_content" in result and "Content status: ok" not in result
    assert "sentinel" not in result and "%PDF-" not in result
    request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["text/plain", "application/json", "application/problem+json"])
async def test_text_and_structured_data_remain_readable(monkeypatch, content_type):
    body = b'{"article":"A PDF header starts with %PDF-1.4; CAPTCHA and WAF are security mechanisms."}'
    request = AsyncMock(return_value=(200, {"Content-Type": content_type}, body, "https://source.invalid/article", False))
    monkeypatch.setattr(_http, "request", request)
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/article"})
    assert "Content status: ok" in result and "security mechanisms" in result


@pytest.mark.asyncio
async def test_pdf_access_denied_is_blocked_not_unsupported(monkeypatch):
    request = AsyncMock(return_value=(403, {"Content-Type": "application/pdf"}, b"%PDF-1.4", "https://source.invalid/file", False))
    monkeypatch.setattr(_http, "request", request)
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/file"})
    assert "Content status: blocked" in result and "unsupported_content" not in result
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_article_keeps_relative_links_dates_and_missing_table_fields(monkeypatch):
    page = '''<html><title>Quote</title><nav>Navigation noise</nav><article>
        <header><time>2026-11-02 16:00</time></header>
        <p>An article discussing CAPTCHA is not a challenge page.</p>
        <a href="/quotes/history">Historical data</a>
        <table><tr><th>Previous close</th><th>Open</th></tr><tr><td></td><td>23100</td></tr></table>
        <dl><dt>Volume</dt><dd></dd><dt>Temperature</dt><dd>21 C</dd></dl>
        </article></html>'''
    fake_http(monkeypatch, lambda url: (200, page))
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/quote"})
    assert "Content status: ok" in result
    assert "Previous close | Open\n[missing] | 23100" in result
    assert "Volume: [missing]" in result and "Temperature: 21 C" in result
    assert "[Historical data](https://source.invalid/quotes/history)" in result
    assert "2026-11-02 16:00" in result and "Navigation noise" not in result


@pytest.mark.asyncio
async def test_fetch_zero_max_chars_preserves_explicit_no_clipping(monkeypatch):
    text = "source content " * 2000
    fake_http(monkeypatch, lambda url: (200, f"<article>{text}</article>"))
    result = await WebFetchWebpageTool().invoke({"url": "https://source.invalid/", "max_chars": 0})
    assert text.strip() in result and "[truncated]" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("header_size", [1000, 12000, 64000, 70000])
async def test_fetch_real_http_accepts_large_but_bounded_headers(monkeypatch, header_size):
    async def handler(request):
        return web.Response(text="<article>Forecast temperature 21 C.</article>", content_type="text/html", headers={"Content-Security-Policy": "a" * header_size})

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        await web.SockSite(runner, sock).start()
        try:
            result = await WebFetchWebpageTool().invoke({"url": f"http://127.0.0.1:{port}/"})
        finally:
            await runner.cleanup()
    if header_size > 65536:
        assert "[ERROR]" in result
    else:
        assert "Forecast temperature 21 C." in result and "Content status: ok" in result


@pytest.mark.asyncio
async def test_budget_shared_across_real_ability_manager_calls_and_rounds(monkeypatch):
    calls = fake_http(monkeypatch, lambda url: (200, bing_page("Hangzhou weather forecast")))
    search = WebFreeSearchTool(enabled_engines=("bing",))
    fetch = WebFetchWebpageTool()
    manager = AbilityManager()
    manager.add([search.card, fetch.card])
    tools = {search.card.id: search, fetch.card.id: fetch}
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda tool_id, **kwargs: tools[tool_id])
    parent = AgentCallbackContext(agent=SimpleNamespace(
        agent_callback_manager=SimpleNamespace(execute=AsyncMock()), ability_manager=manager,
    ))
    rail = WebResearchBudgetRail()
    await rail.before_model_call(parent)
    session = SimpleNamespace(session_id="test-budget")

    def search_call(index):
        return ToolCall(id=f"s-{index}", type="function", name="free_search", arguments=json.dumps({"query": "Hangzhou weather"}))

    results = await manager.execute(parent, [search_call(i) for i in range(5)], session=session)
    assert sum("Content status: ok" in result[0] for result in results) == 2
    assert sum("budget_exhausted" in result[0] for result in results) == 3
    await rail.before_model_call(parent)
    again = await manager.execute(parent, search_call(6), session=session)
    assert "budget_exhausted" in again[0][0] and len(calls) == 2

    results = await manager.execute(parent, [
        ToolCall(id=f"f-{i}", type="function", name="fetch_webpage", arguments=json.dumps({"url": "https://source.invalid/story"}))
        for i in range(5)
    ], session=session)
    assert sum("Content status: ok" in result[0] for result in results) == 3
    assert sum("budget_exhausted" in result[0] for result in results) == 2
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_outer_invocation_and_inner_rounds_share_budget_but_new_invocation_does_not():
    from openjiuwen.harness.deep_agent import DeepAgent

    rail = WebResearchBudgetRail()
    original = RunContext(extra={"keep": "value", "raw_query": "original"})
    outer = AgentCallbackContext(agent=None, inputs=InvokeInputs(query="search", run_context=original))
    await rail.before_invoke(outer)
    budget = outer.extra[WEB_RESEARCH_BUDGET_KEY]
    assert budget.started_at is None and WEB_RESEARCH_BUDGET_KEY not in original.extra
    for index in range(2):
        effective = DeepAgent._to_effective_inputs(outer.inputs)
        round_context = DeepAgent._run_context_for_task_loop_round(effective["run_context"], "follow up", True)
        inner = AgentCallbackContext(agent=None, extra={"run_context": round_context})
        await rail.before_model_call(inner)
        assert inner.extra[WEB_RESEARCH_BUDGET_KEY] is budget
        budget.reserve_search("bing")
    with pytest.raises(WebContentError, match="budget_exhausted"):
        budget.reserve_search("bing")
    await rail.before_invoke(outer)
    assert outer.extra[WEB_RESEARCH_BUDGET_KEY] is not budget
    outer.extra[WEB_RESEARCH_BUDGET_KEY].reserve_search("bing")


def test_budget_provider_count_deadline_and_reader_share_limits():
    budget = WebResearchBudget()
    budget.reserve_search("duckduckgo")
    budget.reserve_search("duckduckgo-jina")
    with pytest.raises(WebContentError, match="attempt limit"):
        budget.reserve_search("duckduckgo")
    budget.reserve_search("bing")
    with pytest.raises(WebContentError, match="source limit"):
        budget.reserve_search("other-provider")
    budget.started_at -= 91
    with pytest.raises(WebContentError, match="deadline"):
        budget.reserve_fetch()


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [4, 11])
async def test_proxy_retry_does_not_reset_total_request_deadline(monkeypatch, elapsed):
    clock = iter((10.0, 10.0 + elapsed))
    monkeypatch.setattr(_http, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(_http, "_resolve_proxy", lambda url, proxy: None)
    timeouts = []

    async def attempt(session, method, url, **kwargs):
        timeouts.append(kwargs["timeout"].total)
        if len(timeouts) == 1:
            raise aiohttp.ClientHttpProxyError(request_info=None, history=(), status=502)
        return 200, {}, b"ok", url, False

    monkeypatch.setattr(_http, "_do_request", attempt)
    if elapsed > 10:
        with pytest.raises(TimeoutError, match="budget exhausted"):
            await _http.request(None, "GET", "https://source.invalid/", timeout_seconds=10)
        assert timeouts == [10]
    else:
        result = await _http.request(None, "GET", "https://source.invalid/", timeout_seconds=10)
        assert result[2] == b"ok" and timeouts == [10, 6]


@pytest.mark.asyncio
async def test_shared_tool_instance_does_not_share_budget_between_invocations(monkeypatch):
    fake_http(monkeypatch, lambda url: (200, bing_page("Hangzhou weather forecast")))
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    rail = WebResearchBudgetRail()
    first = AgentCallbackContext(agent=None)
    second = AgentCallbackContext(agent=None)
    await rail.before_model_call(first)
    await rail.before_model_call(second)
    for index in range(2):
        assert "Content status: ok" in await tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=first)
    assert "budget_exhausted" in await tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=first)
    assert "Content status: ok" in await tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=second)
