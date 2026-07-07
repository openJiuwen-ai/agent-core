#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
import importlib.util
import logging
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_STATUS_LOGGING_PATH = (
    Path(__file__).resolve().parents[5]
    / "openjiuwen"
    / "harness"
    / "tools"
    / "browser_move"
    / "playwright_runtime"
    / "status_logging.py"
)
_SPEC = importlib.util.spec_from_file_location("browser_status_logging", _STATUS_LOGGING_PATH)
assert _SPEC is not None and _SPEC.loader is not None
status_logging = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(status_logging)

BrowserSubagentStatusLogger = status_logging.BrowserSubagentStatusLogger
install_browser_subagent_status_logging_async = status_logging.install_browser_subagent_status_logging_async
is_browser_subagent_status_log_enabled = status_logging.is_browser_subagent_status_log_enabled


class FakeEvent(Enum):
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    ON_MODEL_EXCEPTION = "on_model_exception"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_EXCEPTION = "on_tool_exception"


class FakeAgent:
    def __init__(self) -> None:
        self.callbacks: list[tuple[Any, Any, int]] = []

    async def register_callback(self, event: Any, callback: Any, priority: int = 100) -> "FakeAgent":
        self.callbacks.append((event, callback, priority))
        return self


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_status_logger_summarizes_batch_args_without_values() -> None:
    logger = BrowserSubagentStatusLogger()

    summary = logger.summarize_args(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#firstName", "value": "Alice"},
                {"op": "fill", "placeholder": "Email", "value": "alice@example.com"},
                {"op": "click", "role": "button", "name": "Search"},
            ],
            "timeout_ms": 3000,
            "global_timeout_ms": 20000,
        },
    )

    assert summary["kind"] == "browser_batch_interact"
    assert summary["step_count"] == 3
    assert summary["op_counts"] == {"click": 1, "fill": 2}
    assert summary["steps_preview"][0]["target_keys"] == ["selector"]
    assert summary["steps_preview"][0]["value_keys_redacted"] == ["value"]
    assert "Alice" not in str(summary)
    assert "alice@example.com" not in str(summary)


def test_status_logger_summarizes_failed_batch_result() -> None:
    logger = BrowserSubagentStatusLogger()

    summary = logger.summarize_result(
        "browser_batch_interact",
        {
            "ok": False,
            "elapsed_ms": 124,
            "error": "step failed",
            "steps": [
                {"index": 0, "op": "fill", "ok": True},
                {"index": 1, "op": "select_option", "ok": False, "error": "missing option"},
            ],
        },
    )

    assert summary["ok"] is False
    assert summary["steps_total"] == 2
    assert summary["steps_ok"] == 1
    assert summary["steps_failed"] == 1
    assert summary["first_failed_step"] == {
        "index": 1,
        "op": "select_option",
        "error": "missing option",
    }


def test_status_logger_redacts_type_and_code_args() -> None:
    logger = BrowserSubagentStatusLogger()

    type_summary = logger.summarize_args(
        "mcp_playwright-official_browser_type",
        {"selector": "#email", "text": "alice@example.com"},
    )
    code_summary = logger.summarize_args(
        "mcp_playwright-official_browser_run_code_unsafe",
        {"code": "console.log(document.body.innerText)"},
    )

    assert type_summary["redacted_values"]["text"]["redacted"] is True
    assert "alice@example.com" not in str(type_summary)
    assert code_summary["code_redacted"] is True
    assert code_summary["code_length"] == len("console.log(document.body.innerText)")
    assert "document.body" not in str(code_summary)


def test_status_logger_emits_backend_marker(caplog: Any) -> None:
    backing_logger = logging.getLogger("browser-status-test")
    status_logger = BrowserSubagentStatusLogger(
        logger=backing_logger,
        metadata_provider=lambda: {"session_id": "sess-1", "request_id": "req-1"},
    )
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(query="Session id: sess-1\nRequest id: req-1\nTask:\nsecret task"),
        extra={},
        session=None,
    )

    with caplog.at_level(logging.INFO, logger="browser-status-test"):
        status_logger.before_invoke(ctx)

    assert "[BROWSER_SUBAGENT]" in caplog.text
    assert '"phase": "task_start"' in caplog.text
    assert '"session_id": "sess-1"' in caplog.text
    assert "secret task" not in caplog.text


