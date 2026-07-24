# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
from contextvars import ContextVar, Token


_BROWSER_AGENT_LOG_CONTEXT: ContextVar[bool] = ContextVar(
    "openjiuwen_browser_agent_log_context",
    default=False,
)
_FALSE_VALUES = {"0", "false", "no", "off", ""}
_EXPLICIT_BROWSER_MARKERS = (
    "[BROWSER_AGENT_LOG]",
    "[BROWSER_SUBAGENT]",
    "[BROWSER_SUBAGENT_BOOT]",
    "[BROWSER_SUBAGENT_ERROR]",
    "[BROWSER_BATCH]",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def is_browser_agent_log_context() -> bool:
    return bool(_BROWSER_AGENT_LOG_CONTEXT.get())


def set_browser_agent_log_context(enabled: bool = True) -> Token[bool]:
    return _BROWSER_AGENT_LOG_CONTEXT.set(bool(enabled))


def reset_browser_agent_log_context(token: Token[bool]) -> None:
    _BROWSER_AGENT_LOG_CONTEXT.reset(token)


def is_browser_agent_context_debug_log_enabled() -> bool:
    """Return whether generic framework logs should enter browser debug logs.

    Browser subagents run with a logging context so common framework logs can be
    redirected away from the main app log. By default, generic framework and raw
    LLM request/response logs are suppressed from the browser debug file because
    they are noisy and can contain full prompts/tool schemas. Set either env var
    below to opt back into the old verbose behavior for deep diagnosis:

    - OPENJIUWEN_BROWSER_AGENT_CONTEXT_DEBUG_LOG=1
    - OPENJIUWEN_BROWSER_AGENT_FRAMEWORK_EVENTS=1
    """
    return _env_bool(
        "OPENJIUWEN_BROWSER_AGENT_CONTEXT_DEBUG_LOG",
        default=_env_bool("OPENJIUWEN_BROWSER_AGENT_FRAMEWORK_EVENTS", False),
    )


def should_log_browser_agent_context_message(message: str) -> bool:
    """Filter browser-context common logs before writing them to browser logs."""
    if is_browser_agent_context_debug_log_enabled():
        return True
    text = str(message or "")
    return any(marker in text for marker in _EXPLICIT_BROWSER_MARKERS)
