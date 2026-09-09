# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Convert vendor SDK objects into protocol-safe JSON values."""

from __future__ import annotations

import dataclasses
import math
import os
from enum import Enum
from typing import Any, Mapping


def to_json_safe(value: Any) -> Any:
    """Return a JSON-compatible copy of ``value`` accepted by ``freeze_json_value``.

    Dataclasses, pydantic models, enums, paths and nested containers are
    converted structurally; non-finite floats become ``None`` and anything
    else falls back to its string form so provider payloads never leak live
    SDK objects into the protocol event stream.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return to_json_safe(value.value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json_safe(getattr(value, field.name)) for field in dataclasses.fields(value)}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return to_json_safe(model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in value]
    return str(value)


def to_json_object(value: Any) -> dict[str, Any]:
    """Return ``value`` as a JSON object, wrapping non-mapping values."""

    converted = to_json_safe(value)
    if isinstance(converted, dict):
        return converted
    return {"value": converted}


__all__ = ["to_json_object", "to_json_safe"]
