#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for BrowserService guardrails, retries, and worker conversation behavior."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.profiles import BrowserProfile
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService

from openjiuwen.core.foundation.tool import McpServerConfig


def _make_service(*, retry_once: bool = False, runtime_cwd: str | None = None) -> BrowserService:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright",
        server_name="test-playwright",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": runtime_cwd or str(Path.cwd())},
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
    call_count = {"count": 0}

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del task, session_id, request_id
        call_count["count"] += 1
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
        assert call_count["count"] == 1


def test_max_iteration_resume_requires_opt_in_guardrail() -> None:
    service = _make_service()
    service.guardrails.resume_on_max_iterations = True
    observed_tasks: list[str] = []
    call_count = {"count": 0}

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del session_id, request_id
        observed_tasks.append(task)
        call_count["count"] += 1
        if call_count["count"] == 1:
            return {
                "ok": False,
                "final": "Max iterations reached without completion",
                "page": {"url": "https://www.baidu.com", "title": "百度一下，你就知道"},
                "screenshot": None,
                "error": "max_iterations_reached",
            }
        return {
            "ok": True,
            "final": "Completed after continuation.",
            "page": {"url": "https://www.baidu.com", "title": "百度一下，你就知道"},
            "screenshot": None,
            "error": None,
        }

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        result = _run(
            service.run_task(
                task="Open Baidu homepage",
                session_id="session-max-iter-resume",
                request_id="req-max-iter-resume",
            )
        )

    assert result["ok"] is True
    assert result["attempt"] == 2
    assert call_count["count"] == 2
    assert len(observed_tasks) == 2
    assert "Continuation context:" in observed_tasks[1]




def test_max_iteration_failure_includes_observed_tool_progress() -> None:
    service = _make_service()
    service._update_progress_from_tool_observation(
        session_id="session-progress",
        request_id="req-progress-1",
        tool_name="browser_click",
        tool_result={
            "message": "Clicked Add to cart",
            "page": {"url": "https://example.com/cart", "title": "Cart"},
        },
    )

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del task, session_id, request_id
        return {
            "ok": False,
            "final": "Max iterations reached without completion",
            "page": {"url": "https://example.com/cart", "title": "Cart"},
            "screenshot": None,
            "error": "max_iterations_reached",
        }

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        result = _run(
            service.run_task(
                task="Add item to cart",
                session_id="session-progress",
                request_id="req-progress-1",
            )
        )

    assert result["ok"] is False
    assert "Known progress for continuation:" in str(result["failure_summary"])
    assert "Clicked Add to cart" in str(result["failure_summary"])
    assert result["progress_state"]["recent_tool_steps"][-1].startswith("browser_click:")


