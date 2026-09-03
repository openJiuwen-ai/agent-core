# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Codex SDK failure classifier."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_teams.external.cli_agent.codex.failure_classifier import (
    classify_codex_error_info,
    classify_codex_exception,
    classify_error_notification,
    classify_turn_error,
)


def _error_info(value: str):
    """A plain enum-string codex_error_info (the common shape)."""
    return value


# --- classify_codex_error_info -----------------------------------------


def test_classify_codex_error_info_unauthorized():
    category, info = classify_codex_error_info(_error_info("unauthorized"), None)
    assert category == "auth_required"
    assert info == "unauthorized"


def test_classify_codex_error_info_quota():
    assert classify_codex_error_info(_error_info("usageLimitExceeded"), None)[0] == "quota_exceeded"
    assert classify_codex_error_info(_error_info("sessionBudgetExceeded"), None)[0] == "quota_exceeded"


def test_classify_codex_error_info_server_unavailable():
    assert classify_codex_error_info(_error_info("serverOverloaded"), None)[0] == "server_unavailable"
    assert classify_codex_error_info(_error_info("internalServerError"), None)[0] == "server_unavailable"


def test_classify_codex_error_info_network_timeout():
    from openai_codex.generated.v2_all import (
        HttpConnectionFailed,
        HttpConnectionFailedCodexErrorInfo,
        ResponseStreamConnectionFailed,
        ResponseStreamConnectionFailedCodexErrorInfo,
        ResponseStreamDisconnected,
        ResponseStreamDisconnectedCodexErrorInfo,
        ResponseTooManyFailedAttempts,
        ResponseTooManyFailedAttemptsCodexErrorInfo,
    )

    variants = [
        HttpConnectionFailedCodexErrorInfo(http_connection_failed=HttpConnectionFailed()),
        ResponseStreamConnectionFailedCodexErrorInfo(
            response_stream_connection_failed=ResponseStreamConnectionFailed(),
        ),
        ResponseStreamDisconnectedCodexErrorInfo(
            response_stream_disconnected=ResponseStreamDisconnected(),
        ),
        ResponseTooManyFailedAttemptsCodexErrorInfo(
            response_too_many_failed_attempts=ResponseTooManyFailedAttempts(),
        ),
    ]
    for variant in variants:
        category, info = classify_codex_error_info(variant, None)
        assert category == "network_timeout", variant
        assert info  # camelCase identifier populated


def test_classify_codex_error_info_sdk_error():
    for info in ("contextWindowExceeded", "cyberPolicy", "other"):
        assert classify_codex_error_info(_error_info(info), None)[0] == "sdk_error", info


def test_classify_codex_error_info_bad_request():
    assert classify_codex_error_info(_error_info("badRequest"), None)[0] == "request_rejected"


def test_http_status_overrides_codex_error_info():
    from openai_codex.generated.v2_all import (
        ResponseStreamDisconnected,
        ResponseStreamDisconnectedCodexErrorInfo,
    )

    # responseStreamDisconnected alone → network_timeout; with 401 → auth_required
    variant = ResponseStreamDisconnectedCodexErrorInfo(
        response_stream_disconnected=ResponseStreamDisconnected(http_status_code=401),
    )
    category, info = classify_codex_error_info(variant, 401)
    assert category == "auth_required"
    assert info == "responseStreamDisconnected"


def test_http_status_maps_rate_limited_and_server():
    assert classify_codex_error_info(None, 400)[0] == "request_rejected"
    assert classify_codex_error_info(None, 429)[0] == "rate_limited"
    assert classify_codex_error_info(None, 500)[0] == "server_unavailable"
    assert classify_codex_error_info(None, 529)[0] == "server_unavailable"


def test_http_status_unmapped_falls_back_to_info_category():
    # 404 is unmappable; the structured badRequest category wins.
    assert classify_codex_error_info(_error_info("badRequest"), 404)[0] == "request_rejected"


# --- classify_error_notification ---------------------------------------


def test_classify_error_notification_will_retry():
    payload = SimpleNamespace(
        error=SimpleNamespace(message="m", codex_error_info=_error_info("serverOverloaded")),
        will_retry=True,
    )
    category, reason, will_retry = classify_error_notification(payload)
    assert will_retry is True
    assert category == "server_unavailable"
    assert reason.sdk_error_code == "serverOverloaded"


def test_classify_error_notification_no_retry_records_pending_signal():
    payload = SimpleNamespace(
        error=SimpleNamespace(
            message="request failed",
            additional_details="upstream request id: request-123",
            codex_error_info=_error_info("badRequest"),
        ),
        will_retry=False,
    )
    category, reason, will_retry = classify_error_notification(payload)
    assert will_retry is False
    assert category == "request_rejected"
    assert reason.message == "request failed\nupstream request id: request-123"


# --- classify_turn_error -----------------------------------------------


def test_classify_turn_error_uses_turn_error_structured_fields():
    turn_error = SimpleNamespace(
        message="no auth",
        additional_details="credential rejected by upstream",
        codex_error_info=_error_info("unauthorized"),
    )
    category, reason = classify_turn_error(turn_error)
    assert category == "auth_required"
    assert reason.message == "no auth\ncredential rejected by upstream"
    assert reason.sdk_error_code == "unauthorized"


# --- classify_codex_exception ------------------------------------------


def test_classify_codex_exception_timeout():
    category, _ = classify_codex_exception(TimeoutError("idle"))
    assert category == "network_timeout"


def test_classify_codex_exception_unknown_is_sdk_error():
    category, reason = classify_codex_exception(ValueError("weird"))
    assert category == "sdk_error"
    assert "weird" in reason.message
