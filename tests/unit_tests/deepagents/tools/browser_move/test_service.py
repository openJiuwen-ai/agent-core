#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for BrowserService guardrails, retries, and worker conversation behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.service import BrowserService

from openjiuwen.core.foundation.tool import McpServerConfig


def _make_service(*, retry_once: bool = False) -> BrowserService:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright",
        server_name="test-playwright",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )
    return BrowserService(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=retry_once),
    )


def _run(coro):
    return asyncio.run(coro)


def test_failure_summary_is_reused_then_cleared() -> None:
    service = _make_service()
    observed_tasks: list[str] = []
    responses = [
        {
            "ok": False,
            "final": "Opened dashboard and attempted submit.",
            "page": {"url": "https://example.com/form", "title": "Form"},
            "screenshot": "screenshots/form.png",
            "error": "submit button not found",
        },
        {
            "ok": True,
            "final": "Submitted successfully.",
            "page": {"url": "https://example.com/done", "title": "Done"},
            "screenshot": None,
            "error": None,
        },
        {
            "ok": True,
            "final": "Confirmed completion.",
            "page": {"url": "https://example.com/done", "title": "Done"},
            "screenshot": None,
            "error": None,
        },
    ]

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del session_id, request_id
        observed_tasks.append(task)
        return responses[len(observed_tasks) - 1]

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        first = _run(service.run_task(task="Submit onboarding form", session_id="session-1", request_id="req-1"))
        assert first["ok"] is False
        assert isinstance(first.get("failure_summary"), str)
        assert "submit button not found" in first["failure_summary"]
        assert "Previous failed attempt context:" not in observed_tasks[0]

        second = _run(service.run_task(task="Submit onboarding form", session_id="session-1", request_id="req-2"))
        assert second["ok"] is True
        assert second["failure_summary"] is None
        assert "Previous failed attempt context:" in observed_tasks[1]
        assert "submit button not found" in observed_tasks[1]

        third = _run(service.run_task(task="Submit onboarding form", session_id="session-1", request_id="req-3"))
        assert third["ok"] is True
        assert third["failure_summary"] is None
        assert "Previous failed attempt context:" not in observed_tasks[2]


def test_timeout_failure_generates_summary() -> None:
    service = _make_service()
    observed_tasks: list[str] = []
    calls = {"count": 0}

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del session_id, request_id
        observed_tasks.append(task)
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("simulated timeout")
        return {
            "ok": True,
            "final": "Recovered",
            "page": {"url": "https://example.com", "title": "Example"},
            "screenshot": None,
            "error": None,
        }

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        failed = _run(service.run_task(task="Check status", session_id="session-timeout", request_id="req-timeout"))
        assert failed["ok"] is False
        assert "task_timeout:" in str(failed["error"])
        assert "task_timeout:" in str(failed["failure_summary"])

        recovered = _run(service.run_task(task="Check status", session_id="session-timeout", request_id="req-retry"))
        assert recovered["ok"] is True
        assert recovered["failure_summary"] is None
        assert len(observed_tasks) == 2
        assert "Previous failed attempt context:" in observed_tasks[1]


def test_retryable_runtime_error_retries_once() -> None:
    service = _make_service(retry_once=True)
    observed_tasks: list[str] = []
    calls = {"count": 0}
    restart_calls = {"count": 0}

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del session_id, request_id
        observed_tasks.append(task)
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "ok": False,
                "final": "### Error\nError: page.goto: Frame has been detached.",
                "page": {"url": "https://www.lazada.sg", "title": ""},
                "screenshot": None,
                "error": "tool execution failed",
            }
        return {
            "ok": True,
            "final": "Navigation recovered and task completed.",
            "page": {"url": "https://www.lazada.sg", "title": "Lazada"},
            "screenshot": None,
            "error": None,
        }

    async def fake_restart() -> None:
        restart_calls["count"] += 1
        return None

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ), patch.object(service, "_restart", fake_restart):
        result = _run(service.run_task(task="Open Lazada homepage", session_id="session-retry", request_id="req-retry"))
        assert result["ok"] is True
        assert result["attempt"] == 2
        assert result["failure_summary"] is None
        assert len(observed_tasks) == 2
        assert "Previous failed attempt context:" in observed_tasks[1]
        assert restart_calls["count"] == 1


def test_max_iteration_failure_preserves_worker_output_without_progress_summary() -> None:
    service = _make_service()

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del task, session_id, request_id
        return {
            "ok": False,
            "final": "Max iterations reached without completion",
            "page": {"url": "https://www.lazada.sg", "title": "Lazada"},
            "screenshot": None,
            "error": "max_iterations_reached",
        }

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        result = _run(
            service.run_task(
                task="Add carbonara ingredients to cart",
                session_id="session-max-iter",
                request_id="req-max-iter",
            )
        )
        assert result["ok"] is False
        assert "Max iterations reached without completion" in result["final"]
        assert "Partial progress (recent tool steps):" not in result["final"]
        assert "Partial progress (recent tool steps):" not in str(result["failure_summary"])


def test_run_task_once_uses_fresh_worker_conversation_ids() -> None:
    service = _make_service()
    service.browser_agent = object()
    seen_conversation_ids: list[str] = []
    seen_request_ids: list[str] = []

    async def fake_run_agent(agent, inputs):
        del agent
        seen_conversation_ids.append(str(inputs["conversation_id"]))
        seen_request_ids.append(str(inputs["request_id"]))
        return {
            "output": (
                '{"ok": true, "final": "done", "page": {"url": "", "title": ""},'
                ' "screenshot": null, "error": null}'
            )
        }

    with patch("openjiuwen.deepagents.tools.browser_move.playwright_runtime.service.Runner.run_agent", fake_run_agent):
        first = _run(service.run_task_once(task="Open page", session_id="session-1", request_id="req-1"))
        second = _run(service.run_task_once(task="Open page", session_id="session-1", request_id="req-1"))

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(seen_conversation_ids) == 2
    assert seen_conversation_ids[0] != seen_conversation_ids[1]
    assert all(isinstance(value, str) and value for value in seen_conversation_ids)
    assert seen_request_ids == ["req-1", "req-1"]
