# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Review regressions: scheduling, coherent observations and final tool dispatch."""

import asyncio
import json
import time
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_browser_state_metadata_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import BrowserBatchInteractTool
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import TOOLS, messages_for, setup_policy
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_tools import _make_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

dom_page = shared_dom_page


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_shadow_does_not_wait_for_provider_and_bounds_background_work(streaming):
    policy, llm, client, runtime, context, captured = setup_policy("shadow")
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client.evaluate.side_effect = pending
    messages = await messages_for(policy, context, captured)
    try:
        if streaming:
            async def consume():
                return [chunk async for chunk in policy.stream(messages, tools=TOOLS)]
            result = await asyncio.wait_for(consume(), timeout=1)
            assert "".join(chunk.content for chunk in result) == "original stream"
        else:
            result = await asyncio.wait_for(policy.invoke(messages, tools=TOOLS), timeout=1)
            assert result.content == "original LLM answer"
        await asyncio.wait_for(started.wait(), timeout=1)
        result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
        assert result.metadata["browser_policy"]["reason"] == "shadow_busy"
        assert len(policy._shadow_tasks) == 1
        assert client.evaluate.await_count == 1
        assert not policy._guards
        runtime._call_playwright_run_code_unsafe.assert_not_awaited()
    finally:
        await policy.release_task_resources()
    assert cancelled.is_set()
    assert not policy._shadow_tasks
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_uses_one_runtime_snapshot_and_does_not_reprobe():
    policy, llm, client, runtime, context, captured = setup_policy()
    captured["dom"] = "OLD AX text from an earlier evaluation"
    captured["decision_observation"]["page_text"] = "Fresh DOM: 销量"
    metadata = await policy.publish_context(context, captured, refresh=True, observation_only=False)
    captured["decision_observation"]["controls"].clear()
    await policy.invoke([UserMessage(content="state", metadata=metadata)], tools=TOOLS)
    state = client.evaluate.call_args.args[0]["state"]
    assert state["page_text"] == "Fresh DOM: 销量"
    assert state["capture_id"] == "capture-a"
    assert state["candidate_count"] == 1
    runtime.probe_interactives.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "url", "generation"])
async def test_incoherent_observation_falls_back_before_provider_call(mutation):
    policy, llm, client, runtime, context, captured = setup_policy()
    snapshot = captured["decision_observation"]
    if mutation == "missing":
        captured.pop("decision_observation")
    elif mutation == "url":
        snapshot["url"] = "https://other.test/"
    else:
        snapshot["page"]["generation_id"] = "old-generation"
    metadata = await policy.publish_context(context, captured, refresh=True, observation_only=False)
    result = await policy.invoke([UserMessage(content="state", metadata=metadata)], tools=TOOLS)
    assert result.content == "original LLM answer"
    client.evaluate.assert_not_awaited()
    assert policy.should_observe(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_after_permission", [False, True])
async def test_ability_manager_dispatch_checks_target_after_permission(monkeypatch, changed_after_permission):
    policy, llm, client, runtime, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    inputs = SimpleNamespace(tool_name=call.name, tool_args=json.loads(call.arguments), tool_call=call)
    callback = SimpleNamespace(inputs=inputs)
    session = context.get_session_ref()
    policy.check_tool_call_binding(inputs, session)
    assert policy._guards  # The early rail cannot consume the final execution guard.
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()
    if changed_after_permission:
        runtime._call_playwright_run_code_unsafe.return_value = {"result": {"ok": False}}
    runtime.decision_policy = policy
    runtime.batch_interact = AsyncMock(return_value={"ok": True, "executed": True})
    tool = BrowserBatchInteractTool(runtime)
    manager = AbilityManager(owner_id="jev-final-dispatch")
    manager.add(tool.card)
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **kwargs: tool)
    result, message = await manager._execute_single_tool_call(call, session, callback_context=callback)
    assert result.success is not changed_after_permission
    runtime._call_playwright_run_code_unsafe.assert_awaited_once()
    assert not policy._guards
    if changed_after_permission:
        runtime.batch_interact.assert_not_awaited()
        resumed = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
        assert resumed.content == "original LLM answer"
        assert client.evaluate.await_count == 1
    else:
        assert runtime.batch_interact.call_args.kwargs["allow_stale_recovery"] is False


@pytest.mark.asyncio
async def test_tool_rejects_arguments_changed_after_rails():
    policy, llm, client, runtime, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    inputs = SimpleNamespace(tool_name=call.name, tool_args=call.arguments, tool_call=call)
    runtime.decision_policy = policy
    runtime.batch_interact = AsyncMock()
    arguments = json.loads(call.arguments)
    arguments["steps"][0]["target_id"] = "another-target"
    result = await BrowserBatchInteractTool(runtime).invoke(
        arguments, session=context.get_session_ref(), _tool_callback_context=SimpleNamespace(inputs=inputs),
    )
    assert result.success is False
    runtime.batch_interact.assert_not_awaited()
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_never_rebinds_a_stale_jev_target():
    policy, llm, client, fake, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    arguments = json.loads(result.tool_calls[0].arguments)
    runtime = _make_runtime()
    runtime._page_state = fake._ensure_page_state()
    runtime._page_state.generation += 1
    runtime._refresh_stale_batch_targets = AsyncMock()
    runtime.ensure_runtime_ready = AsyncMock()
    result = await runtime.batch_interact(**arguments, allow_stale_recovery=False)
    assert result["executed"] is False
    assert "generation" in result["error"].lower()
    runtime._refresh_stale_batch_targets.assert_not_awaited()
    runtime.ensure_runtime_ready.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_valid", [False, True])
