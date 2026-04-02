# coding: utf-8
"""Tests for TeamAgent coordination lifecycle wiring."""
from __future__ import annotations

import asyncio
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.team_agent import (
    TeamAgent,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.team_events import (
    EventMessage,
    TeamEvent,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)


def _make_leader() -> TeamAgent:
    leader_member = TeamMemberSpec(
        member_id="leader-1",
        name="Leader",
        role_type=TeamRole.LEADER,
        persona="PM",
        domain="management",
    )
    team_spec = TeamSpec(
        team_id="test-team",
        name="test-team",
        leader_member_id="leader-1",
    )

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="test-team",
        leader=LeaderSpec(
            member_id="leader-1",
            name="Leader",
            persona="PM",
            domain="management",
        ),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_spec=leader_member,
        team_spec=team_spec,
    )
    agent = TeamAgent(
        AgentCard(
            id="t1", name="leader", description="test",
        ),
    )
    agent.configure(spec, context)
    return agent


def test_coordination_loop_created_on_configure():
    """configure() creates a CoordinatorLoop."""
    agent = _make_leader()
    assert agent.coordination_loop is not None
    assert agent.coordination_loop.role == TeamRole.LEADER


@pytest.mark.asyncio
async def test_start_stop_coordination():
    """_start/_stop manage the loop lifecycle."""
    agent = _make_leader()
    await agent._start_coordination(session=None)
    assert agent.coordination_loop.is_running is True
    await agent._stop_coordination()
    assert agent.coordination_loop.is_running is False


@pytest.mark.asyncio
async def test_wake_feeds_messages_to_agent():
    """When loop wakes, unread messages are fed
    to the DeepAgent via follow_up or Runner."""
    agent = _make_leader()
    agent._deep_agent.follow_up = AsyncMock()
    fake_msg = MagicMock()
    fake_msg.message_id = "msg-1"
    fake_msg.from_member = "dev-1"
    fake_msg.content = "task done"
    fake_msg.broadcast = False
    fake_msg.timestamp = 1000
    agent._message_manager = MagicMock()
    agent._dispatcher._read_all_unread = AsyncMock(
        side_effect=[[fake_msg], []],
    )
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={},
    )
    await agent.coordination_loop.enqueue(event)
    await asyncio.sleep(0.1)

    await agent._stop_coordination()
    agent._start_agent.assert_called_once()


# ------------------------------------------------------------------
# @mention direct message tests
# ------------------------------------------------------------------

def _make_leader_with_teammate() -> TeamAgent:
    """Create a leader with a mocked get_team_member for @mention tests."""
    agent = _make_leader()

    async def _has_team_member(mid: str) -> bool:
        return mid == "dev-1"

    agent.has_team_member = _has_team_member
    return agent


@pytest.mark.asyncio
async def test_mention_routes_direct_message():
    """@member_id pattern sends a direct message from 'user', bypassing leader agent."""
    agent = _make_leader_with_teammate()
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock(return_value="msg-123")
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)
    await agent.interact("@dev-1 请完成这个任务")
    await asyncio.sleep(0.1)
    await agent._stop_coordination()

    agent._message_manager.send_message.assert_called_once_with(
        "请完成这个任务", "dev-1", from_member="user",
    )
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_mention_invalid_member_falls_through():
    """@nonexistent falls through to normal leader-agent path."""
    agent = _make_leader_with_teammate()
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock()
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)
    await agent.interact("@nonexistent hello")
    await asyncio.sleep(0.1)
    await agent._stop_coordination()

    agent._message_manager.send_message.assert_not_called()
    agent._start_agent.assert_called_once()


@pytest.mark.asyncio
async def test_no_mention_normal_flow():
    """Plain message without @ goes through existing leader flow."""
    agent = _make_leader_with_teammate()
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock()
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)
    await agent.interact("普通消息")
    await asyncio.sleep(0.1)
    await agent._stop_coordination()

    agent._message_manager.send_message.assert_not_called()
    agent._start_agent.assert_called_once()


@pytest.mark.asyncio
async def test_mention_no_body_falls_through():
    """@member_id with no message body falls through (regex requires body)."""
    agent = _make_leader_with_teammate()
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock()
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)
    await agent.interact("@dev-1")
    await asyncio.sleep(0.1)
    await agent._stop_coordination()

    agent._message_manager.send_message.assert_not_called()
    agent._start_agent.assert_called_once()
