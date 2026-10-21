# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-scoped agent identity carried in the execution context.

``Session`` binds the card identity (agent id / name / description) into a
``ContextVar`` at creation time. Every runner passes a card when creating
the session (single-agent passes the agent card, multi-agent the group
card), so this is the one core-side point all execution modes share —
including orchestrated paths whose agents never register rails or fire
core agent callbacks.

Telemetry handlers (``tracer_otel``) read this as a fallback identity
source for spans whose event payload carries no identity keys. Context
semantics do the propagation: ``asyncio.create_task`` copies the current
context, so member-execution tasks spawned after session creation inherit
the identity; each request builds its own session in its own task, so
concurrent requests never cross-contaminate.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional

_current_agent_identity: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "openjiuwen_current_agent_identity", default=None
)


def set_current_agent_identity(
    agent_id: Any,
    agent_name: Any = None,
    agent_description: Any = None,
) -> Optional[Token]:
    """Bind agent identity to the current execution context.

    Args:
        agent_id: Card id — the anchor; without it nothing is bound.
        agent_name: Card name (optional).
        agent_description: Card description (optional).

    Returns:
        Reset token, or ``None`` when ``agent_id`` is empty and no
        binding happened. Pass the token to
        :func:`reset_current_agent_identity` to restore the previous
        value; ordinary request flows never need to — the task teardown
        drops the whole context.
    """
    if not agent_id:
        return None
    return _current_agent_identity.set(
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_description": agent_description,
        }
    )


def reset_current_agent_identity(token: Token) -> None:
    """Restore the identity bound before the matching ``set`` call."""
    _current_agent_identity.reset(token)


def clear_current_agent_identity() -> None:
    """Unbind the ambient identity (back to the no-identity state).

    ``set_current_agent_identity`` never overwrites with an empty id, so
    this is the explicit way to drop a binding — test isolation uses it;
    ordinary request flows never need to.
    """
    _current_agent_identity.set(None)


def current_agent_identity() -> Optional[Dict[str, Any]]:
    """Snapshot the ambient identity, or ``None`` when none is bound.

    Callers must treat the returned dict as read-only.
    """
    return _current_agent_identity.get()
