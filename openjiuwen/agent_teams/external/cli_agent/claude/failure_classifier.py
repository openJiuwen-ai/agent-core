# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK failure classification.

Pure functions turn Claude structured fields (``AssistantMessage.error``,
``ResultMessage.is_error`` / ``api_error_status`` / ``errors``) and Claude
SDK/CLI exceptions into a unified :class:`ExternalRuntimeFailureCategory`
plus an :class:`ExternalRuntimeFailureReason`.
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
    "rate_limit": "rate_limited",
    "server_error": "server_unavailable",
}

# ``ResultMessage.api_error_status`` → category.
_API_STATUS_MAP: dict[int, ExternalRuntimeFailureCategory] = {
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
    """Classify a Claude ``ResultMessage`` failure by its ``api_error_status`` and ``errors``."""
    errors = getattr(result, "errors", None) or []
    message = "\n".join(str(e) for e in errors) if errors else ""
    api_status = getattr(result, "api_error_status", None)
    http_status = int(api_status) if isinstance(api_status, int) else None
    category: ExternalRuntimeFailureCategory = "sdk_error"
    if http_status is not None and http_status in _API_STATUS_MAP:
        category = _API_STATUS_MAP[http_status]
    return category, ExternalRuntimeFailureReason(message=message, http_status=http_status)


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

    # Network/stream timeout — applies to either phase.
    if _is_timeout_exception(exc):
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
    "classify_assistant_error",
    "classify_claude_exception",
    "classify_result_message",
]
