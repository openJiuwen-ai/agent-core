# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Non-mutating normalization at JSON provider request boundaries."""

from __future__ import annotations

import json
import re
from typing import Any

_SURROGATE = re.compile(r"[\ud800-\udfff]")


def clean_unicode(value: Any) -> Any:
    """Preserve valid pairs; replace isolated UTF-16 halves produced by DOM slicing."""
    if isinstance(value, str):
        if _SURROGATE.search(value):
            return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
        return value
    if isinstance(value, dict):
        return {clean_unicode(key): clean_unicode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(clean_unicode(item) for item in value)
    return value


def json_arguments(value: Any) -> str:
    """ToolCall permits dictionaries; provider function arguments must be JSON strings."""
    if isinstance(value, str):
        return clean_unicode(value)
    return json.dumps(clean_unicode(value if value is not None else {}), ensure_ascii=False)


def sanitize_chat_request(params: dict[str, Any]) -> dict[str, Any]:
    result = clean_unicode(params)
    for message in result.get("messages", []):
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict):
                function["arguments"] = json_arguments(function.get("arguments"))
    return result
