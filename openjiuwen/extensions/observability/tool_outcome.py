# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared reading of how one tool call actually ended.

A tool call can fail in two unrelated ways, and the span has to record both
the same way or the trajectory disagrees with the conversation the model saw:

* it **raises** — the ability manager catches the exception and still hands the
  model a ``ToolMessage`` describing it, so the span needs an output too, not
  only an exception event;
* it **returns** ``ToolOutput(success=False)`` — the far more common shape for
  the built-in tools (bash, grep, glob, read_file, write_file). Nothing raises,
  so a status derived from exceptions alone reports OK on a failed call.

Both the harness rail (which owns the authoritative tool span) and the
low-level callback handler (which owns it in team mode) read the outcome
through this module, so the two paths cannot drift apart.
"""

from __future__ import annotations

import re
from typing import Any

# ``error.type`` for a call that returned a failing result instead of raising.
# Exceptions keep reporting their own class name.
TOOL_REPORTED_FAILURE = "ToolReportedFailure"

# 工具以裸字符串/字典返回失败时统一使用的错误前缀标记。多工具约定（见 web_search /
# image_tools / command_tools / audio_tools / video_tools / bash_tool_safety /
# acp_chat 等），runtime 未抛异常、也未显式 success=False 时，靠此前缀识别失败，
# 否则 web_search 之类"吞掉异常后返回 [ERROR]: ... 字符串"的调用会让 trace 对失败视而不见。
_ERROR_PREFIX_RE = re.compile(r"^\[ERROR\]\s*:?\s*(.*)$", re.IGNORECASE | re.DOTALL)

_DEFAULT_FAILURE_REASON = "tool reported failure"
_EXCEPTION_RESULT_PREFIX = "Ability execution error: "


def tool_failure_reason(output: Any) -> str | None:
    """Return the failure a tool reported in its own result.

    An explicit ``success is False`` always wins (the common shape for built-in
    tools like bash that return ``ToolOutput(success=False, error=...)``). As a
    backstop, a result whose text is prefixed with the shared ``[ERROR]`` marker
    — returned as a plain string, or inside an ``error`` field, without raising —
    is also treated as a failure, so the span gets marked ERROR instead of OK.

    Args:
        output: Whatever the ability returned — a ``ToolOutput``, a mapping, or
            any other value.

    Returns:
        The reason text to put on the span status, or None when the call did
        not report a failure.
    """

    if isinstance(output, dict):
        success = output.get("success")
        error = output.get("error")
    else:
        success = getattr(output, "success", None)
        error = getattr(output, "error", None)

    if success is not False:
        # 兜底：工具吞掉异常后返回 "[ERROR]: ..." 这类约定错误串时，仍判为失败。
        return _error_string_reason(output)
    reason = str(error or "").strip()
    return reason or _DEFAULT_FAILURE_REASON


def _error_string_reason(output: Any) -> str | None:
    """Backstop: detect the shared ``[ERROR]`` failure marker in a result's text.

    Covers the shape where a tool returns ``"[ERROR]: <reason>"`` (or ``"[ERROR]
    <reason>"``) as a plain string, or carries it inside an ``error`` field,
    without raising and without an explicit ``success=False``.
    """
    text: str | None = None
    if isinstance(output, str):
        text = output
    elif isinstance(output, dict):
        err = output.get("error")
        if isinstance(err, str):
            text = err
    else:
        err = getattr(output, "error", None)
        if isinstance(err, str):
            text = err
    if not text:
        return None
    match = _ERROR_PREFIX_RE.match(text.strip())
    if match:
        return match.group(1).strip() or _DEFAULT_FAILURE_REASON
    return None


def tool_result_for_exception(exception: BaseException) -> str:
    """Rebuild the tool result the model was handed for a raised call.

    ``AbilityManager`` turns a raised tool call into a ``ToolMessage`` before
    the model ever sees it, preferring the exception's own ``tool_message``
    when it carries one. Mirroring that here keeps the recorded output equal to
    the conversation content instead of merely similar to it.

    Args:
        exception: The exception the tool call raised.

    Returns:
        The tool result text as the model received it.
    """

    tool_message = getattr(exception, "tool_message", None)
    content = getattr(tool_message, "content", None)
    if content:
        return str(content)
    return f"{_EXCEPTION_RESULT_PREFIX}{exception}"
