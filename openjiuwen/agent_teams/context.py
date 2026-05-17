# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Context Module for Agent Teams

This module provides context variable management for team isolation.
Uses contextvars to support async environments with proper context isolation.
"""

import contextvars
from contextvars import Token
from typing import Optional

# Context variable for session_id (used for message/topic isolation)
_session_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id",
    default=None
)


def set_session_id(session_id: str) -> Token[str]:
    """Set the current session_id context

    Args:
        session_id: Session identifier to set as current context

    Returns:
        Token that can be used to reset to previous value
    """
    return _session_id_context.set(session_id)


def get_session_id() -> Optional[str]:
    """Get the current session_id from context

    Returns:
        Current session_id or None if not set
    """
    return _session_id_context.get() or ""


def reset_session_id(token: Token[str]) -> None:
    """Reset session_id context to previous value

    Args:
        token: Token returned from set_session_id()
    """
    _session_id_context.reset(token)
