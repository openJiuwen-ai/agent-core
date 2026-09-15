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

from typing import Any

# ``error.type`` for a call that returned a failing result instead of raising.
# Exceptions keep reporting their own class name.
TOOL_REPORTED_FAILURE = "ToolReportedFailure"

_DEFAULT_FAILURE_REASON = "tool reported failure"
_EXCEPTION_RESULT_PREFIX = "Ability execution error: "


def tool_failure_reason(output: Any) -> str | None:
    """Return the failure a tool reported in its own result.

    Only an explicit ``success is False`` counts. A result that carries no
    ``success`` field at all (workflow outputs, raw MCP payloads, plain
    strings) is left alone rather than guessed at.

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
        return None
    reason = str(error or "").strip()
    return reason or _DEFAULT_FAILURE_REASON


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
