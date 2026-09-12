# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenAI Codex SDK failure classification.

Turn Codex structured fields (``ErrorNotification.error``,
``TurnCompletedNotification.turn.error`` and their ``codex_error_info`` /
``http_status_code``) and Codex SDK exceptions into a provider-neutral
:class:`TurnError` carrying one of the shared failure categories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from openjiuwen.harness_protocol import TurnError
from openjiuwen.harness_providers.codex.options import load_codex_sdk

_HTTP_STATUS_CATEGORY: dict[int, str] = {
    400: "request_rejected",
    401: "auth_required",
    403: "auth_required",
    429: "rate_limited",
    500: "server_unavailable",
    529: "server_unavailable",
}
_RETRYABLE = frozenset({"rate_limited", "server_unavailable", "network_timeout"})
# Upper bound for a failure message carried into the turn error. Codex
# ``additional_details`` can hold a whole request body dump.
_MAX_FAILURE_DETAIL_CHARS = 8000
# ``CodexErrorInfoValue`` camelCase identifiers -> shared failure categories.
_ERROR_INFO_CATEGORY: dict[str, str] = {
    "unauthorized": "auth_required",
    "usageLimitExceeded": "quota_exceeded",
    "sessionBudgetExceeded": "quota_exceeded",
    "serverOverloaded": "server_unavailable",
    "internalServerError": "server_unavailable",
    "contextWindowExceeded": "sdk_error",
    "badRequest": "request_rejected",
    "cyberPolicy": "sdk_error",
    "threadRollbackFailed": "sdk_error",
    "sandboxError": "sdk_error",
    "other": "sdk_error",
}
_VARIANT_CATEGORY: dict[str, str] = {
    "HttpConnectionFailedCodexErrorInfo": "network_timeout",
    "ResponseStreamConnectionFailedCodexErrorInfo": "network_timeout",
    "ResponseStreamDisconnectedCodexErrorInfo": "network_timeout",
    "ResponseTooManyFailedAttemptsCodexErrorInfo": "network_timeout",
    "ActiveTurnNotSteerableCodexErrorInfo": "sdk_error",
}


@dataclass(frozen=True)
class _SdkTypes:
    """SDK error-info types resolved lazily; empty when the SDK is unavailable."""

    info_root: type | None = None
    value_enum: type | None = None
    variants: dict[type, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _sdk_types() -> _SdkTypes:
    try:
        v2 = load_codex_sdk().generated.v2_all
        variants = {
            getattr(v2, name): category for name, category in _VARIANT_CATEGORY.items() if hasattr(v2, name)
        }
        return _SdkTypes(info_root=v2.CodexErrorInfo, value_enum=v2.CodexErrorInfoValue, variants=variants)
    except Exception:  # noqa: BLE001 - classification must degrade, never raise
        return _SdkTypes()


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
        code=code or None,
        category=category,
        retryable=category in _RETRYABLE,
        provider_data=provider_data,
    )


def classify_codex_error_info(error_info: Any, http_status: int | None) -> tuple[str, str]:
    """Classify a Codex ``codex_error_info`` value, honoring HTTP precedence.

    Returns ``(category, info_value)`` where ``info_value`` is the normalized
    camelCase identifier of the error info.
    """
    info_value = _normalize_error_info(error_info)
    category = _category_for(error_info, info_value)
    if http_status is not None and http_status in _HTTP_STATUS_CATEGORY:
        category = _HTTP_STATUS_CATEGORY[http_status]
    return category, info_value


def _unwrap(error_info: Any) -> Any:
    types = _sdk_types()
    if types.info_root is not None and isinstance(error_info, types.info_root):
        return error_info.root
    return error_info


def _category_for(error_info: Any, info_value: str) -> str:
    unwrapped = _unwrap(error_info)
    types = _sdk_types()
    for variant, category in types.variants.items():
        if isinstance(unwrapped, variant):
            return category
    return _ERROR_INFO_CATEGORY.get(info_value, "sdk_error")


def classify_turn_error(turn_error: Any) -> TurnError:
    """Classify a Codex ``TurnCompletedNotification.turn.error``."""
    message = _codex_error_message(turn_error)
    error_info = getattr(turn_error, "codex_error_info", None)
    http_status = _extract_http_status(error_info)
    category, info_value = classify_codex_error_info(error_info, http_status)
    return _turn_error(message, category=category, code=info_value, http_status=http_status)


