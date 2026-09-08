# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal matching helpers for structured interrupt resume inputs."""
from __future__ import annotations

from typing import Any

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptionState,
)


def pending_tool_resume_ids(state: Any) -> frozenset[str]:
    """Return tool request IDs that are still awaiting input in ``state``."""
    if not isinstance(state, ToolInterruptionState):
        return frozenset()
    pending_ids: set[str] = set()
    for entry in state.interrupted_tools.values():
        pending_ids.update(entry.interrupt_requests)
    return frozenset(pending_ids)


def matches_pending_interrupt(content: Any, state: Any) -> bool:
    """Return whether a structured input addresses the pending tool interrupt."""
    if not isinstance(content, InteractiveInput):
        return False
    resume_ids = frozenset(content.user_inputs)
    pending_ids = pending_tool_resume_ids(state)
    return bool(resume_ids) and resume_ids.issubset(pending_ids)


def tool_resume_scope_ids(content: Any, session: Any) -> frozenset[str]:
    """Freeze the full tool scope when a valid structured input is admitted."""
    if session is None:
        return frozenset()
    state = session.get_state(INTERRUPTION_KEY)
    if not matches_pending_interrupt(content, state):
        return frozenset()
    return pending_tool_resume_ids(state)
