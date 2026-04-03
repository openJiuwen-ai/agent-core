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
from openjiuwen.agent_teams.agent.coordinator import InnerEventType
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
    ToolApprovalResultEvent,
)
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
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


def _make_teammate() -> TeamAgent:
    member_spec = TeamMemberSpec(
        member_id="dev-1",
        name="Dev",
        role_type=TeamRole.TEAMMATE,
        persona="dev",
        domain="backend",
    )
    team_spec = TeamSpec(
        team_id="test-team",
        name="test-team",
        leader_member_id="leader-1",
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="test-team",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_spec=member_spec,
        team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
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


@pytest.mark.asyncio
async def test_tool_approval_event_resumes_interrupt():
    """Tool approval result event should resume teammate HITL with InteractiveInput."""
    member_spec = TeamMemberSpec(
        member_id="dev-1",
        name="Dev",
        role_type=TeamRole.TEAMMATE,
        persona="dev",
        domain="backend",
    )
    team_spec = TeamSpec(team_id="test-team", name="test-team", leader_member_id="leader-1")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="test-team")
    ctx = TeamRuntimeContext(role=TeamRole.TEAMMATE, member_spec=member_spec, team_spec=team_spec)
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    agent.resume_interrupt = AsyncMock()

    event = EventMessage.from_event(ToolApprovalResultEvent(
        team_id="test-team",
        member_id="dev-1",
        tool_call_id="call-1",
        approved=True,
        feedback="ok",
        auto_confirm=True,
    ))
    await agent._dispatcher.dispatch(event)

    agent.resume_interrupt.assert_awaited_once()
    interactive_input = agent.resume_interrupt.await_args.args[0]
    assert interactive_input.user_inputs["call-1"]["approved"] is True
    assert interactive_input.user_inputs["call-1"]["feedback"] == "ok"
    assert interactive_input.user_inputs["call-1"]["auto_confirm"] is True


@pytest.mark.asyncio
async def test_mailbox_messages_deferred_while_interrupt_pending():
    """Normal mailbox messages should not preempt a pending tool interrupt."""
    agent = _make_leader_with_teammate()
    agent._message_manager = MagicMock()
    agent._message_manager.mark_message_read = AsyncMock(return_value=True)
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()
    agent.follow_up = AsyncMock()
    agent.has_pending_interrupt = lambda: True

    fake_msg = MagicMock()
    fake_msg.message_id = "msg-normal"
    fake_msg.from_member = "dev-2"
    fake_msg.broadcast = False
    fake_msg.timestamp = 1000
    fake_msg.content = "normal mailbox message"
    agent._dispatcher._read_all_unread = AsyncMock(side_effect=[[fake_msg]])

    await agent._dispatcher._process_unread_messages("leader-1")

    agent._message_manager.mark_message_read.assert_not_called()
    agent._start_agent.assert_not_called()
    agent.steer.assert_not_called()
    agent.follow_up.assert_not_called()


@pytest.mark.asyncio
async def test_resume_interrupt_queues_while_agent_running():
    """Approval resume should queue when teammate is already running another round."""
    agent = _make_leader()
    fake_entry = MagicMock()
    fake_entry.interrupt_requests = {"call-1": MagicMock()}
    fake_state = MagicMock()
    fake_state.interrupted_tools = {"call-1": fake_entry}
    agent._session = MagicMock()
    agent._session.get_state = MagicMock(return_value=fake_state)
    agent._agent_task = MagicMock()
    agent._agent_task.done.return_value = False
    agent._start_agent = AsyncMock()

    interactive_input = InteractiveInput()
    interactive_input.update("call-1", {"approved": True, "feedback": "ok", "auto_confirm": False})

    await agent.resume_interrupt(interactive_input)

    assert agent._pending_interrupt_resumes == [interactive_input]
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_teammate_round_completion_wakes_mailbox_after_interrupt_clears():
    """Deferred mailbox messages should be retried immediately after interrupt clears."""
    agent = _make_teammate()
    agent._coordination_loop.enqueue = AsyncMock()
    agent._execute_round = AsyncMock(return_value=None)
    agent.has_pending_interrupt = lambda: False

    await agent._run_one_round("continue work", session=None)

    agent._coordination_loop.enqueue.assert_awaited_once()
    event = agent._coordination_loop.enqueue.await_args.args[0]
    assert event.event_type == InnerEventType.POLL_MAILBOX
