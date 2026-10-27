# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""UTF-8-safe text helpers for LLM request/response boundaries.

Python ``str`` stores Unicode scalar values. Lone UTF-16 surrogate code units
(U+D800–U+DFFF) can still appear when a model/stream emits a broken half of an
emoji pair. Those code units are legal in ``str`` / ``json.dumps(ensure_ascii=
False)`` but illegal for ``str.encode("utf-8")`` and OpenAI ``openapi_dumps``.

Intact astral characters (e.g. emoji U+1F600) are single code points in Python
and are preserved. Replacement uses U+FFFD (REPLACEMENT CHARACTER), matching
Unicode and the common ``errors="replace"`` practice.
"""

from __future__ import annotations

import re
from typing import Any, Tuple

# UTF-16 surrogate code units (U+D800–U+DFFF).
_UTF16_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_utf8_text(value: str) -> Tuple[str, int]:
    """Replace UTF-16 surrogate code units so the text is UTF-8 encodable.

    Returns:
        (sanitized_text, replacement_count)
    """
    if not value or not isinstance(value, str):
        return value, 0
    matches = _UTF16_SURROGATE_RE.findall(value)
    if not matches:
        return value, 0
    return _UTF16_SURROGATE_RE.sub("\ufffd", value), len(matches)


def sanitize_utf8_tree(value: Any) -> Tuple[Any, int]:
    """Recursively sanitize string leaves in nested request/response payloads."""
    if isinstance(value, str):
        return sanitize_utf8_text(value)
    if isinstance(value, list):
        total = 0
        out = []
        for item in value:
            cleaned, count = sanitize_utf8_tree(item)
            out.append(cleaned)
            total += count
        return out, total
    if isinstance(value, dict):
        total = 0
        out: dict[str, Any] = {}
        for key, item in value.items():
            cleaned, count = sanitize_utf8_tree(item)
            out[key] = cleaned
            total += count
        return out, total
    return value, 0


def sanitize_message_content(content: Any) -> Any:
    """Sanitize assistant/user ``content`` which may be ``str`` or multimodal list."""
    if isinstance(content, str):
        return sanitize_utf8_text(content)[0]
    if isinstance(content, list):
        return sanitize_utf8_tree(content)[0]
    return content
