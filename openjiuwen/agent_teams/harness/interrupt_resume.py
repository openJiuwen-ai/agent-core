# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal interrupt-resume matching helpers for the harness runtime."""
from __future__ import annotations

from typing import Any, Literal

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptionState,
)

InterruptResumeKind = Literal["none", "tool", "workflow"]


def interrupt_resume_kind(content: Any, session: Any) -> InterruptResumeKind:
    """Classify a structured input from the exact interrupt state at admission."""
    if not isinstance(content, InteractiveInput) or session is None:
        return "none"
    state = session.get_state(INTERRUPTION_KEY)
    if isinstance(state, ToolInterruptionState):
        return "tool"

    # Keep tool-only imports light; the workflow state lives in ReActAgent.
    from openjiuwen.core.single_agent.agents.react_agent import InterruptionState

    if isinstance(state, InterruptionState):
        return "workflow"
    return "none"


def pending_tool_resume_ids(session: Any) -> frozenset[str]:
    """Return tool request IDs currently awaiting a structured resume."""
    if session is None:
        return frozenset()
    state = session.get_state(INTERRUPTION_KEY)
    interrupted = getattr(state, "interrupted_tools", {}) or {}
    pending_ids: set[str] = set()
    for entry in interrupted.values():
        requests = getattr(entry, "interrupt_requests", {}) or {}
        pending_ids.update(requests)
    return frozenset(pending_ids)


def tool_resume_scope_ids(content: Any, session: Any) -> frozenset[str]:
    """Freeze the full pending tool scope for a valid tool-resume input."""
    if not isinstance(content, InteractiveInput):
        return frozenset()
    resume_ids = set(content.user_inputs)
    pending_ids = pending_tool_resume_ids(session)
    if resume_ids and resume_ids.issubset(pending_ids):
        return pending_ids
    return frozenset()
