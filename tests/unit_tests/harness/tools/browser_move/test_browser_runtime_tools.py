#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserCancelTool,
    BrowserCustomActionTool,
    BrowserListActionsTool,
    BrowserRunTaskTool,
    build_browser_runtime_tools,
)
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.base_tool import ToolOutput


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test",
        server_name="test",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )
    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


# ---------------------------------------------------------------------------
# build_browser_runtime_tools
# ---------------------------------------------------------------------------


def test_build_browser_runtime_tools_returns_four_tools() -> None:
    tools = build_browser_runtime_tools(_make_runtime())
    assert len(tools) == 4


def test_each_tool_is_tool_subclass() -> None:
    for t in build_browser_runtime_tools(_make_runtime()):
        assert isinstance(t, Tool), f"{type(t)} is not a Tool subclass"


def test_each_tool_has_a_card() -> None:
    for t in build_browser_runtime_tools(_make_runtime()):
        assert isinstance(t.card, ToolCard), f"{t} missing ToolCard"


def test_tool_names() -> None:
    run_task, cancel, custom_action, list_actions = build_browser_runtime_tools(_make_runtime())
    assert run_task.card.name == "browser_run_task"
    assert cancel.card.name == "browser_cancel_run"
    assert custom_action.card.name == "browser_custom_action"
    assert list_actions.card.name == "browser_list_custom_actions"


def test_tool_classes() -> None:
    run_task, cancel, custom_action, list_actions = build_browser_runtime_tools(_make_runtime())
    assert isinstance(run_task, BrowserRunTaskTool)
    assert isinstance(cancel, BrowserCancelTool)
    assert isinstance(custom_action, BrowserCustomActionTool)
    assert isinstance(list_actions, BrowserListActionsTool)


def test_language_en_uses_english_descriptions() -> None:
    tools = build_browser_runtime_tools(_make_runtime(), language="en")
    for t in tools:
        assert t.card.description, "description must not be empty"
        # English descriptions use Latin characters
        assert any(c.isascii() and c.isalpha() for c in t.card.description)


def test_separate_runtimes_produce_independent_instances() -> None:
    tools_a = build_browser_runtime_tools(_make_runtime())
    tools_b = build_browser_runtime_tools(_make_runtime())
    for a, b in zip(tools_a, tools_b):
        assert a is not b


def test_tool_ids_are_non_empty() -> None:
    for t in build_browser_runtime_tools(_make_runtime()):
        assert t.card.id, f"{t.card.name} has empty id"


# ---------------------------------------------------------------------------
# BrowserRunTaskTool
# ---------------------------------------------------------------------------


def test_run_task_returns_tool_output() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(return_value={
        "ok": True, "session_id": "s1", "final": "done",
        "page": {}, "screenshot": None, "error": None,
    })
    t = BrowserRunTaskTool(runtime)
    result = _run(t.invoke({"task": "open google", "session_id": "s1"}))
    assert isinstance(result, ToolOutput)


def test_run_task_success_maps_to_tool_output_success() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(return_value={
        "ok": True, "session_id": "s1", "final": "done",
        "page": {}, "screenshot": None, "error": None,
    })
    t = BrowserRunTaskTool(runtime)
    result = _run(t.invoke({"task": "nav", "session_id": "s1"}))
    assert result.success is True
    assert result.data["ok"] is True


def test_run_task_calls_ensure_started() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(return_value={
        "ok": True, "session_id": "s1", "final": "done",
        "page": {}, "screenshot": None, "error": None,
    })
    t = BrowserRunTaskTool(runtime)
    _run(t.invoke({"task": "nav", "session_id": "s1"}))
    runtime.ensure_started.assert_called_once()


def test_run_task_strips_base64_screenshot() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(return_value={
        "ok": True, "session_id": "s1", "final": "done",
        "page": {}, "screenshot": "data:image/png;base64,AAAA==", "error": None,
    })
    t = BrowserRunTaskTool(runtime)
    result = _run(t.invoke({"task": "screenshot task", "session_id": "s1"}))
    assert result.data["screenshot"] == "[screenshot saved]"


def test_run_task_preserves_non_data_screenshot() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(return_value={
        "ok": True, "session_id": "s1", "final": "done",
        "page": {}, "screenshot": "/tmp/screen.png", "error": None,
    })
    t = BrowserRunTaskTool(runtime)
    result = _run(t.invoke({"task": "screenshot task", "session_id": "s1"}))
    assert result.data["screenshot"] == "/tmp/screen.png"