def classify_error_notification(payload: Any) -> tuple[TurnError, bool]:
    """Classify a Codex ``ErrorNotification`` payload.

    Returns ``(error, will_retry)``. ``will_retry=True`` means the SDK is
    still retrying and the turn keeps running.
    """
    error = getattr(payload, "error", None)
    will_retry = bool(getattr(payload, "will_retry", False))
    message = _codex_error_message(error)
    error_info = getattr(error, "codex_error_info", None)
    http_status = _extract_http_status(error_info)
    category, info_value = classify_codex_error_info(error_info, http_status)
    return _turn_error(message, category=category, code=info_value, http_status=http_status), will_retry


def classify_codex_exception(exc: BaseException) -> TurnError:
    """Classify a Codex SDK exception by HTTP status, code and timeout hint."""
    exc_type_name = type(exc).__name__
    message = str(exc) or exc_type_name
    http_status = _http_status_from_exception(exc)
    code = getattr(exc, "code", None)
    code_str = str(code) if isinstance(code, int) else ""
    if isinstance(exc, TimeoutError):
        return _turn_error(message, category="network_timeout", sdk_error_type=exc_type_name, http_status=http_status)
    error_info = _extract_error_info_from_data(getattr(exc, "data", None))
    if error_info is not None or http_status is not None:
        category, info_value = classify_codex_error_info(error_info, http_status)
        return _turn_error(
            message,
            category=category,
            code=info_value or code_str,
            sdk_error_type=exc_type_name,
            http_status=http_status,
        )
    return _turn_error(message, category="sdk_error", code=code_str, sdk_error_type=exc_type_name)


def merge_pending_error(pending: TurnError | None, terminal: TurnError | None) -> TurnError:
    """Prefer the structured pending candidate when the terminal state is generic."""
    if terminal is None:
        if pending is None:
            return _turn_error("codex SDK turn failed without a structured error", category="sdk_error")
        return pending
    if pending is None:
        return terminal
    if terminal.category == "sdk_error" and pending.category not in (None, "sdk_error"):
        return TurnError(
            message=terminal.message or pending.message,
            code=terminal.code or pending.code,
            category=pending.category,
            retryable=pending.retryable,
            provider_data=dict(terminal.provider_data) or dict(pending.provider_data),
        )
    return terminal


def _codex_error_message(error: Any) -> str:
    """Combine the bounded Codex error summary with its extra diagnostics.

    Args:
        error: A Codex ``TurnError`` or ``ErrorNotification.error`` payload.

    Returns:
        The summary plus ``additional_details`` when it adds anything, truncated
        to ``_MAX_FAILURE_DETAIL_CHARS``.
    """
    message = str(getattr(error, "message", "") or "").strip()
    additional_details = str(getattr(error, "additional_details", "") or "").strip()
    if additional_details and additional_details not in message:
        message = f"{message}\n{additional_details}" if message else additional_details
    if len(message) <= _MAX_FAILURE_DETAIL_CHARS:
        return message
    return message[:_MAX_FAILURE_DETAIL_CHARS] + "...[truncated]"


def _normalize_error_info(error_info: Any) -> str:
    """Return the camelCase identifier for a ``CodexErrorInfo`` value."""
    if error_info is None:
        return ""
    unwrapped = _unwrap(error_info)
    types = _sdk_types()
    if types.value_enum is not None and isinstance(unwrapped, types.value_enum):
        return str(unwrapped.value)
    if isinstance(unwrapped, str):
        return unwrapped
    for variant in types.variants:
        if isinstance(unwrapped, variant):
            model_field = next(iter(type(unwrapped).model_fields.values()))
            return model_field.alias or ""
    value = getattr(unwrapped, "value", None)
    return str(value) if isinstance(value, str) else ""


def _extract_http_status(error_info: Any) -> int | None:
    """Pull ``http_status_code`` out of a structured ``CodexErrorInfo``."""
    if error_info is None:
        return None
    unwrapped = _unwrap(error_info)
    types = _sdk_types()
    for variant in types.variants:
        if isinstance(unwrapped, variant):
            field_name = next(iter(type(unwrapped).model_fields))
            nested = getattr(unwrapped, field_name)
            status = getattr(nested, "http_status_code", None)
            return status if isinstance(status, int) else None
    return None


def _http_status_from_exception(exc: BaseException) -> int | None:
    data = getattr(exc, "data", None)
    if data is not None:
        status = _extract_http_status(data)
        if status is not None:
            return status
    status = getattr(exc, "http_status_code", None)
    return status if isinstance(status, int) else None


def _extract_error_info_from_data(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("codex_error_info", "codexErrorInfo", "errorInfo"):
            if key in data:
                return _normalize_error_info(data[key])
    if isinstance(data, str):
        return data
    return None


__all__ = [
    "classify_codex_error_info",
    "classify_codex_exception",
    "classify_error_notification",
    "classify_turn_error",
    "merge_pending_error",
]
