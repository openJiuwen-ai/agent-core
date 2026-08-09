# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve SubagentControl instances scoped to a parent DeepAgent session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.control import SubagentControl

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

_CONTROL_ATTR = "_subagent_controls"


def get_subagent_control(parent_agent: "DeepAgent", session: Any) -> SubagentControl:
    """Return or create the SubagentControl for a parent session."""
    if not isinstance(session, Session):
        raise build_error(
            StatusCode.TOOL_SESSION_TOOL_INVOKED,
            reason="subagent tools require a valid session in kwargs",
        )
    parent_session_id = session.get_session_id()
    controls = getattr(parent_agent, _CONTROL_ATTR, None)
    if controls is None:
        controls = {}
        setattr(parent_agent, _CONTROL_ATTR, controls)
    control = controls.get(parent_session_id)
    if control is None:
        control = SubagentControl(parent_agent, parent_session_id, parent_session=session)
        control.hydrate()
        controls[parent_session_id] = control
    return control


async def release_subagent_control(
    parent_agent: Any,
    parent_session_id: str,
    reason: str = "parent_ended",
) -> None:
    """Cancel all subagents and drop the cached control for a parent session."""
    controls = getattr(parent_agent, _CONTROL_ATTR, None) or {}
    control = controls.pop(parent_session_id, None)
    if control is not None:
        await control.cancel_all(reason)
        control.flush()


async def release_all_subagent_controls(
    parent_agent: Any,
    reason: str = "rail_uninit",
) -> None:
    """Cancel all subagents for every cached parent session on an agent."""
    controls = getattr(parent_agent, _CONTROL_ATTR, None) or {}
    session_ids = list(controls.keys())
    for parent_session_id in session_ids:
        await release_subagent_control(parent_agent, parent_session_id, reason=reason)


__all__ = [
    "_CONTROL_ATTR",
    "get_subagent_control",
    "release_all_subagent_controls",
    "release_subagent_control",
]
