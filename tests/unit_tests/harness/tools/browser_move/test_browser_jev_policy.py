# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline boundary tests: policy decisions cannot bypass the existing executor."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu, goal_values
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable, JevClient, validate_choice
from openjiuwen.harness.tools.browser_move.decision.policy_model import CONTEXT_KEY, BrowserPolicyModel
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail


def answer(choice="a1", *, group="CLICK", flat=False, **changes):
    probabilities = {"HANDOFF": 0.01, "FINISH": 0.01, "a1": 0.98}
    if choice != "a1":
        probabilities = {key: 0.98 if key == choice else 0.01 for key in probabilities}
    response = {"model": "jev-1.13.0", "answers": {"action": {
        "type": "choice", "choice": choice, "probabilities": probabilities, "confidence": 0.95, **changes,
    }}, "usage": {"input_tokens": 30, "output_tokens": 4}}
    if not flat:
        action = response["answers"]["action"]
        action["probabilities"][group] = action["probabilities"].pop("a1")
        if choice == "a1":
            action["choice"] = group
        response["answers"]["target_" + group] = {
            "type": "choice", "choice": "a1", "probabilities": {"a1": 1.0}, "confidence": 1.0,
        }
    return response


def grouped_answer(payload, group, key=None, *, confidence=0.96):
    """A provider fixture obeying the actual multi-head wire request."""
    answers = {}
    for name, question in payload["questions"].items():
        criteria = question["criteria"]
        choice = group if name == "action" else key if name == "target_" + group else None
        choice = choice or next(iter(criteria))
        answers[name] = {"type": "choice", "choice": choice, "confidence": confidence,
                         "probabilities": {k: 1.0 if k == choice else 0.0 for k in criteria}}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 30, "output_tokens": 4}}


def choose_operation(client, group):
    client.evaluate.side_effect = lambda payload, **kwargs: grouped_answer(payload, group)


def request_payload(model="jev-1.13.0"):
    return {"model": model, "state": {"goal": "Click Sales"}, "questions": {"action": {
        "type": "choice", "instructions": "Choose the next action.",
        "criteria": {"a1": "Click Sales", "HANDOFF": "Delegate", "FINISH": "Answer"},
    }}}


def setup_policy(mode="hybrid", *, goal="点击销量排序", deadline=None, session_id="session-a"):
    fallback = SimpleNamespace(model_config=None, model_client_config=None, _client=None,
                               invoke=AsyncMock(return_value=AssistantMessage(content="original LLM answer")))

    async def stream(**kwargs):
        yield AssistantMessageChunk(content="original ")
        yield AssistantMessageChunk(content="stream")

    fallback.stream = MagicMock(side_effect=stream)
    page = BrowserPageState(page_id="page-a")
    page.register_interactives({"url": "https://example.test/", "title": "Search", "elements": [{
        "selector_hint": "#sales", "selector_hint_validated": True, "match_count": 1,
        "role": "button", "accessible_name": "销量", "text": "销量", "visible": True,
        "enabled": True, "actionable": True, "clickable": True,
        "decision_state": {"tag": "button", "node_guard": {"document": "doc-a", "node": 1}},
    }]})
    runtime = SimpleNamespace(
        _ensure_page_state=lambda: page,
        probe_interactives=AsyncMock(return_value={"ok": True, "url": page.url}),
        _call_playwright_run_code_unsafe=AsyncMock(return_value={"result": {"ok": True}}),
        _unwrap_mcp_text_result=lambda value: value,
    )
    phase = {"goal": goal, "task": goal, "task_id": "task-a", "deadline_started_at": 1,
             "deadline_at": deadline or time.time() + 60, "recent_actions": []}
    session = SimpleNamespace(get_session_id=lambda: session_id, get_state=lambda _: phase)
    context = SimpleNamespace(get_session_ref=lambda: session)
    client = SimpleNamespace(evaluate=AsyncMock(return_value=answer()), aclose=AsyncMock())
    policy = BrowserPolicyModel(fallback, BrowserDecisionConfig(mode=mode), runtime, client=client)
    page.decision_snapshot = {"capture_id": "capture-a", "url": page.url, "title": page.title,
                              "page_text": "button 销量", "visibility": "visible", "observed_at_ms": 1}
    captured = {"ok": True, "url": page.url, "dom": "button 销量", "page_state": page.export()}
    captured["decision_observation"] = page.export_decision_observation()
    return policy, fallback, client, runtime, context, captured


