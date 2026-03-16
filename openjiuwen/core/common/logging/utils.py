# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Logging Utility Functions

This module provides logging-related utility functions, optimized for async environments (e.g., asyncio).
Uses contextvars instead of threading.local() to support async context isolation.
"""

import contextvars
import os
from typing import (
    Any,
    List,
    Optional,
    Union,
)

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.security.path_checker import is_sensitive_path
from openjiuwen.core.common.security.user_config import UserConfig

# Use ContextVar instead of threading.local() to support async environments
# ContextVar maintains context isolation in async call chains, each coroutine has independent context
_trace_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="default_trace_id")


def set_session_id(trace_id: str = "default_trace_id") -> None:
    """
    Set trace_id in current context

    In async environments, this sets trace_id in current coroutine and its child coroutines.
    Each coroutine has an independent context copy that does not interfere with each other.

    Args:
        trace_id: Trace ID for log correlation and tracing

    Note:
        Function name remains "thread_session" for backward compatibility,
        but actual implementation uses contextvars to support async environments.
    """
    _trace_id_context.set(trace_id)


def get_session_id() -> Optional[str]:
    """
    Get trace_id from current context

    In async environments, this returns the trace_id from current coroutine context.
    If not set, returns default value 'default_trace_id'.

    Returns:
        trace_id from current context, or default value if not set

    Note:
        Function name remains "thread_session" for backward compatibility,
        but actual implementation uses contextvars to support async environments.
    """
    try:
        return _trace_id_context.get()
    except LookupError:
        # If no value in context, return default value
        return "default_trace_id"


def get_log_max_bytes(max_bytes_config: Any) -> int:
    try:
        max_bytes = int(max_bytes_config)
    except (ValueError, TypeError) as e:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"invalid max_bytes configuration: {max_bytes_config}, error: {e}"
        ) from e

    default_log_max_bytes = 100 * 1024 * 1024
    if max_bytes <= 0 or max_bytes > default_log_max_bytes:
        max_bytes = default_log_max_bytes

    return max_bytes


def normalize_and_validate_log_path(path_value: Any) -> str:
    """
    Normalize log path (realpath -> abspath) and check sensitivity.

    This helper is shared by logger config and default logger implementation.
    It raises BaseError when:
      - the value type is invalid, or
      - the normalized path is considered sensitive/unsafe.
    """
    # Support str / PathLike, and guard against invalid types / empty values
    try:
        path_str = os.fspath(path_value)
    except TypeError:
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the path_value is {path_value}'
        ) from e

    if not path_str or str(path_str).strip() == "":
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the path_str is {path_str}'
        )

    try:
        real_path = os.path.realpath(path_str)
    except OSError:
        real_path = os.path.abspath(os.path.expanduser(path_str))

    if is_sensitive_path(real_path):
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the real_path is {real_path}'
        )

    return real_path


def _summarize_tool_call(tc: Any) -> str:
    """Format a single tool call for logging."""
    if isinstance(tc, dict):
        fn = tc.get("function", {})
        return f"{fn.get('name', '?')}({str(fn.get('arguments', ''))[:100]})"
    fn = getattr(tc, "function", tc)
    name = getattr(fn, "name", getattr(tc, "name", "?"))
    args = str(getattr(fn, "arguments", getattr(tc, "arguments", "")))[:100]
    return f"{name}({args})"


def log_llm_request(
    log: Any,
    messages: Optional[List[Any]],
    tools: Optional[List[Any]],
) -> None:
    """Log LLM request messages and tools.

    Args:
        log: Logger instance
        messages: Request messages
        tools: Tool definitions
    """
    msgs = messages or []
    tool_count = len(tools) if tools else 0
    log.info(
        f"[LLM] >>> request: msg_count={len(msgs)}, "
        f"tool_count={tool_count}"
    )
    if UserConfig.is_sensitive():
        return
    for idx, msg in enumerate(msgs):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id", "")
        else:
            role = getattr(msg, "role", "")
            content = str(getattr(msg, "content", ""))
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", "")
        parts: List[str] = [f"[LLM]   msg[{idx}] role={role}"]
        if content:
            parts.append(f"content={content[:300]}")
        if tool_calls:
            tc_summary = [_summarize_tool_call(tc) for tc in tool_calls]
            parts.append(f"tool_calls=[{', '.join(tc_summary)}]")
        if tool_call_id:
            parts.append(f"tool_call_id={tool_call_id}")
        log.info(", ".join(parts))


def log_llm_response(log: Any, ai_message: Any) -> None:
    """Log LLM response content and tool calls.

    Args:
        log: Logger instance
        ai_message: AssistantMessage from LLM
    """
    usage = getattr(ai_message, "usage_metadata", None)
    usage_str = ""
    if usage:
        usage_str = (
            f", tokens={{input={getattr(usage, 'input_tokens', '?')}, "
            f"output={getattr(usage, 'output_tokens', '?')}}}"
        )
    if UserConfig.is_sensitive():
        tc_count = len(ai_message.tool_calls) if ai_message.tool_calls else 0
        log.info(
            f"[LLM] <<< response: "
            f"content_len={len(ai_message.content or '')}, "
            f"tool_call_count={tc_count}{usage_str}"
        )
    else:
        log.info(
            f"[LLM] <<< response: "
            f"content={ai_message.content or ''}{usage_str}"
        )
        if ai_message.tool_calls:
            for tc in ai_message.tool_calls:
                log.info(
                    f"[LLM]   tool_call: "
                    f"{tc.name}({tc.arguments})"
                )