def test_installer_registers_expected_callbacks() -> None:
    agent = FakeAgent()

    _run(install_browser_subagent_status_logging_async(agent, FakeEvent, priority=77))

    registered_events = [event for event, _callback, priority in agent.callbacks]
    assert registered_events == [
        FakeEvent.BEFORE_INVOKE,
        FakeEvent.AFTER_INVOKE,
        FakeEvent.BEFORE_MODEL_CALL,
        FakeEvent.AFTER_MODEL_CALL,
        FakeEvent.ON_MODEL_EXCEPTION,
        FakeEvent.BEFORE_TOOL_CALL,
        FakeEvent.AFTER_TOOL_CALL,
        FakeEvent.ON_TOOL_EXCEPTION,
    ]
    assert all(priority == 77 for _event, _callback, priority in agent.callbacks)


def test_env_toggle_for_status_logging(monkeypatch: Any) -> None:
    monkeypatch.delenv("BROWSER_SUBAGENT_STATUS_LOG", raising=False)
    assert is_browser_subagent_status_log_enabled() is True

    monkeypatch.setenv("BROWSER_SUBAGENT_STATUS_LOG", "0")
    assert is_browser_subagent_status_log_enabled() is False

    monkeypatch.setenv("BROWSER_SUBAGENT_STATUS_LOG", "yes")
    assert is_browser_subagent_status_log_enabled() is True



def test_direct_browser_runtime_rail_wires_status_logger() -> None:
    runtime_path = (
        Path(__file__).resolve().parents[5]
        / "openjiuwen"
        / "harness"
        / "tools"
        / "browser_move"
        / "playwright_runtime"
        / "runtime.py"
    )
    source = runtime_path.read_text(encoding="utf-8")

    assert "from .status_logging import BrowserSubagentStatusLogger" in source
    assert 'self._emit_status("before_invoke", ctx)' in source
    assert 'self._emit_status("before_model_call", ctx)' in source
    assert 'self._emit_status("after_model_call", ctx)' in source
    assert 'self._emit_status("on_model_exception", ctx)' in source
    assert 'self._emit_status("before_tool_call", ctx)' in source
    assert 'self._emit_status("after_tool_call", ctx)' in source
    assert 'self._emit_status("on_tool_exception", ctx)' in source
    assert 'self._emit_status("after_invoke", ctx)' in source


def test_status_logger_accumulates_task_end_totals(caplog: Any) -> None:
    backing_logger = logging.getLogger("browser-status-aggregate-test")
    status_logger = BrowserSubagentStatusLogger(
        logger=backing_logger,
        metadata_provider=lambda: {"request_id": "req-aggregate"},
    )

    class FakeSession:
        def get_session_id(self) -> str:
            return "sess-aggregate"

    class FakeCall:
        id = "tool-call-1"
        name = "browser_batch_interact"

    ctx = SimpleNamespace(
        inputs=SimpleNamespace(query="Go to https://demoqa.com/automation-practice-form", conversation_id="sess-aggregate"),
        extra={},
        session=FakeSession(),
        agent=object(),
    )
    model_ctx = SimpleNamespace(
        inputs=SimpleNamespace(messages=["m1"], tools=["t1"], response=SimpleNamespace(content="", reasoning_content="", tool_calls=[])),
        extra=ctx.extra,
        session=ctx.session,
        agent=ctx.agent,
    )
    tool_ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            tool_call=FakeCall(),
            tool_name="browser_batch_interact",
            tool_args={"steps": [{"op": "fill", "selector": "#x", "value": "secret"}]},
            tool_result={
                "ok": True,
                "steps": [
                    {"index": 0, "op": "fill", "ok": True},
                    {"index": 1, "op": "click", "ok": False, "error": "timeout"},
                ],
            },
        ),
        extra=ctx.extra,
        session=ctx.session,
        agent=ctx.agent,
    )
    end_ctx = SimpleNamespace(
        inputs=SimpleNamespace(result={"output": "done", "result_type": "answer"}),
        extra=ctx.extra,
        session=ctx.session,
        agent=ctx.agent,
    )

    with caplog.at_level(logging.INFO, logger="browser-status-aggregate-test"):
        status_logger.before_invoke(ctx)
        status_logger.before_model_call(model_ctx)
        status_logger.after_model_call(model_ctx)
        status_logger.before_tool_call(tool_ctx)
        status_logger.after_tool_call(tool_ctx)
        status_logger.after_invoke(end_ctx)

    assert '"phase": "task_end"' in caplog.text
    assert '"model_calls": 1' in caplog.text
    assert '"tool_calls": 1' in caplog.text
    assert '"browser_batch_calls": 1' in caplog.text
    assert '"browser_batch_steps_failed": 1' in caplog.text
    assert "secret" not in caplog.text