async def test_runtime_registers_the_coherent_projection_in_its_existing_metadata_call(probe_valid):
    policy, llm, client, fake, context, captured = setup_policy()
    runtime = _make_runtime()
    runtime.decision_policy = policy
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock(return_value='- button "销量" [ref=e3]')
    source = fake._ensure_page_state()
    element = {"selector_hint": "#sales", "selector_hint_validated": True, "match_count": 1,
               "role": "button", "accessible_name": "销量", "text": "销量", "visible": True,
               "enabled": True, "actionable": True, "clickable": True,
               "decision_state": source.export_decision_targets()[0]["decision_state"]}
    runtime._call_playwright_run_code_unsafe = AsyncMock(return_value={
        "ok": True, "url": source.url, "title": source.title,
        "decision_probe": {"ok": True, "url": source.url if probe_valid else "https://other.test/",
                           "elements": [element], "decision_snapshot": source.decision_snapshot},
    })
    observed = await runtime.capture_browser_state(include_decision=True)
    assert bool(observed["decision_observation"]) is probe_valid
    if probe_valid:
        assert observed["decision_observation"]["capture_id"] == "capture-a"
        assert observed["decision_observation"]["controls"][0]["decision_state"]["node_guard"]
    assert "node_guard" not in json.dumps(observed["page_state"])
    runtime._call_playwright_tool.assert_awaited_once_with("browser_snapshot", {})
    runtime._call_playwright_run_code_unsafe.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_execution_guards_are_excluded_from_public_tool_results_and_recall():
    policy, llm, client, fake, context, captured = setup_policy()
    runtime = _make_runtime()
    runtime.decision_policy = policy
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = AsyncMock()
    source = fake._ensure_page_state()
    element = {"selector_hint": "#sales", "selector_hint_validated": True, "match_count": 1,
               "role": "button", "accessible_name": "销量", "visible": True,
               "enabled": True, "actionable": True, "clickable": True,
               "decision_state": source.export_decision_targets()[0]["decision_state"]}
    runtime._execute_probe_json = AsyncMock(return_value=({
        "ok": True, "url": source.url, "elements": [element], "decision_snapshot": source.decision_snapshot,
    }, None, 0))
    result = await runtime.probe_interactives()
    assert "node_guard" not in json.dumps(result)
    assert "decision_snapshot" not in json.dumps(result)
    assert runtime._ensure_page_state().export_decision_observation()["controls"][0]["decision_state"]["node_guard"]


@pytest.mark.asyncio
async def test_strict_runtime_keeps_click_semantics_instead_of_navigation_rewrite():
    policy, llm, client, fake, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    arguments = json.loads(result.tool_calls[0].arguments)
    runtime = _make_runtime()
    runtime._page_state = fake._ensure_page_state()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._resolve_batch_steps = AsyncMock(side_effect=ValueError("stop before mutation"))
    result = await BrowserAgentRuntime.batch_interact(runtime, **arguments, allow_stale_recovery=False)
    assert result["executed"] is False
    assert runtime._resolve_batch_steps.call_args.kwargs["allow_navigation_rewrite"] is False


@pytest.mark.asyncio
async def test_fallback_stream_deadline_does_not_cancel_its_consumer_while_yielded():
    policy, llm, client, runtime, context, captured = setup_policy(deadline=time.time() + 0.15)
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    closed = []

    async def streaming(**kwargs):
        try:
            yield AssistantMessageChunk(content="first")
            yield AssistantMessageChunk(content="second")
        finally:
            closed.append(True)

    llm.stream.side_effect = streaming
    stream = policy.stream(await messages_for(policy, context, captured), tools=TOOLS)
    try:
        assert (await anext(stream)).content == "first"
        await asyncio.sleep(0.2)  # The consumer owns this await, not the provider stream.
        with pytest.raises(TimeoutError, match="deadline"):
            await anext(stream)
    finally:
        await stream.aclose()
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("close_early", [False, True])
async def test_fallback_stream_preserves_provider_contextvar_ownership(close_early):
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    scope = ContextVar("jev_test_provider_scope", default="caller")
    closed = []

    async def streaming(**kwargs):
        token = scope.set("provider")
        try:
            yield AssistantMessageChunk(content="first")
            yield AssistantMessageChunk(content="second")
        finally:
            scope.reset(token)
            closed.append(True)

    llm.stream.side_effect = streaming
    stream = policy.stream(await messages_for(policy, context, captured), tools=TOOLS)
    try:
        assert (await anext(stream)).content == "first"
        if not close_early:
            assert [chunk.content async for chunk in stream] == ["second"]
    finally:
        await stream.aclose()
    assert closed == [True]
    assert scope.get() == "caller"


def test_combined_runtime_probe_captures_text_and_bounds_node_guards(dom_page):
    dom_page.set_content("<h1>当前商品</h1><form>" + "".join(
        f'<button type="button" id="b{i}">查看 {i}</button>' for i in range(40)
    ) + "</form>")
    script = build_browser_state_metadata_js(decision_probe=build_interactive_probe_js(
        max_items=3, decision_mode=True, viewport_only=False,
    ))
    result = dom_page.evaluate("""async code => {
      const page = {evaluate: (fn, arg) => fn(arg), context: () => ({pages: () => []}),
                    url: () => window.location.href, title: async () => document.title};
      return await eval('(' + code + ')')(page);
    }""", script)
    observed = result["decision_probe"]
    snapshot = observed["decision_snapshot"]
    assert observed["returned"] == 3
    assert observed["total_candidates"] == 40
    assert snapshot["url"] == result["url"]
    assert "当前商品" in snapshot["page_text"]
    assert snapshot["capture_id"] and snapshot["observed_at_ms"] > 0
    assert dom_page.evaluate("window.__openjiuwenDecisionNodes.next") == 3
    assert all(element["decision_state"]["node_guard"] for element in observed["elements"])
