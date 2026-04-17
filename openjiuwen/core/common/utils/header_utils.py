# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Mapping, Optional

PROTECTED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "authorization"}


def sanitize_headers(headers: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """Drop invalid/protected keys, filter empty values, and normalize values to strings."""
    if not headers:
        return {}

    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key is None or value is None:
            continue

        key_str = str(key).strip()
        if not key_str:
            continue

        if key_str.lower() in PROTECTED_HEADERS:
            continue

        value_str = str(value)
        if not value_str.strip():
            continue

        sanitized[key_str] = value_str

    return sanitized
