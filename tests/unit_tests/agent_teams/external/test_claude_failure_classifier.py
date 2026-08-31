# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Claude Agent SDK failure classifier."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_teams.external.cli_agent.claude.failure_classifier import (
    classify_assistant_error,
    classify_claude_exception,
    classify_result_message,
)
from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk

_SDK = load_claude_sdk()


def _result(*, is_error: bool, api_error_status=None, errors=None):
    return SimpleNamespace(
        is_error=is_error,
        api_error_status=api_error_status,
        errors=errors,
    )


# --- classify_assistant_error --------------------------------------------


def test_classify_assistant_error_maps_known_values():
    assert classify_assistant_error("authentication_failed")[0] == "auth_required"
    assert classify_assistant_error("billing_error")[0] == "quota_exceeded"
    assert classify_assistant_error("rate_limit")[0] == "rate_limited"
    assert classify_assistant_error("server_error")[0] == "server_unavailable"


def test_classify_assistant_error_degrades_unknown_to_sdk_error():
    category, reason = classify_assistant_error("invalid_request")
    assert category == "sdk_error"
    assert reason.message == "invalid_request"


# --- classify_result_message --------------------------------------------


def test_classify_result_message_maps_api_error_status():
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


def test_classify_claude_exception_unknown_is_sdk_error():
    category, reason = classify_claude_exception(ValueError("weird"), phase="turn")
    assert category == "sdk_error"
    assert "weird" in reason.message
