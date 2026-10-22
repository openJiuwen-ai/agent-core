# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strip image/base64 payloads from content before serializing it as text.

Every path that flattens message content to text — token estimation,
compressor serialization, sending history to a compaction LLM — balloons the
moment a base64 ``data:`` URL is treated as ordinary text: a single screenshot
becomes tens of thousands of tokens of noise. These helpers replace such
payloads with short placeholders while leaving the surrounding structure
intact. Standalone on purpose: only ``re``/``json``, so both the token counter
and the compressors can depend on it without layering cycles.
"""

from __future__ import annotations

import json
import re
from typing import Any

# No \s in the payload class: data URLs contain no whitespace by spec, and
# allowing it makes the match swallow ordinary prose following the URL.
_DATA_URL_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)

IMAGE_DATA_URL_PLACEHOLDER = "[image:data-url-omitted]"
IMAGE_BASE64_PLACEHOLDER = "[image:base64-omitted]"


def _sanitize_dict(payload: dict) -> dict:
    sanitized: dict = {}
    for key, value in payload.items():
        if key in {"data", "data_url", "url"} and isinstance(value, str):
            # Image data URLs only: a non-image payload (e.g. data:application/pdf)
            # must not be mislabeled with an image placeholder. Case-variant image
            # URLs are still caught by the _DATA_URL_RE pass in the fallthrough.
            if value.startswith("data:image/"):
                sanitized[key] = IMAGE_DATA_URL_PLACEHOLDER
                continue
        if key == "source" and isinstance(value, dict):
            source = dict(value)
            if source.get("type") == "base64" and isinstance(source.get("data"), str):
                source["data"] = IMAGE_BASE64_PLACEHOLDER
            sanitized[key] = sanitize_value_for_text(source)
            continue
        if key == "image_url" and isinstance(value, dict):
            image_url = dict(value)
            url = image_url.get("url")
            if isinstance(url, str) and (url.startswith("data:image/") or "base64," in url):
                image_url["url"] = IMAGE_DATA_URL_PLACEHOLDER
            sanitized[key] = sanitize_value_for_text(image_url)
            continue
        sanitized[key] = sanitize_value_for_text(value)
    return sanitized


def sanitize_value_for_text(value: Any) -> Any:
    """Return a copy of *value* with image/base64 payloads replaced by placeholders."""
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return IMAGE_DATA_URL_PLACEHOLDER
        return _DATA_URL_RE.sub(IMAGE_DATA_URL_PLACEHOLDER, value)
    if isinstance(value, dict):
        return _sanitize_dict(value)
    if isinstance(value, list):
        return [sanitize_value_for_text(item) for item in value]
    return value


def sanitize_content_for_text(content: Any) -> str:
    """Serialize message-like content to text without embedding raw image bytes."""
    if isinstance(content, str):
        sanitized = sanitize_value_for_text(content)
        return sanitized if isinstance(sanitized, str) else str(sanitized)
    sanitized = sanitize_value_for_text(content)
    try:
        return json.dumps(sanitized, ensure_ascii=False)
    except TypeError:
        return str(sanitized)


__all__ = [
    "IMAGE_BASE64_PLACEHOLDER",
    "IMAGE_DATA_URL_PLACEHOLDER",
    "sanitize_content_for_text",
    "sanitize_value_for_text",
]
