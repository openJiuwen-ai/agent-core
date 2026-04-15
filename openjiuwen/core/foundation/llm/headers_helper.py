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


def merge_headers_case_insensitive(
        base_headers: dict[str, str],
        new_headers: Optional[Mapping[str, Any]],
) -> dict[str, str]:
    """Merge headers case-insensitively while preserving the first seen key casing."""
    if not new_headers:
        return base_headers

    normalized_to_key = {key.lower(): key for key in base_headers}
    for key, value in sanitize_headers(new_headers).items():
        normalized_key = key.lower()
        existing_key = normalized_to_key.get(normalized_key)
        if existing_key is not None:
            base_headers[existing_key] = value
            continue

        base_headers[key] = value
        normalized_to_key[normalized_key] = key

    return base_headers


def build_base_headers(
        *,
        custom_headers: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    """Build cached config-level headers from sanitized custom headers."""
    return sanitize_headers(custom_headers)


def merge_request_headers(
        base_headers: Optional[Mapping[str, Any]],
        request_custom_headers: Optional[Mapping[str, Any]],
) -> dict[str, str]:
    """Merge request-level headers onto prebuilt config-level headers."""
    effective_headers = dict(base_headers or {})
    merge_headers_case_insensitive(effective_headers, request_custom_headers)
    return effective_headers
