# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model contract, isolation, degradation and real tool dispatch regressions."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, ReasoningConfig, ToolCall
from openjiuwen.core.foundation.llm.reasoning import resolve_reasoning_plan
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.web_research_rail import WebResearchBudgetRail
from openjiuwen.harness.tools.web import WebFreeSearchTool, _http
from openjiuwen.harness.tools.web._assessment import CandidateAssessment, SearchAssessment, assess_candidates
from openjiuwen.harness.tools.web._budget import WEB_RESEARCH_BUDGET_KEY, WebResearchBudget
from openjiuwen.harness.tools.web.free_search import _select_search_candidates


def candidate(index):
    return {"title": f"Document {index}", "snippet": "An excerpt", "url": f"https://source.invalid/{index}"}


def model_reply(results):
    return SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps({"results": results}))))


@pytest.mark.asyncio
async def test_one_bounded_batch_keeps_original_task_and_strips_url_secrets():
    model = model_reply([{"id": 9, "relevance": "uncertain", "reason": "Missing detail"}])
    row = dict(candidate(9), title="T" * 1000, snippet="S" * 5000,
               url="https://user:secret@source.invalid/doc?token=secret#secret")
    result = await assess_candidates(model, "subquestion", [(9, row)], original_task="original task")
    assert result.status == "ok" and result.results[0].id == 9
    model.invoke.assert_awaited_once()
    call = model.invoke.call_args.kwargs
    data = json.loads(call["messages"][1]["content"])
    assert data["original_task"] == "original task" and data["search_query"] == "subquestion"
    assert len(data["candidates"][0]["snippet"]) == 1000
    assert len(data["candidates"][0]["title"]) == 300
    assert "secret" not in call["messages"][1]["content"]
    assert call["tools"] == [] and call["temperature"] == 0 and call["timeout"] == 15
    assert call["reasoning"] == ReasoningConfig(mode="disabled")
    assert 4096 <= call["max_tokens"] <= 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "not json", '{"results":[]}',
    '{"results":[{"id":1,"relevance":"relevant","reason":"ok"}]}',
    '{"results":[{"id":0,"relevance":"relevant","reason":"ok"},{"id":0,"relevance":"rejected","reason":"no"}]}',
    '{"results":[{"id":true,"relevance":"relevant","reason":"ok"}]}',
    '{"results":[{"id":0,"relevance":"maybe","reason":"ok"}]}',
    '{"results":[{"id":0,"relevance":"relevant","reason":"ok","url":"https://evil.invalid"}]}',
    '{"results":[{"id":0,"relevance":"relevant","reason":""}]}',
    '{"results":[{"id":0,"relevance":"relevant","reason":"ok"}]} trailing text',
])
async def test_invalid_output_is_unavailable_not_rejection(content):
    model = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content=content)))
    result = await assess_candidates(model, "query", [(0, candidate(0))])
    assert result.status == "unavailable" and not result.results
    assert result.reason == "invalid_assessment_response"
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_cancels_assessment_without_retries():
    cancelled = asyncio.Event()

    async def slow(**kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    model = SimpleNamespace(invoke=AsyncMock(side_effect=slow))
    result = await assess_candidates(model, "query", [(0, candidate(0))], timeout_seconds=0.01)
    assert result.status == "unavailable" and result.reason == "assessment_timeout"
    assert cancelled.is_set() and model.invoke.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_and_provider_errors_are_redacted():
    model = SimpleNamespace(invoke=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await assess_candidates(model, "query", [(0, candidate(0))])
    model.invoke.side_effect = RuntimeError("secret-api-key")
    result = await assess_candidates(model, "query", [(0, candidate(0))])
    assert result.status == "unavailable" and "secret" not in result.reason


def test_semantic_order_and_structural_checks_are_independent_of_query():
    rows = [candidate(i) for i in range(6)]
    rows += [dict(rows[0]), dict(candidate(7), url="https://bing.com/search?q=x")]
    assessment = SearchAssessment(status="ok", results=[
        CandidateAssessment(id=5, relevance="relevant", reason="Most useful"),
        CandidateAssessment(id=1, relevance="relevant", reason="Useful"),
        CandidateAssessment(id=0, relevance="rejected", reason="Different entity"),
        *[CandidateAssessment(id=i, relevance="uncertain", reason="Need full text") for i in (2, 3, 4)],
    ])
    for query in ("Anything", "任意新领域", "tema desconocido"):
        selected, decisions = _select_search_candidates(query, rows, assessment=assessment)
        assert [row["url"] for row in selected] == [rows[i]["url"] for i in (5, 1, 2, 3)]
        assert decisions[4]["reasons"][-1] == "uncertain_limit"
        assert decisions[6]["reasons"] == ["duplicate_url"]
        assert decisions[7]["reasons"] == ["search_navigation"]
        assert "relevance" not in rows[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "invalid", "error", "resolution_error"])
async def test_assessment_unavailable_returns_only_provisional_leads_without_market_retry(monkeypatch, mode):
    calls = []

    async def request(session, method, url, **kwargs):
        calls.append(url)
        page = "".join(f'<li class="b_algo"><h2><a href="https://source.invalid/{i}">Title {i}</a></h2></li>' for i in range(4))
        return 200, {}, page.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    monkeypatch.delenv("FREE_SEARCH_DEBUG", raising=False)
    model = None if mode == "missing" else model_reply([])
    if mode == "error":
        model.invoke.side_effect = RuntimeError("private-key")
    context = None
    if mode == "resolution_error":
        def fail_resolution():
            raise ValueError("private-key")
        context = SimpleNamespace(agent=SimpleNamespace(get_llm=fail_resolution))
    result = await WebFreeSearchTool(enabled_engines=("bing",), model=model).invoke(
        {"query": "query"}, _tool_callback_context=context,
    )
    assert "Assessment status: unavailable" in result and "Content status: uncertain" in result
    assert "Content status: ok" not in result and "low_relevance" not in result and "private-key" not in result
    assert len(calls) == 1 and all(f"Title {index}" in result for index in range(4))
    assert "not relevance order" in result and "Recommended fetch targets" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["length", "max_tokens", "max_output_tokens"])
