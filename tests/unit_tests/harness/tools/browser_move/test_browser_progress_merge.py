# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Progress and observation contracts across the LLM/JEV merge boundary."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import CORE_BROWSER_TOOL_NAMES
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    BrowserWorkingContextConfig,
    BrowserWorkingContextStore,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import (
    BrowserPageActionTool,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.semantic_state import SemanticStateTracker
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _make_capture_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_tools import _make_runtime


def test_switching_to_compact_observation_does_not_reset_failure_budget():
    tracker = SemanticStateTracker()
    metadata = {"url": "https://example.test/", "result_count": 3}
    complete = {**metadata, "page_content_hash": "a" * 64}
    tracker.observe(complete)
    tracker.observe(complete)
    compact = tracker.observe(metadata, observation_only=True)
    assert compact["progress"] == "inspection"
    assert compact["consecutive_no_progress"] == 1
    assert compact["changed_fields"] == []
    assert "page_content_hash" not in compact["semantic_state"]
    unchanged = tracker.observe(complete, observation_only=True)
    assert unchanged["progress"] == "inspection"
    assert unchanged["consecutive_no_progress"] == 1
    changed = tracker.observe({**complete, "page_content_hash": "b" * 64}, observation_only=True)
    assert changed["progress"] == "progress"
    assert changed["changed_fields"] == ["page_content_hash"]


def test_first_full_fingerprint_after_compact_capture_is_a_baseline():
    tracker = SemanticStateTracker()
    metadata = {"url": "https://example.test/", "result_count": 3}
    complete = {**metadata, "page_content_hash": "a" * 64}
    tracker.observe(metadata)
    tracker.observe(metadata)
    baseline = tracker.observe(complete, observation_only=True)
    assert baseline["progress"] == "inspection"
    assert baseline["changed_fields"] == []
    assert baseline["consecutive_no_progress"] == 1
    changed = tracker.observe({**complete, "page_content_hash": "b" * 64})
    assert changed["progress"] == "progress"
    assert tracker.observe(complete)["progress"] == "state_revisit"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["llm", "shadow", "hybrid"])
async def test_decision_repair_preserves_inspection_budget_without_full_ax(mode):
    runtime = _make_capture_runtime()
    runtime.decision_policy = SimpleNamespace(decision_config=SimpleNamespace(mode=mode))
    baseline = await runtime.capture_browser_state(action_group_id="initial")
    tracker = runtime._ensure_semantic_state_tracker()
    tracker.observe(baseline["semantic_state"], action_group_id="failed-1")
    tracker.observe(baseline["semantic_state"], action_group_id="failed-2")
    runtime.ensure_page_state().decision_snapshot = {"invalidated": "stale"}
    runtime.call_playwright_tool.reset_mock()
    runtime.capture_browser_metadata = AsyncMock(return_value=({
        "ok": True,
        "url": baseline["url"],
        "title": "Tenders",
        "semantic_state": {"result_count": 2, "first_result_text": "Pinned tender"},
    }, None))

    repaired = await runtime.capture_compact_browser_state(action_group_id="read-repair")

    assert repaired["capture_source"] == "decision_repair_rpc"
    assert repaired["semantic_progress"]["observation_only"] is True
    assert repaired["semantic_progress"]["progress"] == "inspection"
    assert repaired["semantic_progress"]["consecutive_no_progress"] == 2
    runtime.call_playwright_tool.assert_not_awaited()
    runtime.capture_browser_metadata.assert_awaited_once_with(include_decision=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_expired_task_cannot_start_a_fallback_capture(streaming):
    policy, llm, client, runtime, context, captured = setup_policy()
    messages = await messages_for(policy, context, captured)
    from dataclasses import replace

    token = messages[0].metadata["browser_policy_observation"]
    refresh = AsyncMock(return_value=messages)
    policy._observations[token] = replace(policy._observations[token], refresh_llm=refresh)
    policy._choose = AsyncMock(return_value=(None, {}, time.time() - 1))
    with pytest.raises(TimeoutError, match="deadline"):
        if streaming:
            async for _ in policy.stream(messages):
                pass
        else:
            await policy.invoke(messages)
    refresh.assert_not_awaited()
    llm.invoke.assert_not_awaited()
    llm.stream.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["read_text", "find"])