def test_run_task_forwards_session_and_request_ids() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    captured: dict = {}

    async def fake_run_task(*, task, session_id=None, request_id=None, timeout_s=None):
        captured.update(task=task, session_id=session_id, request_id=request_id, timeout_s=timeout_s)
        return {"ok": True, "session_id": session_id, "final": "", "page": {}, "screenshot": None, "error": None}

    runtime._service.run_task = fake_run_task
    t = BrowserRunTaskTool(runtime)
    _run(t.invoke({"task": "nav", "session_id": "s-abc", "request_id": "r-xyz", "timeout_s": 60}))
    assert captured == {"task": "nav", "session_id": "s-abc", "request_id": "r-xyz", "timeout_s": 60}


def test_run_task_returns_failure_on_exception() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._service.run_task = AsyncMock(side_effect=RuntimeError("boom"))
    t = BrowserRunTaskTool(runtime)
    result = _run(t.invoke({"task": "nav"}))
    assert result.success is False
    assert "boom" in result.error


# ---------------------------------------------------------------------------
# BrowserCancelTool
# ---------------------------------------------------------------------------


def test_cancel_tool_returns_tool_output() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": None, "error": None})
    t = BrowserCancelTool(runtime)
    result = _run(t.invoke({"session_id": "s1"}))
    assert isinstance(result, ToolOutput)


def test_cancel_tool_calls_cancel_run() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": None, "error": None})
    t = BrowserCancelTool(runtime)
    _run(t.invoke({"session_id": "s1"}))
    runtime.ensure_started.assert_called_once()
    runtime.cancel_run.assert_called_once_with(session_id="s1", request_id=None)


def test_cancel_tool_passes_non_empty_request_id() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": "r1", "error": None})
    t = BrowserCancelTool(runtime)
    _run(t.invoke({"session_id": "s1", "request_id": "r1"}))
    runtime.cancel_run.assert_called_once_with(session_id="s1", request_id="r1")


def test_cancel_tool_converts_empty_request_id_to_none() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": None, "error": None})
    t = BrowserCancelTool(runtime)
    _run(t.invoke({"session_id": "s1", "request_id": ""}))
    runtime.cancel_run.assert_called_once_with(session_id="s1", request_id=None)


# ---------------------------------------------------------------------------
# BrowserListActionsTool
# ---------------------------------------------------------------------------


def test_list_actions_returns_tool_output() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._controller.list_actions = MagicMock(return_value=["echo"])
    runtime._controller.describe_actions = MagicMock(return_value={"echo": {}})
    t = BrowserListActionsTool(runtime)
    result = _run(t.invoke({}))
    assert isinstance(result, ToolOutput)
    assert result.success is True
    assert result.data["actions"] == ["echo"]


def test_list_actions_calls_ensure_started() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._controller.list_actions = MagicMock(return_value=[])
    runtime._controller.describe_actions = MagicMock(return_value={})
    t = BrowserListActionsTool(runtime)
    _run(t.invoke({}))
    runtime.ensure_started.assert_called_once()


# ---------------------------------------------------------------------------
# BrowserCustomActionTool
# ---------------------------------------------------------------------------


def test_custom_action_returns_tool_output() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={"ok": True})
    t = BrowserCustomActionTool(runtime)
    result = _run(t.invoke({"action": "echo", "session_id": "s1"}))
    assert isinstance(result, ToolOutput)
    assert result.success is True


def test_custom_action_calls_ensure_started_and_controller() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={"ok": True})
    t = BrowserCustomActionTool(runtime)
    _run(t.invoke({"action": "echo", "session_id": "s1"}))
    runtime.ensure_started.assert_called_once()
    runtime._controller.bind_runtime.assert_called_once_with(runtime)
    runtime._controller.run_action.assert_called_once()


def test_custom_action_rebinds_code_executor_when_set() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._code_executor = MagicMock()
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={"ok": True})
    t = BrowserCustomActionTool(runtime)
    _run(t.invoke({"action": "echo", "session_id": "s1"}))
    runtime._controller.bind_code_executor.assert_called_once_with(runtime._code_executor)


def test_custom_action_skips_bind_when_executor_is_none() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._code_executor = None
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={"ok": True})
    t = BrowserCustomActionTool(runtime)
    _run(t.invoke({"action": "echo", "session_id": "s1"}))
    runtime._controller.bind_code_executor.assert_not_called()


def test_custom_action_passes_params_as_kwargs() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    captured_kwargs: dict = {}

    async def fake_run_action(action, session_id="", request_id="", **kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True}

    runtime._controller.run_action = fake_run_action
    t = BrowserCustomActionTool(runtime)
    _run(t.invoke({"action": "drag", "session_id": "s1", "params": {"element_source": "#a", "element_target": "#b"}}))
    assert captured_kwargs == {"element_source": "#a", "element_target": "#b"}