def test_status_logger_partial_batch_result_flags() -> None:
    logger = BrowserSubagentStatusLogger()

    summary = logger.summarize_result(
        "browser_batch_interact",
        {
            "ok": True,
            "steps": [
                {"index": 0, "op": "fill", "ok": True},
                {"index": 1, "op": "click", "ok": False, "error": "timeout"},
            ],
        },
    )

    assert summary["ok"] is True
    assert summary["all_steps_ok"] is False
    assert summary["had_step_errors"] is True
    assert summary["steps_failed"] == 1


def test_batch_interact_select_option_accepts_values_alias() -> None:
    action_path = (
        Path(__file__).resolve().parents[5]
        / "openjiuwen"
        / "harness"
        / "tools"
        / "browser_move"
        / "controllers"
        / "action.py"
    )
    source = action_path.read_text(encoding="utf-8")

    assert "step.values !== undefined" in source
    assert "target.selectOption(values" in source
    assert "requires value, values," in source


def test_react_agent_redacts_browser_tool_arguments() -> None:
    react_agent_path = (
        Path(__file__).resolve().parents[5]
        / "openjiuwen"
        / "core"
        / "single_agent"
        / "agents"
        / "react_agent.py"
    )
    source = react_agent_path.read_text(encoding="utf-8")

    assert "_summarize_tool_args_for_log" in source
    assert "_is_browser_tool_name" in source
    assert "mcp_playwright" in source
    assert "Executing tool: %s with args: %s" in source
    assert "browser_batch_interact" in source


def test_status_logger_uses_same_run_key_for_conversation_and_session_without_shared_extra(caplog: Any) -> None:
    backing_logger = logging.getLogger("browser-status-run-key-test")
    status_logger = BrowserSubagentStatusLogger(logger=backing_logger)

    class FakeSession:
        def get_session_id(self) -> str:
            return "sess-same"

    class FakeCall:
        id = "tool-call-run-key"
        name = "browser_snapshot"

    start_ctx = SimpleNamespace(
        inputs=SimpleNamespace(query="Open https://example.com", conversation_id="sess-same"),
        extra={},
        session=None,
        agent=object(),
    )
    model_ctx = SimpleNamespace(
        inputs=SimpleNamespace(messages=["m"], tools=["t"], response=SimpleNamespace(content="", reasoning_content="", tool_calls=[])),
        extra={},
        session=FakeSession(),
        agent=start_ctx.agent,
    )
    tool_ctx = SimpleNamespace(
        inputs=SimpleNamespace(tool_call=FakeCall(), tool_name="browser_snapshot", tool_args={}, tool_result={"ok": True}),
        extra={},
        session=FakeSession(),
        agent=start_ctx.agent,
    )
    end_ctx = SimpleNamespace(
        inputs=SimpleNamespace(result={"output": "done"}),
        extra={},
        session=FakeSession(),
        agent=start_ctx.agent,
    )

    with caplog.at_level(logging.INFO, logger="browser-status-run-key-test"):
        status_logger.before_invoke(start_ctx)
        status_logger.before_model_call(model_ctx)
        status_logger.after_model_call(model_ctx)
        status_logger.before_tool_call(tool_ctx)
        status_logger.after_tool_call(tool_ctx)
        status_logger.after_invoke(end_ctx)

    assert '"run_key": "sess-same"' in caplog.text
    assert '"model_calls": 1' in caplog.text
    assert '"tool_calls": 1' in caplog.text


def test_status_logger_failed_batch_call_without_step_list_is_error() -> None:
    logger = BrowserSubagentStatusLogger()

    summary = logger.summarize_result(
        "browser_batch_interact",
        {
            "ok": False,
            "error": "locator.click: Timeout 5000ms exceeded",
            "steps": [],
        },
    )

    assert summary["ok"] is False
    assert summary["all_steps_ok"] is False
    assert summary["had_step_errors"] is True
    assert summary["steps_total"] == 0
    assert summary["steps_failed"] == 0
    assert summary["first_failed_step"]["error"] == "locator.click: Timeout 5000ms exceeded"


def test_react_agent_redacts_task_tool_description() -> None:
    react_agent_path = (
        Path(__file__).resolve().parents[5]
        / "openjiuwen"
        / "core"
        / "single_agent"
        / "agents"
        / "react_agent.py"
    )
    source = react_agent_path.read_text(encoding="utf-8")

    assert 'lowered_name == "task_tool"' in source
    assert '"task_description"' in source
    assert '"sha256_12"' in source
    assert '"kind": "task_tool"' in source
