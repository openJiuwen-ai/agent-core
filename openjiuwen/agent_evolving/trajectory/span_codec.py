# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure helpers for trajectory OTLP JSON encoding."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger

# Stable marker so callers can tell a model_dump/string coercion fallback from
# a normally serialized string payload.
JSON_SAFE_FALLBACK_MARKER = "__openjiuwen.json_safe_fallback__"


def is_json_safe_fallback(value: Any) -> bool:
    """Return True when ``value`` is a marked ``json_safe`` degradation payload."""
    return isinstance(value, dict) and value.get(JSON_SAFE_FALLBACK_MARKER) is True


def _serialization_fallback(
    value: Any,
    *,
    reason: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        JSON_SAFE_FALLBACK_MARKER: True,
        "type": type(value).__qualname__,
        "repr": str(value),
        "reason": reason,
    }
    if error is not None:
        payload["error_type"] = type(error).__qualname__
        payload["error"] = str(error)
    return payload


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if is_dataclass(value):
        return json_safe(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return json_safe(model_dump())
        except Exception as exc:
            logger.warning(
                "Failed to serialize value with model_dump(); "
                "returning marked fallback payload instead of bare string.",
                exc_info=True,
            )
            return _serialization_fallback(
                value,
                reason="model_dump_failed",
                error=exc,
            )
    return str(value)


def now_nanos() -> int:
    return time.time_ns()


def datetime_to_nanos(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1_000_000_000)


def span_id_hex(trace_id: str, invoke_id: str | None) -> str:
    return hashlib.sha256(f"{trace_id}:{invoke_id or ''}".encode("utf-8")).hexdigest()[:16]


def normalize_trace_id_hex(trace_id: Any) -> str:
    """Return an OTLP-compatible 32-character lowercase hex trace id."""
    try:
        return uuid.UUID(str(trace_id)).hex
    except (TypeError, ValueError):
        return hashlib.sha256(str(trace_id).encode("utf-8")).hexdigest()[:32]


def to_otlp_value(value: Any) -> dict[str, Any]:
    value = json_safe(value)
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [to_otlp_value(item) for item in value if item is not None]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": to_otlp_value(item)} for key, item in value.items() if item is not None
                ]
            }
        }
    return {"stringValue": json.dumps(value, ensure_ascii=False, default=str)}


def otlp_value_to_python(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "arrayValue" in value:
        return [otlp_value_to_python(item) for item in (value["arrayValue"].get("values") or [])]
    if "kvlistValue" in value:
        return {
            item.get("key"): otlp_value_to_python(item.get("value") or {})
            for item in (value["kvlistValue"].get("values") or [])
            if item.get("key")
        }
    return None


def attributes_to_otlp(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for key, value in attributes.items():
        if value is None:
            continue
        result.append({"key": str(key), "value": to_otlp_value(value)})
    return result


def normalize_error(error: Any) -> dict[str, Any]:
    if isinstance(error, BaseError):
        return {"error_code": error.status.code, "message": error.message}
    if isinstance(error, dict):
        return json_safe(error)
    return {
        "error_code": StatusCode.WORKFLOW_EXECUTION_ERROR.code,
        "message": str(error),
    }


def unwrap_io(value: Any, field_name: str) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {field_name}:
        return value[field_name]
    return value


def unwrap_nested_io(value: Any, *, preferred: str = "inputs") -> Any:
    """Peel nested ``inputs``/``outputs`` dict wrappers until a leaf payload remains.

    Prefers ``preferred`` ("inputs" or "outputs"), then tries the other key.
    Stops when neither key yields a nested dict.
    """
    if preferred not in {"inputs", "outputs"}:
        raise ValueError(f"preferred must be 'inputs' or 'outputs', got {preferred!r}")
    other = "outputs" if preferred == "inputs" else "inputs"
    while isinstance(value, dict):
        wrapped = value.get(preferred)
        if not isinstance(wrapped, dict):
            wrapped = value.get(other)
        if not isinstance(wrapped, dict):
            break
        value = wrapped
    return value


def as_message_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        if "messages" in value:
            return as_message_list(value["messages"])
        if "inputs" in value:
            return as_message_list(value["inputs"])
        if "outputs" in value:
            return as_message_list(value["outputs"])
    if isinstance(value, list):
        return value
    return [value]


__all__ = [
    "JSON_SAFE_FALLBACK_MARKER",
    "as_message_list",
    "attributes_to_otlp",
    "datetime_to_nanos",
    "is_json_safe_fallback",
    "json_safe",
    "normalize_trace_id_hex",
    "normalize_error",
    "now_nanos",
    "otlp_value_to_python",
    "span_id_hex",
    "to_otlp_value",
    "unwrap_io",
    "unwrap_nested_io",
]
