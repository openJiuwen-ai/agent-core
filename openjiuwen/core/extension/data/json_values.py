# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strict JSON validation shared by store requests and file persistence."""

import json
import math
import re
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

_PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def valid_package_id(package_id: object) -> bool:
    """Accept one portable package filename, excluding Windows device names."""
    return (
        isinstance(package_id, str)
        and _PACKAGE_ID.fullmatch(package_id) is not None
        and ".." not in package_id
        and not package_id.endswith(".")
        and package_id.split(".", 1)[0].upper() not in _RESERVED_NAMES
    )


def require_package_id(package_id: object) -> None:
    """Reject invalid package identifiers before touching any path."""
    if not valid_package_id(package_id):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="package_id is not a safe package identifier")


def copy_json(value: Any) -> Any:
    """Return an independent JSON value without coercing Python-only types."""
    try:
        return _copy_value(value, set())
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise build_error(
            StatusCode.EXTENSION_DATA_INVALID, cause=exc, reason="value must contain only finite JSON data"
        ) from exc


def _copy_value(value: Any, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if not isinstance(value, (list, dict)) or id(value) in active:
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="unsupported JSON value or cycle")
    active.add(id(value))
    try:
        if isinstance(value, list):
            return [_copy_value(item, active) for item in value]
        if not all(isinstance(key, str) for key in value):
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="JSON object keys must be strings")
        return {key: _copy_value(item, active) for key, item in value.items()}
    finally:
        active.remove(id(value))


def encode_json(value: Any) -> bytes:
    """Encode a validated JSON value as compact UTF-8 bytes."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise build_error(
            StatusCode.EXTENSION_DATA_INVALID, cause=exc, reason="value cannot be encoded as JSON UTF-8"
        ) from exc
