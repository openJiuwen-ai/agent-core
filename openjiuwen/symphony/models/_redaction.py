# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conservative redaction helpers for public Symphony JSON contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_ASSIGNMENT = re.compile(
    r"""(?i)(["']?)([a-z][a-z0-9_-]{1,64})\1\s*[:=]\s*"""
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,;\r\n{}\[\]]+)""",
)
_EXACT_SENSITIVE_NAMES = frozenset(
    {
        "access_key",
        "access_key_id",
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "aws_access_key_id",
        "aws_secret_access_key",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "header",
        "headers",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_access_key",
        "session_id",
        "token",
    }
)
_SENSITIVE_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "secret",
    }
)
_SENSITIVE_KEY_QUALIFIERS = frozenset(
    {
        "api",
        "access",
        "encryption",
        "private",
        "secret",
        "signing",
    }
)
_NON_SECRET_SUFFIXES = (
    "_count",
    "_enabled",
    "_length",
    "_name",
    "_policy",
    "_present",
    "_required",
    "_type",
)
_MAX_REDACTION_DEPTH = 64


def normalize_sensitive_name(value: object) -> str:
    """Normalize snake, kebab, spaced, and camelCase identifiers."""

    split_acronym = _ACRONYM_BOUNDARY.sub("_", str(value or ""))
    split_camel = _CAMEL_BOUNDARY.sub("_", split_acronym)
    return re.sub(r"[^a-z0-9]+", "_", split_camel.casefold()).strip("_")


def is_sensitive_name(value: object) -> bool:
    """Return whether a field name conventionally carries credential material."""

    normalized = normalize_sensitive_name(value)
    if normalized.endswith(_NON_SECRET_SUFFIXES):
        return False
    if normalized in _EXACT_SENSITIVE_NAMES:
        return True
    tokens = set(normalized.split("_"))
    if tokens & _SENSITIVE_TOKENS:
        return True
    if normalized.endswith("_token"):
        return True
    return "key" in tokens and bool(tokens & _SENSITIVE_KEY_QUALIFIERS)


def redact_sensitive_text(value: str) -> str:
    """Redact complete assignment values for recognized sensitive keys."""

    def replace(match: re.Match[str]) -> str:
        if not is_sensitive_name(match.group(2)):
            return match.group(0)
        quote = match.group(1)
        return f"{quote}{match.group(2)}{quote}=<redacted>"

    return _ASSIGNMENT.sub(replace, value)


def redact_sensitive_json(value: Any, _active: set[int] | None = None, _depth: int = 0) -> Any:
    """Recursively redact sensitive mapping values without trimming opaque data."""

    active = set() if _active is None else _active
    is_container = isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    )
    if _depth >= _MAX_REDACTION_DEPTH and is_container:
        return "<redacted:depth-limit>"
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            return "<redacted:recursive>"
        active.add(identity)
        try:
            return {
                str(key): ("<redacted>" if is_sensitive_name(key) else redact_sensitive_json(item, active, _depth + 1))
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in active:
            return "<redacted:recursive>"
        active.add(identity)
        try:
            return [redact_sensitive_json(item, active, _depth + 1) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


__all__ = [
    "is_sensitive_name",
    "normalize_sensitive_name",
    "redact_sensitive_json",
    "redact_sensitive_text",
]