async def messages_for(policy, context, captured, **kwargs):
    captured = {**captured, "decision_observation": policy.runtime._ensure_page_state().export_decision_observation()}
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import observe_runtime

    await observe_runtime(policy.runtime, context.get_session_ref(), captured)
    metadata = await policy.publish_context(context, captured, refresh=True, observation_only=False)
    return [UserMessage(content="current state", metadata=metadata)]


TOOLS = [{"name": "browser_batch_interact"}]


@pytest.mark.asyncio
async def test_hybrid_compiles_one_call_then_runtime_validates_and_consumes_it():
    policy, llm, client, runtime, context, captured = setup_policy()
    messages = await messages_for(policy, context, captured)
    result = await policy.invoke(messages, tools=TOOLS)
    call = result.tool_calls[0]
    assert call.name == "browser_batch_interact"
    assert json.loads(call.arguments)["steps"][0]["op"] == "click"
    assert result.response_model == "jev-1.13.0"
    assert result.usage_metadata.input_tokens == 30
    llm.invoke.assert_not_awaited()
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()
    assert "node_guard" not in json.dumps(client.evaluate.call_args.args[0])
    inputs = SimpleNamespace(tool_name=call.name, tool_args=call.arguments, tool_call=call)
    await policy.validate_tool_call(inputs, context.get_session_ref())
    runtime._call_playwright_run_code_unsafe.assert_awaited_once()
    with pytest.raises(ValueError, match="consumed"):
        await policy.validate_tool_call(inputs, context.get_session_ref())


@pytest.mark.asyncio
async def test_truncated_candidate_coverage_is_visible_to_the_decider():
    policy, llm, client, runtime, context, captured = setup_policy()
    runtime._ensure_page_state().decision_omitted = 19
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert client.evaluate.call_args.args[0]["state"]["omitted_count"] == 19


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [DecisionUnavailable("jev_http_402"), DecisionUnavailable("jev_http_429"),
                                    TimeoutError(), ValueError("malformed"), RuntimeError("provider secret body")])
async def test_failures_fall_back_and_do_not_retry_jev_in_the_same_state(failure):
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = failure
    messages = await messages_for(policy, context, captured)
    result = await policy.invoke(messages, tools=TOOLS)
    assert result.content == "original LLM answer"
    assert "secret body" not in str(result.metadata)
    assert CONTEXT_KEY not in llm.invoke.call_args.kwargs["messages"][0].metadata
    messages = await messages_for(policy, context, captured)
    await policy.invoke(messages, tools=TOOLS)
    assert client.evaluate.await_count == 1
    runtime.probe_interactives.assert_not_awaited()
    assert policy.should_observe(context)  # Only this state is handed back, never the whole task.
    assert llm.invoke.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [answer("FINISH"), answer("HANDOFF"), answer(confidence=0.1),
                                     answer(choice="invented"), {"model": "jev-1.13.0", "answers": {}}])
async def test_finish_handoff_and_invalid_choices_use_original_completion(response):
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.return_value = response
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.content == "original LLM answer"
    assert not result.tool_calls
    assert not policy._guards
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_and_unavailable_tools_never_compile_an_action():
    policy, llm, client, runtime, context, captured = setup_policy("shadow")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.metadata["browser_policy"]["reason"] == "shadow_scheduled"
    await asyncio.gather(*policy._shadow_tasks)
    assert not policy._guards
    await policy.invoke(await messages_for(policy, context, captured), tools=[])
    await asyncio.gather(*policy._shadow_tasks)
    assert client.evaluate.await_count == 1
    assert llm.invoke.await_count == 2