async def test_fixed_reader_executes_with_core_capabilities(op):
    runtime = _make_runtime()
    runtime._service = SimpleNamespace(allowed_tool_names=list(CORE_BROWSER_TOOL_NAMES))
    runtime.call_fixed_with_observation = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://example.test/",
            "text": "observed facts",
            "read_only": True,
        }
    )
    args = {"generation_id": runtime.generation_id, "op": op}
    if op == "find":
        args["query"] = "facts"
    result = await BrowserPageActionTool(runtime).invoke(args)
    assert result.success
    assert result.data["state_changed"] is False
    assert runtime.ensure_page_state().read_observation["text"] == "observed facts"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["llm", "shadow", "hybrid"])
async def test_post_action_reuse_preserves_full_llm_and_compact_jev_captures(mode):
    runtime = _make_runtime()
    runtime.decision_policy = SimpleNamespace(decision_config=SimpleNamespace(mode=mode))
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.call_playwright_tool = AsyncMock(return_value="- paragraph: Complete content")
    runtime.call_playwright_run_code_unsafe = AsyncMock()
    page = runtime.ensure_page_state()
    metadata = {"ok": True, "url": page.url, "title": "Page", "semantic_state": {"result_count": 3}}
    runtime._post_observation = {
        "at": time.monotonic(),
        "scope": (page.page_id, page.generation_id, page.interaction_revision, page.url),
        "metadata": metadata,
    }
    captured = await runtime.capture_browser_state(action_group_id="mutation", include_decision=True)
    assert captured["ok"]
    if mode == "hybrid":
        runtime.call_playwright_tool.assert_not_awaited()
        assert captured["dom"] == ""
        assert captured["capture_source"] == "post_action_rpc"
        assert "page_content_hash" not in captured["semantic_state"]
    else:
        runtime.call_playwright_tool.assert_awaited_once_with("browser_snapshot", {})
        assert "Complete content" in captured["dom"]
        assert captured["semantic_state"]["page_content_hash"]
    runtime.call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
async def test_only_an_actual_jev_handoff_refreshes_the_llm_window(streaming, accepted):
    policy, llm, client, runtime, context, captured = setup_policy()
    messages = await messages_for(policy, context, captured)
    observation = next(reversed(policy._observations.values()))
    token = messages[0].metadata["browser_policy_observation"]
    from dataclasses import replace

    refreshed = [UserMessage(content="full native AX for fallback")]
    refresh = AsyncMock(return_value=refreshed)
    policy._observations[token] = replace(observation, refresh_llm=refresh)
    if not accepted:
        client.evaluate.side_effect = RuntimeError("provider unavailable")
    if streaming:
        result = [chunk async for chunk in policy.stream(messages, tools=TOOLS)]
    else:
        result = await policy.invoke(messages, tools=TOOLS)
    if accepted:
        refresh.assert_not_awaited()
        llm.invoke.assert_not_awaited()
        llm.stream.assert_not_called()
    else:
        refresh.assert_awaited_once_with(messages)
        call = llm.stream.call_args if streaming else llm.invoke.call_args
        assert any("full native AX" in message.content for message in call.kwargs["messages"])
    assert result


@pytest.mark.asyncio
async def test_handoff_replaces_cached_projection_and_preserves_one_step_memory():
    provider = SimpleNamespace(
        capture_browser_state=AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.test/",
                "dom": "full native AX",
                "page_state": {},
            }
        )
    )
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    processor._cached_state = {"ok": True, "dom": ""}
    store = BrowserWorkingContextStore(BrowserWorkingContextConfig())
    original = store.render_and_consume_one_step(None)
    prefix, body, suffix = original.rsplit("\n", 2)
    payload = json.loads(body)
    payload["next_tool_result"] = [{"content": "keep for this invocation"}]
    memory = f"{prefix}\n{json.dumps(payload)}\n{suffix}"
    messages = [
        UserMessage(name="browser_working_context", content=memory),
        processor._build_state_message(processor._cached_state),
    ]
    refreshed = await processor._refresh_llm_context(None, messages, "group")
    assert "full native AX" in refreshed[-1].content
    assert "keep for this invocation" in refreshed[0].content
    assert "full native AX" in processor._cached_state_message.content
    provider.capture_browser_state.assert_awaited_once_with(
        action_group_id="group:llm",
        observation_only=True,
        include_decision=True,
        _allow_reconciliation=False,
    )
    await processor._refresh_llm_context(None, refreshed, "group")
    assert provider.capture_browser_state.await_count == 1
