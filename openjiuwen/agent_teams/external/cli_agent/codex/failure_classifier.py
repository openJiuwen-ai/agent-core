# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenAI Codex SDK failure classification.

Turn Codex structured fields (``ErrorNotification.error``,
``TurnCompletedNotification.turn.error`` and their ``codex_error_info`` /
``http_status_code``) and Codex SDK exceptions into a unified
:class:`ExternalRuntimeFailureCategory` plus an
:class:`ExternalRuntimeFailureReason`.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from openjiuwen.agent_teams.external.cli_agent.codex.options import load_codex_sdk
from openjiuwen.agent_teams.schema.external_runtime_reliability import (
    ExternalRuntimeFailureCategory,
    ExternalRuntimeFailureReason,
)

_v2 = load_codex_sdk().generated.v2_all
CodexErrorInfoValue = _v2.CodexErrorInfoValue
CodexErrorInfo = _v2.CodexErrorInfo

_CODEX_ERROR_INFO_MAP: dict[str, ExternalRuntimeFailureCategory] = {
    CodexErrorInfoValue.unauthorized.value: "auth_required",
    CodexErrorInfoValue.usage_limit_exceeded.value: "quota_exceeded",
    CodexErrorInfoValue.session_budget_exceeded.value: "quota_exceeded",
    CodexErrorInfoValue.server_overloaded.value: "server_unavailable",
    CodexErrorInfoValue.internal_server_error.value: "server_unavailable",
    CodexErrorInfoValue.context_window_exceeded.value: "sdk_error",
    CodexErrorInfoValue.bad_request.value: "sdk_error",
    CodexErrorInfoValue.cyber_policy.value: "sdk_error",
    CodexErrorInfoValue.thread_rollback_failed.value: "sdk_error",
    CodexErrorInfoValue.sandbox_error.value: "sdk_error",
    CodexErrorInfoValue.other.value: "sdk_error",
}

_STRUCTURED_VARIANT_CATEGORY: dict[type, ExternalRuntimeFailureCategory] = {
    _v2.HttpConnectionFailedCodexErrorInfo: "network_timeout",
    _v2.ResponseStreamConnectionFailedCodexErrorInfo: "network_timeout",
    _v2.ResponseStreamDisconnectedCodexErrorInfo: "network_timeout",
    _v2.ResponseTooManyFailedAttemptsCodexErrorInfo: "network_timeout",
    _v2.ActiveTurnNotSteerableCodexErrorInfo: "sdk_error",
}

_HTTP_STATUS_CATEGORY: dict[int, ExternalRuntimeFailureCategory] = {
    401: "auth_required",
    403: "auth_required",
    429: "rate_limited",
    500: "server_unavailable",
    529: "server_unavailable",
}


def classify_codex_error_info(
    error_info: Any,
    http_status: Optional[int],
) -> Tuple[ExternalRuntimeFailureCategory, str]:
    """Classify a Codex ``codex_error_info`` value, honoring HTTP precedence.

    ``error_info`` is the SDK's discriminated union (a plain enum member or a
    structured variant). Returns ``(category, info_value)`` where ``info_value``
    is the normalized camelCase string for ``reason``.
    """
    info_value = _normalize_error_info(error_info)
    category: ExternalRuntimeFailureCategory = _category_for(error_info, info_value)
    http_category = _http_status_category(http_status)
    if http_category is not None:
        category = http_category
    return category, info_value


def _category_for(
    error_info: Any,
    info_value: str,
) -> ExternalRuntimeFailureCategory:
    """Return the default category for a ``codex_error_info`` value."""
    unwrapped = error_info.root if isinstance(error_info, CodexErrorInfo) else error_info
    if isinstance(unwrapped, tuple(_STRUCTURED_VARIANT_CATEGORY)):
        return _STRUCTURED_VARIANT_CATEGORY[type(unwrapped)]
    if info_value in _CODEX_ERROR_INFO_MAP:
        return _CODEX_ERROR_INFO_MAP[info_value]
    return "sdk_error"


