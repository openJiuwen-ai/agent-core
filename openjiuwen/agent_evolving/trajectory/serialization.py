# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared JSON-compatible value conversion for trajectory payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from openjiuwen.core.common.logging import logger


def to_json_compatible(value: Any) -> Any:
    """Return a detached JSON-compatible representation of ``value``."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_compatible(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_compatible(asdict(value))
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            return to_json_compatible(method())
        except Exception:
            logger.warning(
                "Failed to serialize trajectory value with %s(); trying the next fallback.",
                method_name,
                exc_info=True,
            )
            continue
    return str(value)


__all__ = ["to_json_compatible"]