@pytest.mark.parametrize("content", [None, "", '{"results":[{"id":0,"relevance":"relevant","reason":"ok"}]}'])
async def test_output_limit_is_distinct_from_bad_json_and_never_retried(finish, content):
    model = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content=content, finish_reason=finish)))
    result = await assess_candidates(model, "query", [(0, candidate(0))])
    assert result.status == "unavailable" and result.reason == "assessment_output_truncated"
    assert result.results == [] and result.error_type == ""
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "base", "name", "mode", "effort"), [
    ("OpenAI", "https://openrouter.ai/api/v1", "deepseek/deepseek-v4.1-flash", "disabled", None),
    ("DeepSeek", "https://api.deepseek.com", "deepseek-v4-flash", "disabled", None),
    ("OpenAI", "https://api.openai.com/v1", "gpt-5", "enabled", "minimal"),
    ("Moonshot", "https://api.moonshot.cn/v1", "kimi-k3", "enabled", "low"),
])
async def test_assessment_uses_existing_provider_capability_without_mutating_model(provider, base, name, mode, effort):
    model = model_reply([{"id": 0, "relevance": "relevant", "reason": "Useful"}])
    model.model_client_config = ModelClientConfig(client_provider=provider, api_base=base, api_key="test-only")
    model.model_config = ModelRequestConfig(model=name, reasoning=ReasoningConfig(mode="enabled", effort="high"))
    before = model.model_config.model_dump()
    result = await assess_candidates(model, "query", [(0, candidate(0))])
    assert result.status == "ok"
    intent = model.invoke.call_args.kwargs["reasoning"]
    assert intent.mode == mode and intent.effort == effort
    plan = resolve_reasoning_plan(model.model_client_config, model.model_config, explicit_kwargs={"reasoning": intent})
    if "openrouter" in base:
        assert plan.extra_body == {"reasoning": {"effort": "none"}}
    assert model.model_config.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 20, 30])
async def test_output_budget_covers_whole_batch_but_remains_bounded(count):
    model = model_reply([{"id": i, "relevance": "uncertain", "reason": "Read source"} for i in range(count)])
    result = await assess_candidates(model, "query", [(i, candidate(i)) for i in range(count)])
    assert result.status == "ok"
    assert 4096 <= model.invoke.call_args.kwargs["max_tokens"] <= 8192


def test_unassessed_candidates_keep_later_sources_within_requested_limit():
    rows = [candidate(i) for i in range(10)]
    rows[2]["title"] = "Original publication"
    assessment = SearchAssessment(status="unavailable", reason="assessment_output_truncated")
    selected, decisions = _select_search_candidates("any topic", rows, max_results=8, assessment=assessment)
    assert [row["url"] for row in selected] == [row["url"] for row in rows[:8]]
    assert all(row["relevance"] == "uncertain" for row in selected)
    assert decisions[2]["selected"] and "uncertain_limit" not in decisions[2]["reasons"]
    assert decisions[8]["reasons"][-1] == "result_limit"


@pytest.mark.asyncio
async def test_real_dispatch_uses_active_model_and_invocation_task(monkeypatch):
    async def request(session, method, url, **kwargs):
        return 200, {}, b'<li class="b_algo"><h2><a href="https://source.invalid/a">A source</a></h2></li>', url, False

    monkeypatch.setattr(_http, "request", request)
    monkeypatch.delenv("FREE_SEARCH_DEBUG", raising=False)
    first = model_reply([{"id": 0, "relevance": "relevant", "reason": "Supports subquestion"}])
    second = model_reply([{"id": 0, "relevance": "uncertain", "reason": "Missing constraint"}])
    active = [first]
    search = WebFreeSearchTool(enabled_engines=("bing",))
    manager = AbilityManager()
    manager.add([search.card])
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda tool_id, **kwargs: search)
    parent = AgentCallbackContext(agent=SimpleNamespace(
        get_llm=lambda: active[0], agent_callback_manager=SimpleNamespace(execute=AsyncMock()), ability_manager=manager,
    ), inputs=InvokeInputs(query="Original task"))
    await WebResearchBudgetRail().before_invoke(parent)
    session = SimpleNamespace(session_id="assessment-dispatch")
    call = ToolCall(id="s", type="function", name="free_search", arguments=json.dumps({"query": "Subquestion"}))
    result = await manager.execute(parent, call, session=session)
    assert "Content status: ok" in result[0][0]
    active[0] = second
    result = await manager.execute(parent, call, session=session)
    assert "Content status: uncertain" in result[0][0]
    assert first.invoke.await_count == second.invoke.await_count == 1
    assert json.loads(first.invoke.call_args.kwargs["messages"][1]["content"])["original_task"] == "Original task"
    assert parent.extra[WEB_RESEARCH_BUDGET_KEY].searches == {"bing": 2}