def test_structured_progress_is_reused_on_next_invocation() -> None:
    service = _make_service()
    observed_tasks: list[str] = []
    responses = [
        {
            "ok": False,
            "final": "Reached review page but coupon still not applied.",
            "page": {"url": "https://example.com/review", "title": "Review"},
            "screenshot": None,
            "error": "max_iterations_reached",
            "status": "partial",
            "progress": {
                "completed_steps": ["Opened cart", "Reached review page"],
                "remaining_steps": ["Apply coupon", "Submit order"],
                "next_step": "Open the coupon panel and apply the saved code",
                "missing_requirements": ["Coupon code not applied yet"],
            },
        },
        {
            "ok": True,
            "final": "Coupon applied and order submitted.",
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
        first = _run(service.run_task(task="Checkout cart", session_id="session-reuse", request_id="req-1"))
        second = _run(service.run_task(task="Checkout cart", session_id="session-reuse", request_id="req-2"))

    assert first["ok"] is False
    assert first["progress_state"]["status"] == "partial"
    assert "Opened cart" in first["progress_state"]["completed_steps"]
    assert second["ok"] is True
    assert second["failure_summary"] is None
    assert second["progress_state"] is None
    assert "Known progress for continuation:" in observed_tasks[1]
    assert "Opened cart" in observed_tasks[1]
    assert "Apply coupon" in observed_tasks[1]


def test_completed_status_overrides_false_ok_when_evidence_is_present() -> None:
    service = _make_service()

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del task, session_id, request_id
        return {
            "ok": False,
            "final": "The confirmation page shows order #12345.",
            "page": {"url": "https://example.com/done", "title": "Done"},
            "screenshot": None,
            "error": "worker_marked_incomplete",
            "status": "completed",
            "progress": {
                "completion_evidence": ["Confirmation page shows order #12345"],
                "missing_requirements": [],
            },
        }

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ):
        result = _run(service.run_task(task="Place order", session_id="session-complete", request_id="req-complete"))

    assert result["ok"] is True
    assert result["error"] is None
    assert result["failure_summary"] is None

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

    with patch("openjiuwen.harness.tools.browser_move.playwright_runtime.service.Runner.run_agent", fake_run_agent):
        first = _run(service.run_task_once(task="Open page", session_id="session-1", request_id="req-1"))
        second = _run(service.run_task_once(task="Open page", session_id="session-1", request_id="req-1"))

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(seen_conversation_ids) == 2
    assert seen_conversation_ids[0] != seen_conversation_ids[1]
    assert all(isinstance(value, str) and value for value in seen_conversation_ids)
    assert seen_request_ids == ["req-1", "req-1"]


def test_ensure_managed_driver_started_reuses_healthy_existing_driver() -> None:
    async def _test():
        service = _make_service()
        setattr(service, "_driver_mode", "managed")
        healthy_driver = MagicMock()
        healthy_driver.is_endpoint_ready.return_value = True
        setattr(service, "_managed_driver", healthy_driver)

        with patch(
            "openjiuwen.harness.tools.browser_move.playwright_runtime.service.ManagedBrowserDriver"
        ) as mock_cls:
            await getattr(service, "_ensure_managed_driver_started")()

        assert getattr(service, "_managed_driver") is healthy_driver
        mock_cls.assert_not_called()

    _run(_test())


def test_ensure_managed_driver_started_replaces_stale_driver() -> None:
    async def _test():
        service = _make_service()
        setattr(service, "_driver_mode", "managed")
        stale_driver = MagicMock()
        stale_driver.is_endpoint_ready.return_value = False
        setattr(service, "_managed_driver", stale_driver)

        profile = BrowserProfile(
            name="jiuwenclaw",
            driver_type="managed",
            cdp_url="http://127.0.0.1:9333",
            user_data_dir=str(Path.cwd()),
            debug_port=9333,
            host="127.0.0.1",
        )
        new_driver = MagicMock()
        new_driver.start.return_value = "http://127.0.0.1:9333"
        profile_store = getattr(service, "_profile_store")

        with patch.object(profile_store, "get_profile", return_value=profile), patch.object(
            profile_store,
            "upsert_profile",
            side_effect=lambda browser_profile, select=False: browser_profile,
        ), patch(
            "openjiuwen.harness.tools.browser_move.playwright_runtime.service.ManagedBrowserDriver",
            return_value=new_driver,
        ):
            await getattr(service, "_ensure_managed_driver_started")()

        assert getattr(service, "_managed_driver") is new_driver
        new_driver.start.assert_called_once()

    _run(_test())


def test_profile_store_defaults_to_runtime_workspace() -> None:
    expected_root = (Path.cwd() / "tmp-runtime-root").resolve()
    service = _make_service(runtime_cwd=str(expected_root))
    expected = expected_root / ".browser" / "profiles.json"
    assert getattr(service, "_profile_store").path.resolve() == expected


def test_build_managed_profile_defaults_user_data_dir_to_runtime_workspace() -> None:
    with patch.dict(os.environ, {"BROWSER_DRIVER": "managed"}, clear=False):
        expected_root = (Path.cwd() / "tmp-runtime-root").resolve()
        service = _make_service(runtime_cwd=str(expected_root))
    profile = getattr(service, "_build_managed_profile")()
    expected = expected_root / ".browser-profiles" / getattr(service, "_profile_name")
    assert Path(profile.user_data_dir).resolve() == expected


def test_run_task_does_not_reset_browser_runtime_after_completion() -> None:
    service = _make_service()
    setattr(service, "_driver_mode", "managed")
    managed_driver = MagicMock()
    managed_driver.owns_process = True
    setattr(service, "_managed_driver", managed_driver)
    reset_calls = {"count": 0}

    async def fake_ensure_started() -> None:
        return None

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str):
        del task, session_id, request_id
        return {
            "ok": True,
            "final": "done",
            "page": {"url": "https://www.baidu.com", "title": "Baidu"},
            "screenshot": None,
            "error": None,
        }

    async def fake_reset_browser_runtime() -> None:
        reset_calls["count"] += 1

    with patch.object(service, "ensure_started", fake_ensure_started), patch.object(
        service, "run_task_once", fake_run_task_once
    ), patch.object(service, "_reset_browser_runtime", fake_reset_browser_runtime):
        result = _run(service.run_task(task="Open Baidu", session_id="keep-after-task", request_id="req-keep"))

    assert result["ok"] is True
    assert reset_calls["count"] == 0
