# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bing routing through real tool invocation with isolated HTTP responses."""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.web_research_rail import WebResearchBudgetRail
from openjiuwen.harness.tools.web import WebFreeSearchTool, _http
from openjiuwen.harness.tools.web import _budget
from openjiuwen.harness.tools.web._budget import WEB_RESEARCH_BUDGET_KEY, WebResearchBudget


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, scripted_search_model):
    scripted_search_model.labels.update({
        "Unrelated device manual": "rejected",
        "Unrelated devices": "rejected",
        "What is artificial intelligence (AI)?": "rejected",
        "AI agents take the next step": "uncertain",
    })
    for name in (
        "FREE_SEARCH_BING_MARKET", "FREE_SEARCH_BING_FALLBACK_MARKET",
        "FREE_SEARCH_DEBUG", "FREE_SEARCH_DEBUG_DIR",
        "WEB_RESEARCH_SEARCHES_PER_ENGINE", "WEB_RESEARCH_MAX_ENGINES",
        "WEB_RESEARCH_MAX_FETCHES", "WEB_RESEARCH_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def bing_page(title: str, slug: str = "weather") -> str:
    return f'<li class="b_algo"><h2><a href="https://source.invalid/{slug}">{title}</a></h2></li>'


def fake_http(monkeypatch, handler):
    calls = []

    async def request(session, method, url, **kwargs):
        parameters = parse_qs(urlsplit(url).query)
        calls.append((url, parameters, kwargs))
        status, text = handler(parameters)
        return status, {"Content-Type": "text/html; charset=utf-8"}, text.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Hangzhou weather", "\u676d\u5dde\u5929\u6c14"])
async def test_default_market_is_independent_of_query_and_headers(monkeypatch, query):
    calls = fake_http(monkeypatch, lambda params: (200, bing_page(query + " forecast")))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": query})
    assert "Content status: ok" in result and len(calls) == 1
    _, params, options = calls[0]
    assert params == {"q": [query], "setlang": ["en-US"], "mkt": ["en-US"], "cc": ["US"]}
    assert options["headers"]["Accept-Language"].startswith("en-US" if query.isascii() else "zh-CN")


@pytest.mark.asyncio
async def test_low_relevance_switches_once_within_one_budgeted_search(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_BING_MARKET", " zh-cn ")
    calls = fake_http(monkeypatch, lambda params: (
        200, bing_page("Unrelated device manual", "rejected") if params["mkt"] == ["zh-CN"]
        else bing_page("Hangzhou weather forecast"),
    ))
    ctx = AgentCallbackContext(agent=None)
    await WebResearchBudgetRail().before_model_call(ctx)
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    result = await tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=ctx)
    assert "Content status: ok" in result and "https://source.invalid/rejected" not in result
    assert [params["mkt"][0] for _, params, _ in calls] == ["zh-CN", "en-US"]
    assert ctx.extra[WEB_RESEARCH_BUDGET_KEY].searches == {"bing": 1}
    retry = await tool.invoke({"query": "Hangzhou weather forecast"}, _tool_callback_context=ctx)
    assert "Content status: ok" in retry and len(calls) == 4
    assert [params["q"][0] for _, params, _ in calls] == [
        "Hangzhou weather", "Hangzhou weather", "Hangzhou weather forecast", "Hangzhou weather forecast",
    ]
    assert [params["mkt"][0] for _, params, _ in calls] == ["zh-CN", "en-US", "zh-CN", "en-US"]
    assert ctx.extra[WEB_RESEARCH_BUDGET_KEY].searches == {"bing": 2}
    exhausted = await tool.invoke({"query": "Hangzhou weather tomorrow"}, _tool_callback_context=ctx)
    assert "Content status: budget_exhausted" in exhausted and len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_relevant", [True, False])
async def test_generic_ai_results_do_not_stop_market_fallback_or_consume_extra_slots(monkeypatch, fallback_relevant):
    generic = (
        '<li class="b_algo"><h2><a href="https://source.invalid/generic">What is artificial intelligence (AI)?</a></h2>'
        '<p>AI research trends report</p></li>'
    )
    calls = fake_http(monkeypatch, lambda params: (
        200, bing_page("Agentic AI research trends report", "agents")
        if params["mkt"] == ["zh-CN"] and fallback_relevant else generic,
    ))
    budget = WebResearchBudget()
    ctx = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    for query in ("AI Agent \u524d\u6cbf\u65b9\u5411 2026", "agentic AI research trends report"):
        result = await tool.invoke({"query": query, "provider": "bing"}, _tool_callback_context=ctx)
        assert ("Content status: ok" if fallback_relevant else "Content status: low_relevance") in result
        assert "https://source.invalid/generic" not in result
        assert ("https://source.invalid/agents" in result) is fallback_relevant
    assert [params["mkt"][0] for _, params, _ in calls] == ["en-US", "zh-CN", "en-US", "zh-CN"]
    assert budget.searches == {"bing": 2}
    result = await tool.invoke({"query": "AI Agent latest research"}, _tool_callback_context=ctx)
    assert "Content status: budget_exhausted" in result and len(calls) == 4


@pytest.mark.asyncio
async def test_generic_ai_and_specific_provisional_results_only_return_specific_candidate(monkeypatch):
    page = bing_page("What is artificial intelligence (AI)?", "generic")
    page += bing_page("AI agents take the next step", "agents")
    calls = fake_http(monkeypatch, lambda params: (200, page))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "AI Agent news"})
    assert "Content status: uncertain" in result and len(calls) == 1
    assert "https://source.invalid/agents" in result and "https://source.invalid/generic" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_relevant", [True, False])
