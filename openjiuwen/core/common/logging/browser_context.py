# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from contextvars import ContextVar, Token


_BROWSER_AGENT_LOG_CONTEXT: ContextVar[bool] = ContextVar(
    "openjiuwen_browser_agent_log_context",
    default=False,
)


def is_browser_agent_log_context() -> bool:
    return bool(_BROWSER_AGENT_LOG_CONTEXT.get())


def set_browser_agent_log_context(enabled: bool = True) -> Token[bool]:
    return _BROWSER_AGENT_LOG_CONTEXT.set(bool(enabled))


def reset_browser_agent_log_context(token: Token[bool]) -> None:
    _BROWSER_AGENT_LOG_CONTEXT.reset(token)