@pytest.mark.asyncio
async def test_empty_batch_never_invokes_model():
    model = model_reply([])
    assert (await assess_candidates(model, "query", [])).status == "skipped"
    model.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_tool_calls_and_oversized_batches_are_not_executed():
    response = SimpleNamespace(content='{"results":[]}', tool_calls=[{"name": "fetch_webpage"}])
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))
    result = await assess_candidates(model, "query", [(0, candidate(0))])
    assert result.status == "unavailable" and result.reason == "invalid_assessment_response"
    result = await assess_candidates(model, "query", [(i, candidate(i)) for i in range(31)])
    assert result.status == "unavailable" and result.reason == "candidate_limit_exceeded"
    assert model.invoke.await_count == 1


@pytest.mark.asyncio
async def test_shared_tool_keeps_concurrent_models_and_original_tasks_separate(monkeypatch):
    async def request(session, method, url, **kwargs):
        return 200, {}, b'<li class="b_algo"><h2><a href="https://source.invalid/a">Document</a></h2></li>', url, False

    monkeypatch.setattr(_http, "request", request)
    monkeypatch.delenv("FREE_SEARCH_DEBUG", raising=False)
    entered = asyncio.Queue()
    release = asyncio.Event()
    payloads = {}

    async def evaluate(label, **kwargs):
        payloads[label] = json.loads(kwargs["messages"][1]["content"])
        await entered.put(label)
        await release.wait()
        return SimpleNamespace(content=json.dumps({"results": [{
            "id": 0, "relevance": label, "reason": label,
        }]}))

    first = SimpleNamespace(invoke=lambda **kwargs: evaluate("relevant", **kwargs))
    second = SimpleNamespace(invoke=lambda **kwargs: evaluate("uncertain", **kwargs))
    contexts = [SimpleNamespace(agent=SimpleNamespace(get_llm=lambda: first), extra={
        WEB_RESEARCH_BUDGET_KEY: WebResearchBudget(original_task="First task"),
    }), SimpleNamespace(agent=SimpleNamespace(get_llm=lambda: second), extra={
        WEB_RESEARCH_BUDGET_KEY: WebResearchBudget(original_task="Second task"),
    })]
    tool = WebFreeSearchTool(enabled_engines=("bing",))
    tasks = [asyncio.create_task(tool.invoke({"query": "Same subquestion"}, _tool_callback_context=ctx))
             for ctx in contexts]
    try:
        async with asyncio.timeout(5):
            assert {await entered.get(), await entered.get()} == {"relevant", "uncertain"}
            release.set()
            results = await asyncio.gather(*tasks)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert "Content status: ok" in results[0] and "Content status: uncertain" in results[1]
    assert payloads["relevant"]["original_task"] == "First task"
    assert payloads["uncertain"]["original_task"] == "Second task"
    assert all(ctx.extra[WEB_RESEARCH_BUDGET_KEY].searches == {"bing": 1} for ctx in contexts)


@pytest.mark.asyncio
async def test_assessment_cannot_publish_results_after_overall_deadline(monkeypatch):
    from openjiuwen.harness.tools.web import _budget

    clock = [0.0]
    monkeypatch.setattr(_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    request = AsyncMock(return_value=(200, {},
        b'<li class="b_algo"><h2><a href="https://source.invalid/a">Document</a></h2></li>',
        "https://bing.com/search", False))
    monkeypatch.setattr(_http, "request", request)
    monkeypatch.delenv("FREE_SEARCH_DEBUG", raising=False)

    async def evaluate(**kwargs):
        assert kwargs["timeout"] <= 1
        clock[0] = 2
        return SimpleNamespace(content=json.dumps({"results": [
            {"id": 0, "relevance": "relevant", "reason": "Useful"},
        ]}))

    model = SimpleNamespace(invoke=AsyncMock(side_effect=evaluate))
    budget = WebResearchBudget(timeout_seconds=1)
    context = SimpleNamespace(extra={WEB_RESEARCH_BUDGET_KEY: budget})
    result = await WebFreeSearchTool(enabled_engines=("bing",), model=model).invoke(
        {"query": "query"}, _tool_callback_context=context,
    )
    assert "Content status: budget_exhausted" in result and "source.invalid/a" not in result
    assert request.await_count == model.invoke.await_count == 1
