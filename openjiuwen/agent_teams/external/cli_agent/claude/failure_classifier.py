# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK failure classification.

Pure functions turn Claude structured fields (``SystemMessage.api_retry``,
``AssistantMessage.error``, ``ResultMessage.is_error`` /
``api_error_status`` / ``errors``) and Claude SDK/CLI exceptions into a
unified :class:`ExternalRuntimeFailureCategory` plus an
:class:`ExternalRuntimeFailureReason`.
"""

from __future__ import annotations

from typing import Any, Tuple

from openjiuwen.agent_teams.schema.external_runtime_reliability import (
    ExternalRuntimeFailureCategory,
    ExternalRuntimeFailureReason,
)

# Maps Claude ``AssistantMessage.error`` string values to failure categories.
# The SDK declares these as a ``Literal`` (``AssistantMessageError``), which is
# a type annotation, not a runtime enum, so the membership is mirrored here as
# plain strings.
_ASSISTANT_ERROR_MAP: dict[str, ExternalRuntimeFailureCategory] = {
    "authentication_failed": "auth_required",
    "billing_error": "quota_exceeded",
    "invalid_request": "request_rejected",
    "rate_limit": "rate_limited",
    "server_error": "server_unavailable",
}

# ``ResultMessage.api_error_status`` → category.
_API_STATUS_MAP: dict[int, ExternalRuntimeFailureCategory] = {
    400: "request_rejected",
    401: "auth_required",
    403: "auth_required",
    429: "rate_limited",
    500: "server_unavailable",
    529: "server_unavailable",
}


def classify_assistant_error(
    error: Any,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Claude ``AssistantMessage.error`` value."""
    error_str = str(error or "")
    category: ExternalRuntimeFailureCategory = "sdk_error"
    if error_str in _ASSISTANT_ERROR_MAP:
        category = _ASSISTANT_ERROR_MAP[error_str]
    return category, ExternalRuntimeFailureReason(message=error_str)


def classify_result_message(
    result: Any,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Claude ``ResultMessage`` failure and retain its detail."""
    errors = getattr(result, "errors", None) or []
    result_detail = str(getattr(result, "result", None) or "")
    message = merge_claude_failure_messages(result_detail, *(str(error) for error in errors))
    api_status = getattr(result, "api_error_status", None)
    http_status = int(api_status) if isinstance(api_status, int) else None
    category: ExternalRuntimeFailureCategory = "sdk_error"
    if http_status is not None and http_status in _API_STATUS_MAP:
        category = _API_STATUS_MAP[http_status]
    return category, ExternalRuntimeFailureReason(message=message, http_status=http_status)


def merge_claude_failure_messages(*messages: Any) -> str:
    """Join distinct Claude diagnostics without repeating contained text."""
    details: list[str] = []
    for value in messages:
        for raw_line in str(value or "").splitlines():
            text = raw_line.strip()
            if not text or text.lower() == "unknown":
                continue
            if any(text in existing for existing in details):
                continue
            details = [existing for existing in details if existing not in text]
            details.append(text)
    return "\n".join(details)


def classify_api_retry(
    data: Any,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Claude ``SystemMessage(subtype="api_retry")`` payload."""
    payload = data if isinstance(data, dict) else {}
    raw_status = payload.get("error_status")
    http_status = int(raw_status) if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    error_code = str(payload.get("error") or "")
    category: ExternalRuntimeFailureCategory = "sdk_error"
    if http_status is not None and http_status in _API_STATUS_MAP:
        category = _API_STATUS_MAP[http_status]
    elif error_code in _ASSISTANT_ERROR_MAP:
        category = _ASSISTANT_ERROR_MAP[error_code]
    attempt = payload.get("attempt")
    max_retries = payload.get("max_retries")
    retry_delay_ms = payload.get("retry_delay_ms")
    retry_detail = ""
    if isinstance(attempt, int) and isinstance(max_retries, int):
        retry_detail = f"attempt {attempt}/{max_retries}"
    delay_detail = ""
    if isinstance(retry_delay_ms, (int, float)) and not isinstance(retry_delay_ms, bool):
        delay_detail = f"retry in {retry_delay_ms / 1000:.3f}s"
    message = error_code
    if retry_detail:
        message = f"{message}: {retry_detail}" if message else retry_detail
    if delay_detail:
        message = f"{message}, {delay_detail}" if message else delay_detail
    return category, ExternalRuntimeFailureReason(
        message=message,
        sdk_error_code=error_code,
        http_status=http_status,
    )


def classify_claude_exception(
    exc: BaseException,
    *,
    phase: str,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Claude SDK/CLI exception by type and phase."""
    # Lazy import keeps this module independent of the SDK at import time.
    from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk

    sdk = load_claude_sdk()
    exc_type_name = type(exc).__name__
    message = str(exc) or exc_type_name
    stderr = getattr(exc, "stderr", None)
    if stderr:
        message = f"{message}\nError output: {stderr}" if message else str(stderr)

    # Network/stream timeout — applies to either phase. The runtime's idle
    # watchdog raises its own sentinel, so match it by name to avoid an
    # import cycle (the classifier must stay runtime-independent).
    if _is_timeout_exception(exc) or exc_type_name == "_ClaudeTurnIdleTimeout":
        return "network_timeout", ExternalRuntimeFailureReason(
            message=message,
            sdk_error_type=exc_type_name,
        )

    process_start_types: tuple[type, ...] = (
        sdk.CLINotFoundError,
        sdk.CLIConnectionError,
        sdk.ProcessError,
    )
    connection_types: tuple[type, ...] = (
        sdk.CLIConnectionError,
        sdk.ProcessError,
    )

    if phase == "startup":
        if isinstance(exc, process_start_types):
            return "process_start_failed", ExternalRuntimeFailureReason(
                message=message,
                sdk_error_type=exc_type_name,
            )
    else:
        if isinstance(exc, connection_types):
            return "sdk_error", ExternalRuntimeFailureReason(
                message=message,
                sdk_error_type=exc_type_name,
            )

    return "sdk_error", ExternalRuntimeFailureReason(
        message=message,
        sdk_error_type=exc_type_name,
    )


def _is_timeout_exception(exc: BaseException) -> bool:
    """Return whether ``exc`` is an explicit timeout signal."""
    # ``asyncio.TimeoutError`` is an alias of ``TimeoutError`` since Python 3.11.
    return isinstance(exc, TimeoutError)


__all__ = [
    "classify_api_retry",
    "classify_assistant_error",
    "classify_claude_exception",
    "classify_result_message",
    "merge_claude_failure_messages",
]
