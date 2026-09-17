# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal matching helpers for structured interrupt resume inputs."""
from __future__ import annotations

from typing import Any

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import InterruptionState
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptionState,
)


def build_approval_interactive_input(
    tool_call_id: Any,
    *,
    approved: Any,
    feedback: str = "",
    auto_confirm: bool = False,
) -> InteractiveInput | None:
    """Build the HITL resume payload for one leader tool-approval decision.

    Event-bus and mailbox delivery must construct the same ``InteractiveInput``
    shape; a raw dict does not match a pending interrupt. Returns ``None``
    when ``tool_call_id`` is missing or not a non-empty string — callers must
    not invent aliases or coerce other keys.
    """
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return None
    interactive_input = InteractiveInput()
    interactive_input.update(
        tool_call_id,
        {
            "approved": approved,
            "feedback": feedback or "",
            "auto_confirm": auto_confirm,
        },
    )
    return interactive_input


def pending_tool_resume_ids(state: Any) -> frozenset[str]:
    """Return tool request IDs that are still awaiting input in ``state``."""
    if not isinstance(state, ToolInterruptionState):
        return frozenset()
    pending_ids: set[str] = set()
    for entry in state.interrupted_tools.values():
        pending_ids.update(entry.interrupt_requests)
    return frozenset(pending_ids)


def matches_pending_interrupt(content: Any, state: Any) -> bool:
    """Return whether a structured input addresses the current interrupt."""
    if not isinstance(content, InteractiveInput):
        return False
    if isinstance(state, ToolInterruptionState):
        resume_ids = frozenset(content.user_inputs)
        pending_ids = pending_tool_resume_ids(state)
        return bool(resume_ids) and resume_ids.issubset(pending_ids)
    if isinstance(state, InterruptionState):
        return content.raw_inputs is not None or state.pending_component_id in content.user_inputs
    return False


def workflow_resume_slot(content: Any, state: Any) -> tuple[str, str] | None:
    """Bind a workflow reply to the workflow slot pending at admission."""
    if isinstance(state, InterruptionState) and matches_pending_interrupt(content, state):
        return state.pending_workflow_id, state.pending_component_id
    return None


def matches_admitted_interrupt(
    content: Any,
    state: Any,
    admitted_workflow_slot: tuple[str, str] | None,
) -> bool:
    """Match queued input without retargeting a workflow reply."""
    if admitted_workflow_slot is not None:
        return (
            isinstance(state, InterruptionState)
            and (
                state.pending_workflow_id,
                state.pending_component_id,
            ) == admitted_workflow_slot
            and matches_pending_interrupt(content, state)
        )
    if isinstance(content, InteractiveInput) and content.raw_inputs is not None:
        return False
    return matches_pending_interrupt(content, state)


def tool_resume_scope_ids(content: Any, session: Any) -> frozenset[str]:
    """Freeze the full tool scope when a valid structured input is admitted."""
    if session is None:
        return frozenset()
    state = session.get_state(INTERRUPTION_KEY)
    if not matches_pending_interrupt(content, state):
        return frozenset()
    return pending_tool_resume_ids(state)
