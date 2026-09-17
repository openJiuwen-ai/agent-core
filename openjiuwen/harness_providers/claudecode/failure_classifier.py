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
    "invalid_request": "request_rejected",
    "rate_limit": "rate_limited",
    "server_error": "server_unavailable",
}

# ``ResultMessage.api_error_status`` -> category.
# Upper bound for assistant text folded into a turn error message.
_MAX_FAILURE_DETAIL_CHARS = 8000

_API_STATUS_MAP: dict[int, str] = {
    400: "request_rejected",
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


def classify_assistant_error(error: Any, message: Any = None) -> TurnError:
    """Classify a Claude ``AssistantMessage.error`` value.

    Args:
        error: The ``AssistantMessage.error`` value (an error identifier).
        message: The owning ``AssistantMessage``, when available. Its text
            blocks carry the human-readable cause that ``error`` omits.

    Returns:
        The classified turn error, carrying the message detail when present.
    """
    error_str = str(error or "")
    category = _ASSISTANT_ERROR_MAP.get(error_str, "sdk_error")
    detail = _assistant_failure_detail(message) if message is not None else ""
    return _turn_error(detail or error_str, category=category, code=error_str or None)


def _assistant_failure_detail(message: Any) -> str:
    """Extract bounded text diagnostics from a failed assistant message.

    Args:
        message: A Claude ``AssistantMessage``.

    Returns:
        The joined text blocks, truncated to ``_MAX_FAILURE_DETAIL_CHARS``.
    """
    from openjiuwen.harness_providers.claudecode.options import load_claude_sdk

    sdk = load_claude_sdk()
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    parts = [block.text for block in content if isinstance(block, sdk.TextBlock) and block.text]
    detail = "\n".join(parts)
    if len(detail) <= _MAX_FAILURE_DETAIL_CHARS:
        return detail
    return detail[:_MAX_FAILURE_DETAIL_CHARS] + "...[truncated]"


def classify_result_message(result: Any) -> TurnError:
    """Classify a Claude ``ResultMessage`` failure by ``api_error_status`` and ``errors``."""
    errors = getattr(result, "errors", None) or []
    informative = [item for item in errors if str(item).strip().lower() != "unknown"]
    message = "\n".join(str(item) for item in informative) if informative else ""
    api_status = getattr(result, "api_error_status", None)
    http_status = int(api_status) if isinstance(api_status, int) else None
    category = "sdk_error"
    if http_status is not None and http_status in _API_STATUS_MAP:
        category = _API_STATUS_MAP[http_status]
    if not message and http_status is not None:
        message = f"Claude turn failed: HTTP {http_status}"
    return _turn_error(
        message or f"Claude turn failed ({getattr(result, 'subtype', 'error')})",
        category=category,
        code=str(getattr(result, "subtype", "") or "") or None,
        http_status=http_status,
    )


def merge_pending_error(pending: TurnError | None, terminal: TurnError) -> TurnError:
    """Fold an earlier assistant diagnostic into the terminal failure.

    The pending category wins only where the terminal state is generic, but the
    pending message is kept either way: it is usually the only text naming the
    actual cause.

    Args:
        pending: The error classified from an earlier assistant message.
        terminal: The error classified from the terminal result message.

    Returns:
        The merged turn error.
    """
    if pending is None:
        return terminal
    category = terminal.category
    retryable = terminal.retryable
    provider_data = dict(terminal.provider_data)
    http_status = terminal.provider_data.get("http_status")
    generic_terminal = http_status is None or terminal.category == "sdk_error"
    if generic_terminal and pending.category not in (None, "sdk_error"):
        category = pending.category
        retryable = pending.retryable
        provider_data = dict(terminal.provider_data) or dict(pending.provider_data)
    return TurnError(
        message=_merged_message(pending, terminal),
        code=terminal.code or pending.code,
        category=category,
        retryable=retryable,
        provider_data=provider_data,
    )


def _merged_message(pending: TurnError, terminal: TurnError) -> str:
    """Combine a pending diagnostic with the terminal failure message.

    Args:
        pending: The error classified from an earlier assistant message.
        terminal: The error classified from the terminal result message.

    Returns:
        The terminal message, extended with the pending detail it does not
        already carry.
    """
    pending_message = pending.message.strip()
    terminal_message = terminal.message.strip()
    if not pending_message or pending_message == pending.code:
        return terminal_message or pending_message
    if not terminal_message:
        return pending_message
    if pending_message in terminal_message:
        return terminal_message
    return f"{terminal_message}\n{pending_message}"


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