def classify_turn_error(
    turn_error: Any,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Codex ``TurnCompletedNotification.turn.error`` (TurnError)."""
    message = str(getattr(turn_error, "message", "") or "")
    error_info = getattr(turn_error, "codex_error_info", None)
    http_status = _extract_http_status(error_info)
    category, info_value = classify_codex_error_info(error_info, http_status)
    return category, ExternalRuntimeFailureReason(
        message=message,
        sdk_error_code=info_value,
        http_status=http_status,
    )


def classify_error_notification(
    payload: Any,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason, bool]:
    """Classify a Codex ``ErrorNotification`` payload.

    Returns ``(category, reason, will_retry)``. ``will_retry=True`` signals the
    runtime to publish retrying progress and keep the round running;
    ``will_retry=False`` records a pending failure and waits for the terminal
    state.
    """
    error = getattr(payload, "error", None)
    will_retry = bool(getattr(payload, "will_retry", False))
    message = str(getattr(error, "message", "") or "")
    error_info = getattr(error, "codex_error_info", None)
    http_status = _extract_http_status(error_info)
    category, info_value = classify_codex_error_info(error_info, http_status)
    return (
        category,
        ExternalRuntimeFailureReason(
            message=message,
            sdk_error_code=info_value,
            http_status=http_status,
        ),
        will_retry,
    )


def classify_codex_exception(
    exc: BaseException,
) -> Tuple[ExternalRuntimeFailureCategory, ExternalRuntimeFailureReason]:
    """Classify a Codex SDK exception by HTTP status, code and timeout hint.

    Structured fields win; an explicit timeout maps to ``network_timeout``;
    everything else degrades to ``sdk_error``. Raw text is preserved in
    ``reason.message``.
    """
    exc_type_name = type(exc).__name__
    message = str(exc) or exc_type_name
    http_status = _http_status_from_exception(exc)
    code = getattr(exc, "code", None)
    code_str = str(code) if isinstance(code, int) else ""

    if _is_timeout_exception(exc):
        return "network_timeout", ExternalRuntimeFailureReason(
            message=message,
            sdk_error_type=exc_type_name,
            http_status=http_status,
        )

    # codex_error_info carried in RPC error data (server_overloaded etc.).
    data = getattr(exc, "data", None)
    error_info = _extract_error_info_from_data(data)
    if error_info is not None or http_status is not None:
        category, info_value = classify_codex_error_info(error_info, http_status)
        return category, ExternalRuntimeFailureReason(
            message=message,
            sdk_error_type=exc_type_name,
            sdk_error_code=info_value or code_str,
            http_status=http_status,
        )

    return "sdk_error", ExternalRuntimeFailureReason(
        message=message,
        sdk_error_type=exc_type_name,
        sdk_error_code=code_str,
    )


# ------------------------------------------------------------------
# Extraction helpers
# ------------------------------------------------------------------


def _normalize_error_info(error_info: Any) -> str:
    """Return the camelCase identifier for a ``CodexErrorInfo`` value.

    ``CodexErrorInfo`` is a pydantic ``RootModel`` discriminated union: a plain
    ``CodexErrorInfoValue`` enum member or a structured variant.
    """
    if error_info is None:
        return ""
    if isinstance(error_info, CodexErrorInfo):
        return _normalize_error_info(error_info.root)
    if isinstance(error_info, CodexErrorInfoValue):
        return error_info.value
    if isinstance(error_info, str):
        return error_info
    if isinstance(error_info, tuple(_STRUCTURED_VARIANT_CATEGORY)):
        field = next(iter(type(error_info).model_fields.values()))
        return field.alias or ""
    return ""


def _extract_http_status(error_info: Any) -> Optional[int]:
    """Pull ``http_status_code`` out of a structured ``CodexErrorInfo``."""
    if error_info is None:
        return None
    if isinstance(error_info, CodexErrorInfo):
        return _extract_http_status(error_info.root)
    if isinstance(error_info, tuple(_STRUCTURED_VARIANT_CATEGORY)):
        field_name = next(iter(type(error_info).model_fields))
        nested = getattr(error_info, field_name)
        return getattr(nested, "http_status_code", None)
    return None


def _http_status_category(http_status: Optional[int]) -> Optional[ExternalRuntimeFailureCategory]:
    """Map an HTTP status to a category, or ``None`` when not mappable."""
    if http_status is None:
        return None
    return _HTTP_STATUS_CATEGORY.get(http_status)


def _http_status_from_exception(exc: BaseException) -> Optional[int]:
    """Best-effort HTTP status extraction from a Codex SDK exception."""
    data = getattr(exc, "data", None)
    if data is not None:
        status = _extract_http_status(data)
        if status is not None:
            return status
    return getattr(exc, "http_status_code", None) if hasattr(exc, "http_status_code") else None


def _extract_error_info_from_data(data: Any) -> Optional[str]:
    """Return a ``codex_error_info`` string from an exception's ``data`` field."""
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("codex_error_info", "codexErrorInfo", "errorInfo"):
            if key in data:
                return _normalize_error_info(data[key])
    if isinstance(data, str):
        return data
    return None


def _is_timeout_exception(exc: BaseException) -> bool:
    """Return whether ``exc`` is an explicit timeout signal."""
    # ``asyncio.TimeoutError`` is an alias of ``TimeoutError`` since Python 3.11.
    return isinstance(exc, TimeoutError)


__all__ = [
    "classify_codex_error_info",
    "classify_codex_exception",
    "classify_error_notification",
    "classify_turn_error",
]
