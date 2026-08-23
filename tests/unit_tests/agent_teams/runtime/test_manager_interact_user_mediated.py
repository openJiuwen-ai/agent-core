# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamRuntimeManager.interact user-mediated approve_tool routing.

Covers Task 5 of the teammate user-mediated approval sidecar: when
``spec.team_approval_mode == "user-mediated"`` and
``payload.member_name`` is a teammate (not the leader and not None),
``interact`` short-circuits before ``resume_interrupt`` and routes each
``user_inputs`` entry to ``entry.agent.team_backend.approve_tool`` with
the five-param mapping (member_name, tool_call_id, approved, feedback,
auto_confirm). leader-mediated and member_name==leader/None fall
through to ``resume_interrupt`` unchanged.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.interaction import DeliverResult
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

LEADER_NAME = "leader1"
TEAM_NAME = "team_x"
SESSION_ID = "sess_1"


def _make_manager(agent: MagicMock) -> TeamRuntimeManager:
    """Bypass __init__ and wire _resolve_entry to return ``agent``."""
    mgr = TeamRuntimeManager.__new__(TeamRuntimeManager)
    entry = SimpleNamespace(agent=agent)
    mgr._resolve_entry = AsyncMock(return_value=entry)
    return mgr


def _make_agent(team_approval_mode: str, leader_name: str | None = LEADER_NAME) -> MagicMock:
    """Build a mock TeamAgent exposing spec / member_name / team_backend."""
    agent = MagicMock()
    agent.spec = MagicMock(team_approval_mode=team_approval_mode)
    agent.member_name = leader_name
    agent.team_backend = MagicMock()
    agent.team_backend.approve_tool = AsyncMock()
    agent.resume_interrupt = AsyncMock(return_value="delivered")
    return agent


def _make_interactive_input(member_name: str | None, user_inputs: dict | None = None) -> InteractiveInput:
    inp = InteractiveInput()
    inp.member_name = member_name
    if user_inputs:
        for tcid, payload in user_inputs.items():
            inp.update(tcid, payload)
    return inp


@pytest.mark.asyncio
async def test_user_mediated_routes_teammate_to_approve_tool() -> None:
    """user-mediated + member_name != leader → approve_tool per user_inputs entry."""
    agent = _make_agent("user-mediated", leader_name=LEADER_NAME)
    mgr = _make_manager(agent)

    user_inputs = {
        "tcid_1": {"approved": True, "feedback": "ok", "auto_confirm": False},
        "tcid_2": {"approved": False, "feedback": None, "auto_confirm": True},
    }
    payload = _make_interactive_input(member_name="t1", user_inputs=user_inputs)

    result = await mgr.interact(payload, team_name=TEAM_NAME, session_id=SESSION_ID)

    assert result.ok
    assert result.message_id is None
    approve_tool = agent.team_backend.approve_tool
    assert approve_tool.await_count == 2
    assert approve_tool.await_args_list[0].kwargs == {
        "member_name": "t1",
        "tool_call_id": "tcid_1",
        "approved": True,
        "feedback": "ok",
        "auto_confirm": False,
    }
    assert approve_tool.await_args_list[1].kwargs == {
        "member_name": "t1",
        "tool_call_id": "tcid_2",
        "approved": False,
        "feedback": None,
        "auto_confirm": True,
    }
    agent.resume_interrupt.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_mediated_member_name_eq_leader_falls_through() -> None:
    """user-mediated + member_name == leader → resume_interrupt (approve_tool NOT called)."""
    agent = _make_agent("user-mediated", leader_name=LEADER_NAME)
    mgr = _make_manager(agent)

    user_inputs = {"tcid_1": {"approved": True}}
    payload = _make_interactive_input(member_name=LEADER_NAME, user_inputs=user_inputs)

    result = await mgr.interact(payload, team_name=TEAM_NAME, session_id=SESSION_ID)

    assert result.ok
    agent.team_backend.approve_tool.assert_not_awaited()
    agent.resume_interrupt.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_user_mediated_member_name_none_falls_through() -> None:
    """user-mediated + member_name is None → resume_interrupt (approve_tool NOT called)."""
    agent = _make_agent("user-mediated", leader_name=LEADER_NAME)
    mgr = _make_manager(agent)

    user_inputs = {"tcid_1": {"approved": True}}
    payload = _make_interactive_input(member_name=None, user_inputs=user_inputs)

    result = await mgr.interact(payload, team_name=TEAM_NAME, session_id=SESSION_ID)

    assert result.ok
    agent.team_backend.approve_tool.assert_not_awaited()
    agent.resume_interrupt.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_user_mediated_approve_tool_false_returns_failure() -> None:
    """approve_tool returns False → DeliverResult.failure(approve_tool_failed); resume_interrupt not called.

    approve_tool returns False only when the teammate does not exist (publish failures
    are logged inside and still return True). Surfacing it stops the relay from treating
    a dropped approval as delivered, which would leave the teammate silently hung on the
    ask. The leader-mediated (resume_interrupt) path is unaffected.
    """
    agent = _make_agent("user-mediated", leader_name=LEADER_NAME)
    agent.team_backend.approve_tool = AsyncMock(return_value=False)
    mgr = _make_manager(agent)

    user_inputs = {"tcid_1": {"approved": True, "feedback": "ok", "auto_confirm": False}}
    payload = _make_interactive_input(member_name="t1", user_inputs=user_inputs)

    result = await mgr.interact(payload, team_name=TEAM_NAME, session_id=SESSION_ID)

    assert not result.ok
    assert result.reason == "approve_tool_failed"
    agent.team_backend.approve_tool.assert_awaited_once()
    agent.resume_interrupt.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_mediated_always_resume_interrupt() -> None:
    """leader-mediated (even with teammate member_name) → resume_interrupt unchanged."""
    agent = _make_agent("leader-mediated", leader_name=LEADER_NAME)
    mgr = _make_manager(agent)

    user_inputs = {"tcid_1": {"approved": True}}
    payload = _make_interactive_input(member_name="t1", user_inputs=user_inputs)

    result = await mgr.interact(payload, team_name=TEAM_NAME, session_id=SESSION_ID)

    assert result.ok
    agent.team_backend.approve_tool.assert_not_awaited()
    agent.resume_interrupt.assert_awaited_once_with(payload)
