#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for BrowserAgentRuntime response mapping and smoke-level wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

from openjiuwen.core.foundation.tool import McpServerConfig


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


def test_handle_request_uses_main_agent_error_output() -> None:
    runtime = _make_runtime()
    runtime.main_agent = object()

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_agent(agent, inputs):
        del agent, inputs
        return {"output": "The browser task failed.", "result_type": "error"}

    with patch.object(runtime, "ensure_started", fake_ensure_started), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.Runner.run_agent",
        fake_run_agent,
    ):
        result = _run(runtime.handle_request(query="Add carbonara ingredients to cart", session_id="session-1"))

    assert result["ok"] is False
    assert result["error"] == "The browser task failed."
    assert result["final"] == "The browser task failed."


def test_handle_request_returns_main_agent_answer() -> None:
    runtime = _make_runtime()
    runtime.main_agent = object()

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_agent(agent, inputs):
        del agent, inputs
        return {"output": "Submitted successfully.", "result_type": "answer"}

    with patch.object(runtime, "ensure_started", fake_ensure_started), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.Runner.run_agent",
        fake_run_agent,
    ):
        result = _run(runtime.handle_request(query="Submit onboarding form", session_id="session-2"))

    assert result["ok"] is True
    assert result["error"] is None
    assert result["final"] == "Submitted successfully."


def test_runtime_ensure_started_smoke_initializes_tools_once() -> None:
    runtime = _make_runtime()
    main_agent = object()
    register_calls: list[str] = []

    async def fake_service_ensure_started() -> None:
        return None

    def fake_register_runtime_tool(_tool_obj, *, tool_name: str) -> None:
        register_calls.append(tool_name)

    with patch.object(runtime.service, "ensure_started", fake_service_ensure_started), patch.object(
        BrowserAgentRuntime,
        "_register_runtime_tool",
        side_effect=fake_register_runtime_tool,
    ), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.build_main_agent",
        return_value=main_agent,
    ):
        _run(runtime.ensure_started())
        _run(runtime.ensure_started())

    assert runtime.main_agent is main_agent
    assert runtime.browser_tool is not None
    assert runtime.browser_custom_action_tool is not None
    assert runtime.browser_list_actions_tool is not None
    assert register_calls == [
        "browser_run_task",
        "browser_custom_action",
        "browser_list_custom_actions",
    ]


def test_runtime_custom_action_tools_smoke_list_and_run() -> None:
    runtime = _make_runtime()

    async def fake_service_ensure_started() -> None:
        return None

    with patch.object(runtime.service, "ensure_started", fake_service_ensure_started), patch.object(
        BrowserAgentRuntime,
        "_register_runtime_tool",
        return_value=None,
    ), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.build_main_agent",
        return_value=object(),
    ):
        _run(runtime.ensure_started())
        actions = _run(runtime.browser_list_actions_tool.invoke({}))
        echoed = _run(
            runtime.browser_custom_action_tool.invoke(
                {
                    "action": "echo",
                    "session_id": "session-1",
                    "request_id": "request-1",
                    "params": {"text": "hello", "source": "smoke"},
                }
            )
        )

    assert actions["ok"] is True
    assert "echo" in actions["actions"]
    assert "browser_drag_and_drop" in actions["details"]
    assert echoed["ok"] is True
    assert echoed["text"] == "hello"
    assert echoed["session_id"] == "session-1"
    assert echoed["request_id"] == "request-1"
    assert echoed["meta"] == {"source": "smoke"}


def test_runtime_handle_request_smoke_routes_browser_tool_with_context_ids() -> None:
    runtime = _make_runtime()
    main_agent = object()
    observed: dict[str, object] = {}

    async def fake_service_ensure_started() -> None:
        return None

    async def fake_run_task(
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, object]:
        observed["task"] = task
        observed["session_id"] = session_id
        observed["request_id"] = request_id
        observed["timeout_s"] = timeout_s
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "final": "browser-finished",
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": None,
            "attempt": 1,
            "failure_summary": None,
        }

    async def fake_run_agent(agent, inputs):
        assert agent is main_agent
        tool_result = await runtime.browser_tool.invoke({"task": "Open homepage"})
        return {
            "output": (
                f"{inputs['conversation_id']}|{inputs['request_id']}|"
                f"{tool_result['session_id']}|{tool_result['request_id']}"
            ),
            "result_type": "answer",
        }

    with patch.object(runtime.service, "ensure_started", fake_service_ensure_started), patch.object(
        runtime.service,
        "run_task",
        fake_run_task,
    ), patch.object(
        BrowserAgentRuntime,
        "_register_runtime_tool",
        return_value=None,
    ), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.build_main_agent",
        return_value=main_agent,
    ), patch(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.Runner.run_agent",
        fake_run_agent,
    ):
        result = _run(
            runtime.handle_request(
                query="Open the homepage",
                session_id="session-ctx",
                request_id="request-ctx",
            )
        )

    assert result["ok"] is True
    assert result["session_id"] == "session-ctx"
    assert result["request_id"] == "request-ctx"
    assert result["final"] == "session-ctx|request-ctx|session-ctx|request-ctx"
    assert observed == {
        "task": "Open homepage",
        "session_id": "session-ctx",
        "request_id": "request-ctx",
        "timeout_s": 180,
    }
