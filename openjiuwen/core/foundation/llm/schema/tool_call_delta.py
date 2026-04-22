# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
"""Tool call delta serialization utilities"""

from typing import Any, Dict


def serialize_tool_call_delta(tool_call: Any) -> Dict[str, Any]:
    """Serialize a streamed tool call delta for WS/history transport."""
    arguments = getattr(tool_call, "arguments", "")
    if arguments is None:
        arguments = ""
    elif not isinstance(arguments, str):
        arguments = str(arguments)
    _id = getattr(tool_call, "id", "") or ""
    return {
        "id": _id,
        "tool_call_id": _id,
        "type": getattr(tool_call, "type", "function") or "function",
        "name": getattr(tool_call, "name", "") or "",
        "arguments": arguments,
        "index": getattr(tool_call, "index", 0),
    }
