# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Claude Agent SDK failure classifier."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_teams.external.cli_agent.claude.failure_classifier import (
    classify_api_retry,
    classify_assistant_error,
    classify_claude_exception,
    classify_result_message,
    merge_claude_failure_messages,
)
from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk

_SDK = load_claude_sdk()


def _result(
    *,
    is_error: bool,
    api_error_status: int | None = None,
    errors: list[str] | None = None,
    result: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        is_error=is_error,
        api_error_status=api_error_status,
        errors=errors,
        result=result,
    )


# --- classify_assistant_error --------------------------------------------


def test_classify_assistant_error_maps_known_values():
    assert classify_assistant_error("authentication_failed")[0] == "auth_required"
    assert classify_assistant_error("invalid_request")[0] == "request_rejected"
    assert classify_assistant_error("billing_error")[0] == "quota_exceeded"
    assert classify_assistant_error("rate_limit")[0] == "rate_limited"
    assert classify_assistant_error("server_error")[0] == "server_unavailable"


def test_classify_assistant_error_degrades_unknown_to_sdk_error():
    category, reason = classify_assistant_error("unexpected_error")
    assert category == "sdk_error"
    assert reason.message == "unexpected_error"


# --- classify_result_message --------------------------------------------


def test_classify_result_message_maps_api_error_status():
    assert classify_result_message(_result(is_error=True, api_error_status=400))[0] == "request_rejected"
    assert classify_result_message(_result(is_error=True, api_error_status=401))[0] == "auth_required"
    assert classify_result_message(_result(is_error=True, api_error_status=403))[0] == "auth_required"
    assert classify_result_message(_result(is_error=True, api_error_status=429))[0] == "rate_limited"
    assert classify_result_message(_result(is_error=True, api_error_status=500))[0] == "server_unavailable"
    assert classify_result_message(_result(is_error=True, api_error_status=529))[0] == "server_unavailable"


def test_classify_result_message_without_api_status_degrades_to_sdk_error():
    category, reason = classify_result_message(_result(is_error=True, api_error_status=None, errors=["boom"]))
    assert category == "sdk_error"
    assert reason.http_status is None
    assert "boom" in reason.message


def test_classify_result_message_records_http_status():
    _, reason = classify_result_message(_result(is_error=True, api_error_status=429))
    assert reason.http_status == 429


def test_classify_result_message_combines_result_and_error_details():
    _, reason = classify_result_message(
        _result(
            is_error=True,
            api_error_status=429,
            errors=["upstream quota exhausted", "unknown"],
            result="API Error: Request rejected (429) · Budget has been exceeded",
        ),
    )

    assert reason.message == (
        "API Error: Request rejected (429) · Budget has been exceeded\n"
        "upstream quota exhausted"
    )


def test_merge_claude_failure_messages_deduplicates_contained_text():
    message = merge_claude_failure_messages(
        "rate_limit",
        "API Error: rate_limit · Budget has been exceeded",
        "unknown",
        "API Error: rate_limit · Budget has been exceeded",
    )

    assert message == "API Error: rate_limit · Budget has been exceeded"


# --- classify_api_retry -------------------------------------------------


def test_classify_api_retry_maps_status_and_preserves_retry_detail():
    category, reason = classify_api_retry(
        {
            "attempt": 3,
            "max_retries": 10,
            "retry_delay_ms": 36500.0,
            "error_status": 429,
            "error": "rate_limit",
        },
    )

    assert category == "rate_limited"
    assert reason.http_status == 429
    assert reason.sdk_error_code == "rate_limit"
    assert reason.message == "rate_limit: attempt 3/10, retry in 36.500s"


def test_classify_api_retry_falls_back_to_error_code():
    category, reason = classify_api_retry({"error": "server_error"})

    assert category == "server_unavailable"
    assert reason.http_status is None


# --- classify_claude_exception ------------------------------------------


def test_classify_claude_exception_startup_process_start_failed():
    exc = _SDK.CLIConnectionError("connection refused")
    category, reason = classify_claude_exception(exc, phase="startup")
    assert category == "process_start_failed"
    assert reason.sdk_error_type == "CLIConnectionError"


def test_classify_claude_exception_startup_cli_not_found():
    exc = _SDK.CLINotFoundError("not found")
    category, _ = classify_claude_exception(exc, phase="startup")
    assert category == "process_start_failed"


def test_classify_claude_exception_turn_connection_is_sdk_error():
    exc = _SDK.CLIConnectionError("stream broke")
    category, _ = classify_claude_exception(exc, phase="turn")
    assert category == "sdk_error"


def test_classify_claude_exception_turn_process_error_is_sdk_error():
    exc = _SDK.ProcessError("crash", exit_code=1, stderr="bang")
    category, reason = classify_claude_exception(exc, phase="turn")
    assert category == "sdk_error"
    assert "bang" in reason.message


def test_classify_claude_exception_timeout_is_network_timeout():
    category, _ = classify_claude_exception(TimeoutError("idle"), phase="turn")
    assert category == "network_timeout"


def test_classify_claude_idle_watchdog_as_network_timeout():
    idle_timeout_type = type("_ClaudeTurnIdleTimeout", (RuntimeError,), {})

    category, reason = classify_claude_exception(idle_timeout_type("stream stalled"), phase="turn")

    assert category == "network_timeout"
    assert reason.sdk_error_type == "_ClaudeTurnIdleTimeout"


def test_classify_claude_exception_unknown_is_sdk_error():
    category, reason = classify_claude_exception(ValueError("weird"), phase="turn")
    assert category == "sdk_error"
    assert "weird" in reason.message
