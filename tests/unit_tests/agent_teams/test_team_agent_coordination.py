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
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberStatusChangedEvent,
    MessageEvent,
    ToolApprovalResultEvent,
)
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)


def _make_leader() -> TeamAgent:
    team_spec = TeamSpec(
        team_name="test-team",
        display_name="test-team",
        leader_member_name="leader-1",
    )

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="test-team",
        leader=LeaderSpec(
            member_name="leader-1",
            display_name="Leader",
            persona="PM",
        ),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        persona="PM",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
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
    fake_msg.from_member_name = "dev-1"
    fake_msg.content = "task done"
    fake_msg.broadcast = False
    fake_msg.timestamp = 1000
    agent._message_manager = MagicMock()
    agent._message_manager.mark_message_read = AsyncMock(return_value=True)
    agent._dispatcher._read_all_unread = AsyncMock(
        side_effect=[[fake_msg], []],
    )
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)

    event = EventMessage.from_event(MessageEvent(
        team_name="test-team",
        message_id="msg-1",
        from_member_name="dev-1",
        to_member_name="leader-1",
    ))
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
    team_spec = TeamSpec(
        team_name="test-team",
        display_name="test-team",
        leader_member_name="leader-1",
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="test-team",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        persona="dev",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
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
        "请完成这个任务", "dev-1", from_member_name="user",
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
    team_spec = TeamSpec(
        team_name="test-team",
        display_name="test-team",
        leader_member_name="leader-1")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="test-team")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE, member_name="dev-1", persona="dev", team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    agent.resume_interrupt = AsyncMock()

    event = EventMessage.from_event(ToolApprovalResultEvent(
        team_name="test-team",
        member_name="dev-1",
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
    fake_msg.from_member_name = "dev-2"
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
    agent._deep_agent._loop_session = MagicMock()
    agent._deep_agent._loop_session.get_state = MagicMock(return_value=fake_state)
    agent._agent_task = MagicMock()
    agent._agent_task.done.return_value = False
    agent._start_agent = AsyncMock()

    interactive_input = InteractiveInput()
    interactive_input.update("call-1", {"approved": True, "feedback": "ok", "auto_confirm": False})

    await agent.resume_interrupt(interactive_input)

    assert agent._pending_interrupt_resumes == [interactive_input]
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_member_ready_with_claimed_task_triggers_nudge():
    """Leader nudges a member returning to READY while still holding a claimed task."""
    agent = _make_leader()
    fake_task = MagicMock()
    fake_task.task_id = "task-1"
    fake_task.title = "Fix bug"
    fake_task.content = "Investigate and fix the critical bug"

    agent._task_manager = MagicMock()
    agent._task_manager.get_tasks_by_assignee = AsyncMock(return_value=[fake_task])
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock(return_value="msg-1")

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="busy",
        new_status="ready",
    ))
    await agent._dispatcher._handle_leader_member_event(event)

    agent._task_manager.get_tasks_by_assignee.assert_awaited_once_with(
        "dev-1", status="claimed",
    )
    agent._message_manager.send_message.assert_awaited_once()
    content, to_member_name = agent._message_manager.send_message.await_args.args
    assert to_member_name == "dev-1"
    assert "task-1" in content
    assert "Fix bug" in content


@pytest.mark.asyncio
async def test_member_error_with_claimed_task_triggers_nudge():
    """Leader also nudges on transition into ERROR when claimed tasks remain."""
    agent = _make_leader()
    fake_task = MagicMock()
    fake_task.task_id = "task-2"
    fake_task.title = "Ship feature"
    fake_task.content = "Wrap up the pending PR"

    agent._task_manager = MagicMock()
    agent._task_manager.get_tasks_by_assignee = AsyncMock(return_value=[fake_task])
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock(return_value="msg-2")

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="busy",
        new_status="error",
    ))
    await agent._dispatcher._handle_leader_member_event(event)

    agent._message_manager.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_member_ready_without_claimed_task_skips_nudge():
    """No message is sent when the member has no claimed tasks."""
    agent = _make_leader()
    agent._task_manager = MagicMock()
    agent._task_manager.get_tasks_by_assignee = AsyncMock(return_value=[])
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="busy",
        new_status="ready",
    ))
    await agent._dispatcher._handle_leader_member_event(event)

    agent._message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_member_status_unchanged_skips_nudge():
    """Redundant READY → READY transitions should not query tasks nor send messages."""
    agent = _make_leader()
    agent._task_manager = MagicMock()
    agent._task_manager.get_tasks_by_assignee = AsyncMock()
    agent._message_manager = MagicMock()
    agent._message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="ready",
        new_status="ready",
    ))
    await agent._dispatcher._handle_leader_member_event(event)

    agent._task_manager.get_tasks_by_assignee.assert_not_called()
    agent._message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_teammate_round_completion_wakes_mailbox_after_interrupt_clears():
    """Deferred mailbox messages should be retried immediately after interrupt clears."""
    agent = _make_teammate()
    # _make_teammate wires a real TeamBackend (with a default sqlite DB path)
    # during configure(). _update_status would then hit the DB on BUSY/READY
    # transitions -- skip it by detaching _team_member for this unit test,
    # which is scoped to the mailbox-wake behavior in _run_one_round.
    agent._team_member = None
    agent._coordination_loop.enqueue = AsyncMock()
    agent._execute_round = AsyncMock(return_value=None)
    agent.has_pending_interrupt = lambda: False

    await agent._run_one_round("continue work")

    agent._coordination_loop.enqueue.assert_awaited_once()
    event = agent._coordination_loop.enqueue.await_args.args[0]
    assert event.event_type == InnerEventType.POLL_MAILBOX
