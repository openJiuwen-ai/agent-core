# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify tool-interrupt resumes without coupling ReActAgent to agent_teams.

Permission / confirm interrupts must only resume from a matching
``InteractiveInput``. Plain text such as 「继续」 is not an approval.
Ask-user and SkillTurbo interrupts are out of scope.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptionState


_SKIP_CONFIRM_TOOL_NAMES = frozenset({"ask_user", "skill_acceleration_exec"})


def pending_tool_resume_ids(state: ToolInterruptionState) -> frozenset[str]:
    """Return outer and inner ids that a structured resume may address."""
    pending: set[str] = set()
    for outer_id, entry in state.interrupted_tools.items():
        pending.add(outer_id)
        pending.update(entry.interrupt_requests.keys())
    return frozenset(pending)


def is_confirm_payload_interrupt(state: Any) -> bool:
    """Return whether any pending tool interrupt is a confirm/permission card.

    Detected from ``InterruptRequest.payload_schema``: ConfirmPayload exposes
    ``approved`` and does not expose ``answers`` (AskUserPayload).
    """
    if not isinstance(state, ToolInterruptionState):
        return False
    for entry in state.interrupted_tools.values():
        for request in entry.interrupt_requests.values():
            if _is_confirm_payload_schema(getattr(request, "payload_schema", None)):
                return True
    return False


def is_pure_permission_confirm_interrupt(state: Any) -> bool:
    """Return whether ``state`` is permission HITL and nothing else.

    Mixed interrupts, ``ask_user``, and SkillTurbo are left on the existing
    resume path so a leftover confirm cannot swallow those answers.
    """
    if not isinstance(state, ToolInterruptionState):
        return False
    interrupted = getattr(state, "interrupted_tools", None)
    if not isinstance(interrupted, dict) or not interrupted:
        return False

    found_confirm = False
    for entry in interrupted.values():
        tool_name = _tool_call_name(getattr(entry, "tool_call", None))
        if tool_name in _SKIP_CONFIRM_TOOL_NAMES:
            return False
        requests = getattr(entry, "interrupt_requests", None)
        if not isinstance(requests, dict) or not requests:
            return False
        for request in requests.values():
            if not _is_confirm_payload_schema(getattr(request, "payload_schema", None)):
                return False
            found_confirm = True
    return found_confirm


def is_matching_tool_resume(user_input: Any, state: Any) -> bool:
    """Return whether ``user_input`` is an InteractiveInput for this interrupt."""
    if not isinstance(user_input, InteractiveInput):
        return False
    if not isinstance(state, ToolInterruptionState):
        return False
    resume_ids = frozenset(user_input.user_inputs)
    pending_ids = pending_tool_resume_ids(state)
    return bool(resume_ids) and resume_ids.issubset(pending_ids)


def should_abandon_unmatched_confirm_resume(user_input: Any, state: Any) -> bool:
    """Return whether a leftover confirm should be closed as a new query.

    Plain text such as 「继续」 is not an approval and must not re-emit ASK.
    A structured ``InteractiveInput`` — including empty or wrong-id payloads —
    stays on ``handle_resume`` so the card can be retried.
    """
    if not is_pure_permission_confirm_interrupt(state):
        return False
    if is_matching_tool_resume(user_input, state):
        return False
    return not isinstance(user_input, InteractiveInput)


def _is_confirm_payload_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    return "approved" in properties and "answers" not in properties


def _tool_call_name(tool_call: Any) -> str:
    if tool_call is None:
        return ""
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or tool_call.get("name") or "")
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")