@pytest.mark.asyncio
async def test_stream_preserves_jev_tool_call_and_llm_streaming():
    policy, llm, client, runtime, context, captured = setup_policy()
    chunks = [item async for item in policy.stream(await messages_for(policy, context, captured), tools=TOOLS)]
    assert len(chunks) == 1 and chunks[0].tool_calls[0].id.startswith("jev_")
    assert chunks[0].usage_metadata.input_tokens == 30
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    chunks = [item async for item in policy.stream(await messages_for(policy, context, captured), tools=TOOLS)]
    assert [chunk.content for chunk in chunks] == ["original ", "stream"]
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_llm_fallback():
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    llm.invoke.assert_not_awaited()
    assert not policy._guards


@pytest.mark.asyncio
async def test_observation_tokens_are_one_use_and_cannot_cross_instances():
    policy, llm, client, runtime, context, captured = setup_policy()
    other, other_llm, other_client, *_ = setup_policy(session_id="session-b")
    messages = await messages_for(policy, context, captured)
    await other.invoke(messages, tools=TOOLS)
    other_client.evaluate.assert_not_awaited()
    other_llm.invoke.assert_awaited_once()
    await policy.invoke(messages, tools=TOOLS)
    await policy.invoke(messages, tools=TOOLS)
    assert client.evaluate.await_count == 1
    llm.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["page", "arguments", "node", "session"])
async def test_executor_rejects_changed_state_without_dispatching_an_action(mutation):
    policy, llm, client, runtime, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    inputs = SimpleNamespace(tool_name=call.name, tool_args=call.arguments, tool_call=call)
    session = context.get_session_ref()
    if mutation == "page":
        runtime._ensure_page_state().generation += 1
    elif mutation == "arguments":
        inputs.tool_args = {"steps": [{"op": "click", "target_id": "attacker"}]}
    elif mutation == "node":
        runtime._call_playwright_run_code_unsafe.return_value = {"result": {"ok": False}}
    else:
        session = SimpleNamespace(get_session_id=lambda: "other")
    with pytest.raises(ValueError, match="original LLM"):
        await policy.validate_tool_call(inputs, session)


@pytest.mark.asyncio
async def test_previous_error_does_not_disable_a_still_legal_decision():
    policy, llm, client, runtime, context, captured = setup_policy()
    messages = await messages_for(policy, context, captured)
    messages.append(ToolMessage(content='{"ok":false}', tool_call_id="jev_previous"))
    result = await policy.invoke(messages, tools=TOOLS)
    assert result.tool_calls
    client.evaluate.assert_awaited_once()
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_preserves_browser_and_new_task_can_use_jev_after_failure():
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    await policy.release_task_resources()
    client.aclose.assert_awaited_once()
    assert not policy._observations and not policy._guards
    phase = context.get_session_ref().get_state(None)
    phase["deadline_started_at"] = 2
    client.evaluate.side_effect = None
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.tool_calls


def test_literal_values_and_missing_values_do_not_invent_form_content():
    assert "九问 浏览器" in goal_values('搜索“九问 浏览器”，停止在结果页')
    assert goal_values("填写我的姓名和地址") == []
    control = {"target_id": "t1", "name": "姓名", "role": "textbox", "enabled": True, "actionable": True,
               "decision_state": {"tag": "input", "node_guard": {"node": 1}}}
    assert not build_menu([control], "填写我的姓名", limit=30).steps
    menu = build_menu([control], '姓名填写“张 三”', limit=30)
    assert list(menu.steps.values()) == [{"target_id": "t1", "op": "fill", "value": "张 三"}]
    control["decision_state"]["sensitive"] = True
    assert not build_menu([control], '填写“secret”', limit=30).steps


@pytest.mark.parametrize("args", [
    {"target": "#book", "element": "预订"}, {"steps": [{"op": "click", "target_id": "t1"}]},
])
def test_click_after_extraction_is_a_mutation_not_another_extraction(args):
    name = "browser_batch_interact" if "steps" in args else "browser_click"
    assert BrowserRuntimeRail._classify_tool_phase(name, args, {"current_phase": "extraction"}) == "form"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