async def test_last_search_slot_allows_one_fallback_without_refunding_quota(monkeypatch, fallback_relevant):
    calls = fake_http(monkeypatch, lambda params: (
        200, bing_page("Hangzhou weather forecast") if params["mkt"] == ["zh-CN"] and fallback_relevant
        else bing_page("Unrelated devices"),
    ))
    budget = WebResearchBudget(searches_per_engine=1)
    ctx = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    result = await tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=ctx)
    assert ("Content status: ok" if fallback_relevant else "Content status: low_relevance") in result
    assert len(calls) == 2 and budget.searches == {"bing": 1}
    result = await tool.invoke({"query": "Hangzhou weather forecast"}, _tool_callback_context=ctx)
    assert "Content status: budget_exhausted" in result and len(calls) == 2


@pytest.mark.asyncio
async def test_concurrent_searches_share_quota_but_each_owns_its_fallback(monkeypatch):
    calls = []

    async def request(session, method, url, **kwargs):
        market = parse_qs(urlsplit(url).query)["mkt"][0]
        calls.append(market)
        await asyncio.sleep(0)
        title = "Unrelated devices" if market == "en-US" else "Hangzhou weather forecast"
        return 200, {"Content-Type": "text/html"}, bing_page(title).encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    budget = WebResearchBudget()
    ctx = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    results = await asyncio.gather(*(
        tool.invoke({"query": "Hangzhou weather"}, _tool_callback_context=ctx) for _ in range(3)
    ))
    assert sum("Content status: ok" in result for result in results) == 2
    assert sum("Content status: budget_exhausted" in result for result in results) == 1
    assert calls.count("en-US") == 2 and calls.count("zh-CN") == 2
    assert budget.searches == {"bing": 2}


@pytest.mark.asyncio
async def test_two_low_relevance_markets_fail_without_more_internal_retries(monkeypatch):
    monkeypatch.setenv("WEB_RESEARCH_SEARCHES_PER_ENGINE", "10")
    calls = fake_http(monkeypatch, lambda params: (200, bing_page("Unrelated devices", "rejected")))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "Hangzhou weather"})
    assert "Content status: low_relevance" in result and len(calls) == 2
    assert "Recommended fetch targets" not in result and "https://source.invalid/rejected" not in result
    assert "bing (en-US): low_relevance" in result and "bing (zh-CN): low_relevance" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", ["none", "EN-us"])
async def test_disabled_or_duplicate_fallback_makes_one_request(monkeypatch, fallback):
    monkeypatch.setenv("FREE_SEARCH_BING_FALLBACK_MARKET", fallback)
    calls = fake_http(monkeypatch, lambda params: (200, bing_page("Unrelated devices")))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "Hangzhou weather"})
    assert "Content status: low_relevance" in result and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "value"), [
    ("FREE_SEARCH_BING_MARKET", "none"),
    ("FREE_SEARCH_BING_MARKET", "unsupported"),
    ("FREE_SEARCH_BING_FALLBACK_MARKET", "en-US&cc=CN"),
])
async def test_invalid_market_configuration_fails_without_http(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    calls = fake_http(monkeypatch, lambda params: (200, bing_page("Hangzhou weather")))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "Hangzhou weather"})
    assert "Content status: invalid_input" in result and key in result and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "page", "expected"), [
    (200, "<html><body>No results</body></html>", "empty"),
    (200, "<title>Just a moment...</title>", "blocked"),
    (403, "Access denied", "blocked"),
    (429, "Rate limit", "blocked"),
    (200, bing_page("AI agents take the next step"), "uncertain"),
])
async def test_only_low_relevance_triggers_market_fallback(monkeypatch, status, page, expected):
    calls = fake_http(monkeypatch, lambda params: (status, page))
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "AI Agent news"})
    assert f"Content status: {expected}" in result and len(calls) == 1


