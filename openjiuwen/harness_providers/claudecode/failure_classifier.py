# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK failure classification.

Pure functions turn Claude structured fields (``AssistantMessage.error``,
``ResultMessage.is_error`` / ``api_error_status`` / ``errors``) and Claude
SDK/CLI exceptions into a provider-neutral :class:`TurnError` whose
``category`` is one of the shared external-runtime failure categories.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness_protocol import TurnError

# Shared failure vocabulary; the team reliability layer maps it one-to-one.
FAILURE_CATEGORIES = (
    "auth_required",
    "quota_exceeded",
    "rate_limited",
    "server_unavailable",
    "network_timeout",
    "process_start_failed",
    "sdk_error",
    "unknown",
)

# Maps Claude ``AssistantMessage.error`` string values to failure categories.
# The SDK declares these as a ``Literal`` (``AssistantMessageError``), which is
# a type annotation, not a runtime enum, so the membership is mirrored here as
# plain strings.
_ASSISTANT_ERROR_MAP: dict[str, str] = {
    "authentication_failed": "auth_required",
    "billing_error": "quota_exceeded",
    "rate_limit": "rate_limited",
    "server_error": "server_unavailable",
}

# ``ResultMessage.api_error_status`` -> category.
_API_STATUS_MAP: dict[int, str] = {
    401: "auth_required",
    403: "auth_required",
    429: "rate_limited",
    500: "server_unavailable",
    529: "server_unavailable",
}


def _turn_error(
    message: str,
    *,
    category: str,
    code: str | None = None,
    sdk_error_type: str | None = None,
    http_status: int | None = None,
) -> TurnError:
    provider_data: dict[str, Any] = {}
    if sdk_error_type:
        provider_data["sdk_error_type"] = sdk_error_type
    if http_status is not None:
        provider_data["http_status"] = http_status
    return TurnError(
        message=message or category,
        code=code,
        category=category,
        retryable=category in {"rate_limited", "server_unavailable", "network_timeout"},
        provider_data=provider_data,
    )


def classify_assistant_error(error: Any) -> TurnError:
    """Classify a Claude ``AssistantMessage.error`` value."""
    error_str = str(error or "")
    category = _ASSISTANT_ERROR_MAP.get(error_str, "sdk_error")
    return _turn_error(error_str, category=category, code=error_str or None)


def classify_result_message(result: Any) -> TurnError:
    """Classify a Claude ``ResultMessage`` failure by ``api_error_status`` and ``errors``."""
    errors = getattr(result, "errors", None) or []
    message = "\n".join(str(item) for item in errors) if errors else ""
    api_status = getattr(result, "api_error_status", None)
    http_status = int(api_status) if isinstance(api_status, int) else None
    category = "sdk_error"
    if http_status is not None and http_status in _API_STATUS_MAP:
        category = _API_STATUS_MAP[http_status]
    return _turn_error(
        message or f"Claude turn failed ({getattr(result, 'subtype', 'error')})",
        category=category,
        code=str(getattr(result, "subtype", "") or "") or None,
        http_status=http_status,
    )


def merge_pending_error(pending: TurnError | None, terminal: TurnError) -> TurnError:
    """Prefer a structured pending category when the terminal state is generic."""
    if pending is None:
        return terminal
    http_status = terminal.provider_data.get("http_status")
    if http_status is None or terminal.category == "sdk_error":
        if pending.category not in (None, "sdk_error"):
            return TurnError(
                message=terminal.message or pending.message,
                code=terminal.code or pending.code,
                category=pending.category,
                retryable=pending.retryable,
                provider_data=dict(terminal.provider_data) or dict(pending.provider_data),
            )
    return terminal


def classify_claude_exception(exc: BaseException, *, phase: str) -> TurnError:
    """Classify a Claude SDK/CLI exception by type and phase."""
    # Lazy import keeps this module independent of the SDK at import time.
    from openjiuwen.harness_providers.claudecode.options import load_claude_sdk

    sdk = load_claude_sdk()
    exc_type_name = type(exc).__name__
    message = str(exc) or exc_type_name
    stderr = getattr(exc, "stderr", None)
    if stderr:
        message = f"{message}\nError output: {stderr}" if message else str(stderr)

    # Network/stream timeout applies to either phase.
    if isinstance(exc, TimeoutError):
        return _turn_error(message, category="network_timeout", sdk_error_type=exc_type_name)

    process_start_types: tuple[type, ...] = (
        sdk.CLINotFoundError,
        sdk.CLIConnectionError,
        sdk.ProcessError,
    )
    if phase == "startup" and isinstance(exc, process_start_types):
        return _turn_error(message, category="process_start_failed", sdk_error_type=exc_type_name)
    return _turn_error(message, category="sdk_error", sdk_error_type=exc_type_name)


__all__ = [
    "FAILURE_CATEGORIES",
    "classify_assistant_error",
    "classify_claude_exception",
    "classify_result_message",
    "merge_pending_error",
]
