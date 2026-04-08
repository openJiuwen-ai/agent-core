#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for BrowserAgentRuntime kernel behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright-runtime",
        server_name="test-playwright-runtime",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )
    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


def _run(coro):
    return asyncio.run(coro)


def test_runtime_ensure_started_registers_bridge_tools_once() -> None:
    runtime = _make_runtime()
    fake_browser_agent = MagicMock()
    fake_browser_agent.ability_manager = MagicMock()
    runtime.service._browser_agent = fake_browser_agent
    register_calls: list[str] = []
    runtime.ensure_runtime_ready = AsyncMock()

    async def fake_service_ensure_started() -> None:
        return None

    def fake_register_runtime_tool(_tool_obj, *, tool_name: str) -> None:
        register_calls.append(tool_name)

    with patch.object(runtime.service, "ensure_started", fake_service_ensure_started), patch.object(
        BrowserAgentRuntime,
        "_register_runtime_tool",
        side_effect=fake_register_runtime_tool,
    ):
        _run(runtime.ensure_started())
        _run(runtime.ensure_started())

    assert runtime.browser_custom_action_tool is not None
    assert runtime.browser_list_actions_tool is not None
    assert register_calls == [
        "browser_custom_action",
        "browser_list_custom_actions",
    ]
    assert fake_browser_agent.ability_manager.add.call_count == 2


def test_run_browser_task_forwards_to_service() -> None:
    runtime = _make_runtime()
    runtime.ensure_started = AsyncMock()
    runtime.service.run_task = AsyncMock(
        return_value={
            "ok": True,
            "session_id": "session-1",
            "request_id": "request-1",
            "final": "done",
            "page": {},
            "screenshot": None,
            "error": None,
        }
    )

    result = _run(
        runtime.run_browser_task(
            task="Submit onboarding form",
            session_id="session-1",
            request_id="request-1",
            timeout_s=120,
        )
    )

    runtime.ensure_started.assert_called_once()
    runtime.service.run_task.assert_called_once_with(
        task="Submit onboarding form",
        session_id="session-1",
        request_id="request-1",
        timeout_s=120,
    )
    assert result["ok"] is True


def test_run_custom_action_uses_controller_with_bound_runtime() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = MagicMock()
    runtime._controller.bind_runtime = MagicMock()
    runtime._controller.bind_code_executor = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={"ok": True, "text": "hello"})

    result = _run(
        runtime.run_custom_action(
            action="echo",
            session_id="session-1",
            request_id="request-1",
            params={"text": "hello"},
        )
    )

    runtime.ensure_runtime_ready.assert_called_once()
    runtime._controller.bind_runtime.assert_called_once_with(runtime)
    runtime._controller.bind_code_executor.assert_called_once_with(runtime._code_executor)
    runtime._controller.run_action.assert_called_once_with(
        action="echo",
        session_id="session-1",
        request_id="request-1",
        text="hello",
    )
    assert result["ok"] is True


def test_list_actions_returns_controller_metadata() -> None:
    runtime = _make_runtime()
    runtime._controller.list_actions = MagicMock(return_value=["echo"])
    runtime._controller.describe_actions = MagicMock(return_value={"echo": {}})

    result = _run(runtime.list_actions())

    assert result == {"ok": True, "actions": ["echo"], "details": {"echo": {}}}


def test_runtime_health_reflects_service_state() -> None:
    runtime = _make_runtime()
    runtime.service.started = True
    runtime.service._connection_healthy = False
    runtime.service._last_heartbeat_ok = 12.5

    result = _run(runtime.runtime_health())

    assert result == {
        "ok": False,
        "started": True,
        "last_heartbeat_ok": 12.5,
        "provider": "openai",
        "api_base": "https://example.invalid/v1",
        "model_name": "test-model",
    }
