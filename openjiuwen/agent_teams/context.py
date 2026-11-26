# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Context Module for Agent Teams

This module provides context variable management for team isolation.
Uses contextvars to support async environments with proper context isolation.
"""

import contextvars
from contextvars import Token
from typing import Optional

from openjiuwen.core.common.logging.utils import set_session_id as _set_log_trace_id

# Context variable for session_id (used for message/topic isolation)
_session_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id",
    default=None
)

# Placeholder the core logging layer renders when no trace id is bound.
_LOG_DEFAULT_TRACE_ID = "default_trace_id"

# 请求级环境作用域（issue #1755）：workspace/environment/project，随团队 span 写入。
# 由执行入口在创建团队任务前绑定，团队后台任务继承该上下文。
_env_scope_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "agent_teams_env_scope",
    default=None
)


def set_env_scope(scope: Optional[dict]) -> None:
    """绑定请求级环境作用域（空值不覆盖既有绑定）。

    注意：本函数是**宿主（agent-studio）跨仓调用**的 API——Studio
    ``runner/controller_runner.py`` 在创建团队任务前调用它，使团队/成员 span
    带上 ``openjiuwen.workspace.id`` 等属性；Core 包内因此没有调用点，
    请勿按“无引用”删除（对应 issue 1755）。
    """
    if scope:
        _env_scope_context.set(dict(scope))


def get_env_scope() -> dict:
    """当前请求级环境作用域；未绑定时返回空 dict。"""
    return dict(_env_scope_context.get() or {})


def set_session_id(session_id: str) -> Token[str]:
    """Set the current session_id context

    The session id doubles as the trace id for logging: it is mirrored into
    the core logging trace_id contextvar so structured/text logs emitted under
    this session render the real id instead of the ``default_trace_id``
    placeholder. The message-isolation contextvar below stays the single
    source of truth for topic routing; the logging mirror is cosmetic and is
    kept in sync on reset rather than Token-tracked here.

    Args:
        session_id: Session identifier to set as current context

    Returns:
        Token that can be used to reset to previous value
    """
    _set_log_trace_id(session_id)
    return _session_id_context.set(session_id)


def get_session_id() -> Optional[str]:
    """Get the current session_id from context

    Returns:
        Current session_id or None if not set
    """
    return _session_id_context.get() or ""


def reset_session_id(token: Token[str]) -> None:
    """Reset session_id context to previous value

    Restores the logging trace_id mirror to match the now-current session id
    so a torn-down session does not leak its trace id into later logs on the
    same context.

    Args:
        token: Token returned from set_session_id()
    """
    _session_id_context.reset(token)
    restored = _session_id_context.get()
    _set_log_trace_id(restored if restored else _LOG_DEFAULT_TRACE_ID)