@pytest.mark.asyncio
async def test_transport_error_does_not_trigger_market_fallback(monkeypatch):
    def response(params):
        raise TimeoutError("test transport timeout")

    calls = fake_http(monkeypatch, response)
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "Hangzhou weather"})
    assert "Content status: transport_error" in result and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["attempts", "deadline", "sources"])
async def test_market_retry_cannot_reset_budget_limits(monkeypatch, limit):
    clock = [100.0]
    monkeypatch.setattr(_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    budget = WebResearchBudget(searches_per_engine=1 if limit == "attempts" else 2, max_engines=1)
    if limit == "attempts":
        budget.reserve_search("bing")
    if limit == "sources":
        budget.reserve_search("duckduckgo")

    def response(params):
        if limit == "deadline":
            clock[0] += 91
        return 200, bing_page("Unrelated devices")

    calls = fake_http(monkeypatch, response)
    ctx = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke(
        {"query": "Hangzhou weather"}, _tool_callback_context=ctx,
    )
    assert "Content status: budget_exhausted" in result
    assert len(calls) == (1 if limit == "deadline" else 0)


@pytest.mark.asyncio
async def test_fallback_receives_remaining_time_not_a_new_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response(params):
        clock[0] += 80
        return 200, bing_page("Unrelated devices")

    calls = fake_http(monkeypatch, response)
    await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "Hangzhou weather"})
    assert [options["timeout_seconds"] for _, _, options in calls] == [20, 10]


@pytest.mark.asyncio
async def test_auto_can_switch_engine_within_unchanged_source_budget(monkeypatch):
    calls = []

    async def request(session, method, url, **kwargs):
        calls.append(url)
        if "duckduckgo.com" in url:
            return 202, {}, b'<form id="challenge-form" action="/anomaly.js"></form>', url, False
        params = parse_qs(urlsplit(url).query)
        text = bing_page("Unrelated devices" if params["mkt"] == ["en-US"] else "Hangzhou weather")
        return 200, {"Content-Type": "text/html"}, text.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    budget = WebResearchBudget()
    ctx = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    result = await WebFreeSearchTool(enabled_engines=("duckduckgo", "bing")).invoke(
        {"query": "Hangzhou weather"}, _tool_callback_context=ctx,
    )
    assert "Free search results (Bing)" in result and len(calls) == 3
    assert budget.searches == {"duckduckgo": 1, "bing": 1}


@pytest.mark.asyncio
async def test_market_diagnostics_preserve_both_responses_and_domain_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_SEARCH_DEBUG", "true")
    monkeypatch.setenv("FREE_SEARCH_DEBUG_DIR", str(tmp_path))
    calls = fake_http(monkeypatch, lambda params: (
        200, bing_page("Unrelated devices", "rejected") if params["mkt"] == ["en-US"]
        else bing_page("Hangzhou weather forecast"),
    ))
    tool = WebFreeSearchTool(enabled_engines=("bing",), allowed_domains=("source.invalid",))
    result = await tool.invoke({"query": "Hangzhou weather"})
    assert "Content status: ok" in result
    assert [params["q"] for _, params, _ in calls] == [["Hangzhou weather (site:source.invalid)"]] * 2
    responses = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*_bing_response.json")]
    assert len(responses) == 2
    assert any("Unrelated devices" in row["raw_html"] for row in responses)
    assert any("Hangzhou weather forecast" in row["raw_html"] for row in responses)
    filtered = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*_bing_filtered.json")]
    assert {row["market"]: row["selected_count"] for row in filtered} == {"en-US": 0, "zh-CN": 1}
    assert all(row["decisions"] for row in filtered)


def test_existing_budget_defaults_are_unchanged():
    budget = WebResearchBudget.from_env()
    assert (budget.searches_per_engine, budget.max_engines, budget.max_fetches, budget.timeout_seconds) == (2, 2, 3, 90)
