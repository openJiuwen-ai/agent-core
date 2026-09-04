# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any


def normalize_finish_reason(
    raw_reason: Any,
    *,
    has_tool_calls: bool,
    default: str = "stop",
) -> str:
    """Normalize Chat and Responses terminal reasons to one contract.

    Explicit provider reasons other than ``stop``/``null`` are authoritative,
    especially truncation and content-filter signals.  A tool call replaces a
    normal or missing stop reason, while a missing reason without tool calls
    falls back to ``default``.
    """
    reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
    if reason and reason not in {"stop", "null"}:
        return reason
    if has_tool_calls:
        return "tool_calls"
    return reason or default