async def test_http_failures_are_safe_and_retries_bounded(monkeypatch, status):
    monkeypatch.setenv("TEST_JEV_KEY", "not-a-real-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="raw secret provider body", headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = JevClient(BrowserDecisionConfig(api_key_env="TEST_JEV_KEY"), client=http)
        with pytest.raises(DecisionUnavailable, match=f"jev_http_{status}") as error:
            await client.evaluate(request_payload(), deadline_at=time.time() + 10)
        assert "secret" not in str(error.value)
    assert len(calls) == (2 if status in {429, 500} else 1)
    assert all(str(call.url) == "https://api.typesafe.ai/v1/systemone" for call in calls)


@pytest.mark.asyncio
async def test_transport_timeout_cancellation_missing_key_and_invalid_response(monkeypatch):
    monkeypatch.delenv("TEST_JEV_KEY", raising=False)
    config = BrowserDecisionConfig(api_key_env="TEST_JEV_KEY", request_timeout_ms=100)
    client = JevClient(config)
    with pytest.raises(DecisionUnavailable, match="missing_jev_key"):
        await client.evaluate(request_payload(), deadline_at=time.time() + 10)
    monkeypatch.setenv("TEST_JEV_KEY", "test")

    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json=answer(flat=True))

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        client = JevClient(config, client=http)
        with pytest.raises(DecisionUnavailable, match="jev_timeout"):
            await client.evaluate(request_payload(), deadline_at=time.time() + 10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))) as http:
        with pytest.raises(DecisionUnavailable, match="invalid_jev_response"):
            await JevClient(config, client=http).evaluate(request_payload(), deadline_at=time.time() + 10)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, None])
def test_invalid_probabilities_are_not_actions(value):
    with pytest.raises(DecisionUnavailable):
        validate_choice({"type": "choice", "choice": "a", "confidence": value,
                         "probabilities": {"a": 0.9, "b": 0.1}}, {"a": "A", "b": "B"}, 0.5)


@pytest.mark.asyncio
async def test_empty_page_uses_llm_navigation_then_allows_jev():
    policy, llm, client, runtime, context, captured = setup_policy()
    page = runtime._ensure_page_state()
    target_ids = page._decision_target_ids
    page._decision_target_ids = []
    assert not (await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)).tool_calls
    page._decision_target_ids = target_ids
    assert (await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)).tool_calls
    client.evaluate.assert_awaited_once()
    llm.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_expired_shared_deadline_does_not_start_fallback(streaming):
    policy, llm, client, runtime, context, captured = setup_policy(deadline=time.time() - 1)
    messages = await messages_for(policy, context, captured)
    with pytest.raises(TimeoutError, match="deadline"):
        if streaming:
            _ = [chunk async for chunk in policy.stream(messages, tools=TOOLS)]
        else:
            await policy.invoke(messages, tools=TOOLS)
    client.evaluate.assert_not_awaited()
    llm.invoke.assert_not_awaited()
    llm.stream.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_stream_closes_on_cancellation():
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    closed = []

    async def streaming(**kwargs):
        try:
            yield AssistantMessageChunk(content="first")
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    llm.stream.side_effect = streaming
    stream = policy.stream(await messages_for(policy, context, captured), tools=TOOLS)
    assert (await anext(stream)).content == "first"
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed == [True]


@pytest.mark.asyncio
async def test_request_success_uses_native_protocol_and_pinned_model(monkeypatch):
    monkeypatch.setenv("TEST_JEV_KEY", "synthetic-key")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert json.loads(request.content)["model"] == "jev-1.13.0"
        return httpx.Response(200, json=answer(flat=True))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = JevClient(BrowserDecisionConfig(api_key_env="TEST_JEV_KEY"), client=http)
        result = await client.evaluate(request_payload(), deadline_at=time.time() + 5)
        assert result == answer(flat=True)
        client.config = BrowserDecisionConfig(model="jev-1.12.0", api_key_env="TEST_JEV_KEY")
        with pytest.raises(DecisionUnavailable, match="model_mismatch"):
            await client.evaluate(request_payload(), deadline_at=time.time() + 5)
