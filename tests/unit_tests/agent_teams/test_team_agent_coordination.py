# coding: utf-8
"""Tests for TeamAgent coordination lifecycle wiring."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.team_agent import (
    TeamAgent,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberStatusChangedEvent,
    MessageEvent,
    TaskClaimedEvent,
    TaskListDrainedEvent,
    TeamCleanedEvent,
    TeamCompletedEvent,
    TeamEvent,
    ToolApprovalResultEvent,
)
from openjiuwen.agent_teams.schema.team import (
    TeamCompletionSnapshot,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig
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
            id="t1",
            name="leader",
            description="test",
        ),
    )
    agent.configure(spec, context)
    return agent


@pytest.mark.level0
def test_coordination_loop_created_on_configure():
    """configure() creates a EventBus."""
    agent = _make_leader()
    assert agent.coordination_loop is not None
    assert agent.coordination_loop.role == TeamRole.LEADER


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_stop_coordination():
    """_start/_stop manage the loop lifecycle."""
    agent = _make_leader()
    await agent._start_coordination(session=None)
    assert agent.coordination_loop.is_running is True
    await agent._stop_coordination()
    assert agent.coordination_loop.is_running is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_wake_feeds_messages_to_agent():
    """When loop wakes, unread messages are fed
    to the DeepAgent via follow_up or Runner."""
    agent = _make_leader()
    agent._configurator.harness.inner_agent.follow_up = AsyncMock()
    fake_msg = MagicMock()
    fake_msg.message_id = "msg-1"
    fake_msg.from_member_name = "dev-1"
    fake_msg.content = "task done"
    fake_msg.broadcast = False
    fake_msg.timestamp = 1000
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.mark_message_read = AsyncMock(return_value=True)
    agent._coordination.dispatcher.message._read_all_unread = AsyncMock(
        side_effect=[[fake_msg], []],
    )
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._start_coordination(session=None)

    event = EventMessage.from_event(
        MessageEvent(
            team_name="test-team",
            message_id="msg-1",
            from_member_name="dev-1",
            to_member_name="leader-1",
        )
    )
    await agent.coordination_loop.enqueue(event)
    await asyncio.sleep(0.1)

    await agent._stop_coordination()
    agent._start_agent.assert_called_once()


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
@pytest.mark.level0
async def test_human_agent_inbound_callback_fires_on_message_event():
    """Leader-side dispatcher must forward team→human_agent messages
    to the registered ``on_inbound`` callback so the SDK can deliver
    them to the external user."""
    agent = _make_leader()

    # Register a human-agent member name on the live backend so
    # ``is_human_agent`` recognises the recipient.
    agent.team_backend._human_agent_names.add("human_alice")

    received: list = []

    async def cb(evt):
        received.append(evt)

    agent.team_backend.register_human_agent_inbound("human_alice", cb)

    # Mock the message DB lookup the dispatcher does to fetch the body.
    fake_row = MagicMock()
    fake_row.content = "leader pinging the user"
    fake_row.timestamp = 12345
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.db.message.get_message = AsyncMock(return_value=fake_row)

    await agent._start_coordination(session=None)

    event = EventMessage.from_event(
        MessageEvent(
            team_name="test-team",
            message_id="msg-99",
            from_member_name="dev-1",
            to_member_name="human_alice",
        )
    )
    await agent.coordination_loop.enqueue(event)
    await asyncio.sleep(0.1)
    await agent._stop_coordination()

    assert len(received) == 1
    evt = received[0]
    assert evt.member_name == "human_alice"
    assert evt.sender == "dev-1"
    assert evt.body == "leader pinging the user"
    assert evt.broadcast is False
    assert evt.message_id == "msg-99"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_tool_approval_event_resumes_interrupt():
    """Tool approval result event should resume teammate HITL with InteractiveInput."""
    team_spec = TeamSpec(
        team_name="test-team",
        display_name="test-team",
        leader_member_name="leader-1",
    )
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="test-team")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        persona="dev",
        team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    agent.resume_interrupt = AsyncMock()

    event = EventMessage.from_event(
        ToolApprovalResultEvent(
            team_name="test-team",
            member_name="dev-1",
            tool_call_id="call-1",
            approved=True,
            feedback="ok",
            auto_confirm=True,
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    agent.resume_interrupt.assert_awaited_once()
    interactive_input = agent.resume_interrupt.await_args.args[0]
    assert interactive_input.user_inputs["call-1"]["approved"] is True
    assert interactive_input.user_inputs["call-1"]["feedback"] == "ok"
    assert interactive_input.user_inputs["call-1"]["auto_confirm"] is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mailbox_messages_deferred_while_interrupt_pending():
    """Normal mailbox messages should not preempt a pending tool interrupt."""
    agent = _make_leader()
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.mark_message_read = AsyncMock(return_value=True)
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
    agent._coordination.dispatcher.message._read_all_unread = AsyncMock(side_effect=[[fake_msg]])

    await agent._coordination.dispatcher.message._process_unread_messages("leader-1")

    agent._configurator.message_manager.mark_message_read.assert_not_called()
    agent._start_agent.assert_not_called()
    agent.steer.assert_not_called()
    agent.follow_up.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_interrupt_queues_while_agent_running():
    """Approval resume should queue when teammate is already running another round."""
    agent = _make_leader()
    fake_entry = MagicMock()
    fake_entry.interrupt_requests = {"call-1": MagicMock()}
    fake_state = MagicMock()
    fake_state.interrupted_tools = {"call-1": fake_entry}
    agent._configurator.harness.inner_agent._loop_session = MagicMock()
    agent._configurator.harness.inner_agent._loop_session.get_state = MagicMock(return_value=fake_state)
    agent._stream_controller.agent_task = MagicMock()
    agent._stream_controller.agent_task.done.return_value = False
    agent._start_agent = AsyncMock()

    interactive_input = InteractiveInput()
    interactive_input.update("call-1", {"approved": True, "feedback": "ok", "auto_confirm": False})

    await agent.resume_interrupt(interactive_input)

    assert agent._stream_controller.pending_interrupt_resumes == [interactive_input]
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_ready_with_claimed_task_triggers_nudge():
    """Leader nudges a member returning to READY while still holding a claimed task."""
    agent = _make_leader()
    fake_task = MagicMock()
    fake_task.task_id = "task-1"
    fake_task.title = "Fix bug"
    fake_task.content = "Investigate and fix the critical bug"
    fake_task.updated_at = 0  # stale → triggers nudge path

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get_tasks_by_assignee = AsyncMock(return_value=[fake_task])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock(return_value="msg-1")

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-1",
            old_status="busy",
            new_status="ready",
        )
    )
    await agent._coordination.dispatcher.member._handle_leader_member_event(event)

    agent._configurator.task_manager.get_tasks_by_assignee.assert_awaited_once_with(
        "dev-1",
        status="claimed",
    )
    agent._configurator.message_manager.send_message.assert_awaited_once()
    content, to_member_name = agent._configurator.message_manager.send_message.await_args.args
    assert to_member_name == "dev-1"
    assert "task-1" in content
    assert "Fix bug" in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_error_with_claimed_task_triggers_nudge():
    """Leader also nudges on transition into ERROR when claimed tasks remain."""
    agent = _make_leader()
    fake_task = MagicMock()
    fake_task.task_id = "task-2"
    fake_task.title = "Ship feature"
    fake_task.content = "Wrap up the pending PR"
    fake_task.updated_at = 0  # stale → triggers nudge path

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get_tasks_by_assignee = AsyncMock(return_value=[fake_task])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock(return_value="msg-2")

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-1",
            old_status="busy",
            new_status="error",
        )
    )
    await agent._coordination.dispatcher.member._handle_leader_member_event(event)

    agent._configurator.message_manager.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_ready_without_claimed_task_skips_nudge():
    """No message is sent when the member has no claimed tasks."""
    agent = _make_leader()
    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get_tasks_by_assignee = AsyncMock(return_value=[])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-1",
            old_status="busy",
            new_status="ready",
        )
    )
    await agent._coordination.dispatcher.member._handle_leader_member_event(event)

    agent._configurator.message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_status_unchanged_skips_nudge():
    """Redundant READY → READY transitions should not query tasks nor send messages."""
    agent = _make_leader()
    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get_tasks_by_assignee = AsyncMock()
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-1",
            old_status="ready",
            new_status="ready",
        )
    )
    await agent._coordination.dispatcher.member._handle_leader_member_event(event)

    agent._configurator.task_manager.get_tasks_by_assignee.assert_not_called()
    agent._configurator.message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_claimed_for_other_member_falls_through_to_board_nudge():
    """An idle leader observing a teammate claim is nudged with the updated board.

    TASK_CLAIMED carries ``member_name=<assignee>``; when it is not the
    local member, the handler must still feed the current board into
    the local agent so an idle leader does not miss the change until
    the next stale-pending poll.
    """
    agent = _make_leader()
    incomplete_task = MagicMock()
    incomplete_task.task_id = "task-7"
    incomplete_task.title = "Investigate crash"
    incomplete_task.content = "Reproduce and root-cause"
    incomplete_task.status = "claimed"
    incomplete_task.assignee = "dev-1"

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[incomplete_task])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="dev-1",
            task_id="task-7",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.list_tasks.assert_awaited_once_with()
    agent._start_agent.assert_awaited_once()
    agent.steer.assert_not_called()
    content = agent._start_agent.await_args.args[0]
    assert "task-7" in content
    assert "Investigate crash" in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_claimed_for_other_member_skipped_when_round_in_flight():
    """A leader already running a round must not be nudged on a teammate claim."""
    agent = _make_leader()
    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock()

    in_flight = MagicMock()
    in_flight.done = MagicMock(return_value=False)
    agent._stream_controller.agent_task = in_flight
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="dev-1",
            task_id="task-8",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.list_tasks.assert_not_called()
    agent._start_agent.assert_not_called()
    agent.steer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_claimed_for_self_uses_teammate_template():
    """Self-claim by a regular member keeps the teammate-style prompt.

    Regression guard for the role-aware branch in ``on_task_claimed``:
    when ``is_human_agent(member_name)`` is False, the assignee sees the
    existing ``[任务指派]`` text that urges them to call ``view_task`` and
    work on the task autonomously.
    """
    agent = _make_leader()
    # Make sure the leader is NOT registered as a human-agent member —
    # the default after _make_leader is an empty roster, but assert it
    # explicitly so the test does not silently drift.
    assert "leader-1" not in agent.team_backend.human_agent_names()

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(return_value=MagicMock(title="Fix bug"))
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="leader-1",
            task_id="task-42",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._start_agent.assert_awaited_once()
    content = agent._start_agent.await_args.args[0]
    assert "[任务指派]" in content
    assert "task-42" in content
    # Teammate prompt steers toward autonomous execution via view_task.
    assert "view_task" in content
    # Must not pick the controller-facing HITT variant.
    assert "控制者" not in content


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_claimed_for_self_uses_human_template_when_human_agent():
    """Self-claim by a human-agent avatar renders the controller-facing prompt.

    When the current member is registered as a human-agent, the
    self-assignment branch must use ``hitt.task_assigned_to_self_human``
    so the avatar LLM frames the event as a notification for its
    controller (not as a self-execution prompt). The task title is
    inlined so the controller sees what was assigned without a separate
    ``view_task`` round-trip.
    """
    agent = _make_leader()
    # Register the leader's member_name as a human-agent member. The
    # role check in TaskBoardHandler only consults
    # ``backend.is_human_agent`` — TeamRole itself is not gated, so
    # piggy-backing on the leader fixture keeps the test small.
    agent.team_backend._human_agent_names.add("leader-1")

    task_row = MagicMock()
    task_row.title = "Write design doc"
    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(return_value=task_row)
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="leader-1",
            task_id="task-7",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.get.assert_awaited_once_with("task-7")
    agent._start_agent.assert_awaited_once()
    content = agent._start_agent.await_args.args[0]
    # Controller-facing HITT prefix and inlined title.
    assert "[任务指派给控制者]" in content
    assert "task-7" in content
    assert "Write design doc" in content
    # Must not show the teammate guidance to autonomously call view_task.
    assert "view_task" not in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_claimed_for_human_self_swallows_title_lookup_error():
    """Title lookup failure must not break the dispatch loop.

    ``task_manager.get`` raising is logged + swallowed by the handler;
    the prompt still goes out with an empty title placeholder so the
    avatar still gets notified.
    """
    agent = _make_leader()
    agent.team_backend._human_agent_names.add("leader-1")

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(side_effect=RuntimeError("db down"))
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="leader-1",
            task_id="task-9",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._start_agent.assert_awaited_once()
    content = agent._start_agent.await_args.args[0]
    assert "[任务指派给控制者]" in content
    assert "task-9" in content


@pytest.mark.level0
def test_format_message_uses_teammate_template_when_not_human():
    """Default rendering keeps the teammate-style ``dispatcher.msg_received``."""
    agent = _make_leader()
    handler = agent._coordination.dispatcher.message

    msg = MagicMock()
    msg.message_id = "msg-1"
    msg.from_member_name = "dev-1"
    msg.content = "ping"
    msg.broadcast = False

    text = handler._format_message(msg, is_human_agent=False)
    assert "msg-1" in text
    assert "dev-1" in text
    assert "ping" in text
    # Teammate template carries the autonomous-reply guidance.
    assert "send_message" in text
    # And must not have leaked the controller-facing wording.
    assert "控制者" not in text


@pytest.mark.level0
def test_format_message_uses_human_template_when_human_agent():
    """HITT rendering frames the message as a for-controller notification.

    Distinguishes direct vs broadcast through the ``msg_type`` field and
    embeds the autonomy-suppressing tip so the avatar LLM does not
    auto-trigger ``send_message`` on team-side messages.
    """
    agent = _make_leader()
    handler = agent._coordination.dispatcher.message

    direct = MagicMock()
    direct.message_id = "msg-direct"
    direct.from_member_name = "leader"
    direct.content = "are you around?"
    direct.broadcast = False

    direct_text = handler._format_message(direct, is_human_agent=True)
    assert "[转发给控制者的单播消息]" in direct_text
    assert "msg-direct" in direct_text
    assert "are you around?" in direct_text
    # Suppress autonomous send_message.
    assert "不要主动调 send_message" in direct_text

    bcast = MagicMock()
    bcast.message_id = "msg-bcast"
    bcast.from_member_name = "leader"
    bcast.content = "stand-up in 5"
    bcast.broadcast = True

    bcast_text = handler._format_message(bcast, is_human_agent=True)
    assert "[转发给控制者的广播消息]" in bcast_text


def _make_claimed_task(
    task_id: str,
    assignee: str,
    *,
    updated_at: int | None,
    title: str = "Fix bug",
):
    task = MagicMock()
    task.task_id = task_id
    task.title = title
    task.content = f"Work on {task_id}"
    task.status = "claimed"
    task.assignee = assignee
    task.updated_at = updated_at
    return task


def _make_pending_task(
    task_id: str,
    *,
    updated_at: int | None,
    title: str = "Pending work",
):
    task = MagicMock()
    task.task_id = task_id
    task.title = title
    task.content = f"Work on {task_id}"
    task.status = "pending"
    task.assignee = None
    task.updated_at = updated_at
    return task


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_leader_messages_assignee():
    """Leader messages a member whose claimed task has aged past the threshold."""
    agent = _make_leader()
    # updated_at at wall clock 0 → ancient → well past 60s threshold.
    stale_task = _make_claimed_task("task-1", assignee="dev-1", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale_task])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock(return_value="msg-1")

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent._configurator.task_manager.list_tasks.assert_awaited_once_with(status="claimed")
    agent._configurator.message_manager.send_message.assert_awaited_once()
    content, to_name = agent._configurator.message_manager.send_message.await_args.args
    assert to_name == "dev-1"
    assert "task-1" in content
    assert "task-1" in agent._coordination.dispatcher.stale_task._last_stale_nudge


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_fresh_task_does_not_nudge():
    """A task just claimed (updated_at ≈ now) is under the threshold and skipped."""
    agent = _make_leader()
    fresh_ms = int(time.time() * 1000)
    fresh_task = _make_claimed_task("task-2", assignee="dev-1", updated_at=fresh_ms)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[fresh_task])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent._configurator.message_manager.send_message.assert_not_called()
    assert "task-2" not in agent._coordination.dispatcher.stale_task._last_stale_nudge


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_throttles_follow_up_polls():
    """After one nudge, follow-up polls in the same window do not re-nudge."""
    agent = _make_leader()
    stale_task = _make_claimed_task("task-1b", assignee="dev-1", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale_task])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock(return_value="msg")

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()
    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent._configurator.message_manager.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_self_nudge_when_idle():
    """Teammate nudges itself via start_agent when idle on a stale self-claim."""
    agent = _make_teammate()
    agent._state.team_member = None
    own_task = _make_claimed_task("task-3", assignee="dev-1", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[own_task])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent._start_agent.assert_awaited_once()
    agent.steer.assert_not_called()
    content = agent._start_agent.await_args.args[0]
    assert "task-3" in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_self_nudge_steers_when_running():
    """A running self-owned stale task is nudged via steer rather than start."""
    agent = _make_teammate()
    agent._state.team_member = None
    own_task = _make_claimed_task("task-4", assignee="dev-1", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[own_task])
    # deliver_input dispatches on the ``_streaming_active`` flag, not on
    # ``_is_agent_running()`` — set the flag directly so steer is chosen.
    agent._stream_controller.streaming_active = True
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent.steer.assert_awaited_once()
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_claim_throttle_drops_unrelated_entries():
    """Throttle entries for tasks no longer claimed are cleaned up."""
    agent = _make_leader()
    still_claimed = _make_claimed_task("task-5", assignee="dev-1", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[still_claimed])
    agent._configurator.message_manager = MagicMock()
    agent._configurator.message_manager.send_message = AsyncMock()

    agent._coordination.dispatcher.stale_task._last_stale_nudge["task-5"] = 0.0
    agent._coordination.dispatcher.stale_task._last_stale_nudge["task-6"] = 0.0

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    assert "task-6" not in agent._coordination.dispatcher.stale_task._last_stale_nudge
    assert "task-5" in agent._coordination.dispatcher.stale_task._last_stale_nudge


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_pending_leader_self_nudges_with_hint():
    """Leader self-prompts about stale pending tasks so its LLM picks targets."""
    agent = _make_leader()
    stale = _make_pending_task("p-1", updated_at=0, title="Argue for ACP")

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent._configurator.task_manager.list_tasks.assert_awaited_once_with(status="pending")
    agent._start_agent.assert_awaited_once()
    agent.steer.assert_not_called()
    content = agent._start_agent.await_args.args[0]
    assert "p-1" in content
    assert "send_message" in content
    assert "claim_task" in content
    assert "p-1" in agent._coordination.dispatcher.stale_task._last_pending_nudge


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_pending_leader_steers_when_running():
    """A running leader should still receive the hint via steer."""
    agent = _make_leader()
    stale = _make_pending_task("p-2", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale])
    # deliver_input dispatches on the ``_streaming_active`` flag, not on
    # ``_is_agent_running()`` — set the flag directly so steer is chosen.
    agent._stream_controller.streaming_active = True
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent.steer.assert_awaited_once()
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_pending_fresh_task_skipped():
    """A pending task whose updated_at is recent should not trigger a nudge."""
    agent = _make_leader()
    fresh = _make_pending_task("p-3", updated_at=int(time.time() * 1000))

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[fresh])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent._start_agent.assert_not_called()
    assert "p-3" not in agent._coordination.dispatcher.stale_task._last_pending_nudge


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_pending_throttled_after_first_nudge():
    """Follow-up polls inside the same window should not re-nudge."""
    agent = _make_leader()
    stale = _make_pending_task("p-4", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()
    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent._start_agent.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_pending_teammate_skips_check():
    """Only the leader should self-prompt about pending tasks."""
    agent = _make_teammate()
    agent._state.team_member = None
    stale = _make_pending_task("p-5", updated_at=0)

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[stale])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent._configurator.task_manager.list_tasks.assert_not_called()
    agent._start_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_cleaned_event_shuts_down_teammate():
    """A teammate receiving TEAM_CLEANED must call shutdown_self exactly once."""
    agent = _make_teammate()
    agent._state.team_member = None
    agent.shutdown_self = AsyncMock()

    event = EventMessage.from_event(TeamCleanedEvent(team_name="test-team"))
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_cleaned_event_ignored_by_leader():
    """A leader must NEVER shutdown_self from its own CLEANED event.

    A persistent leader has to survive clean_team to accept the next
    interaction; a temporary leader's teardown is driven by the natural
    _finalize_round path instead.
    """
    agent = _make_leader()
    agent.shutdown_self = AsyncMock()

    event = EventMessage.from_event(TeamCleanedEvent(team_name="test-team"))
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_shutdown_self_cancels_running_round_and_closes_stream():
    """shutdown_self drives the cooperative cancel path and unblocks stream().

    The task loop should receive an abort signal first; if the round is
    stuck (here, an indefinite ``asyncio.sleep``), the fallback hard
    cancel must still terminate it so stream() can drain its sentinel.
    """
    agent = _make_teammate()
    agent._state.team_member = None
    agent._stream_controller.stream_queue = asyncio.Queue()

    abort_calls: list[None] = []

    async def _fake_abort() -> None:
        abort_calls.append(None)

    agent.harness._deep_agent.abort = _fake_abort

    async def _stuck_round() -> None:
        await asyncio.sleep(60)

    real_task = asyncio.create_task(_stuck_round())
    agent._stream_controller.agent_task = real_task

    # Patch the timeout so the test does not block for the production value.
    import openjiuwen.agent_teams.agent.stream_controller as stream_controller_module

    original_timeout = stream_controller_module._COOPERATIVE_ABORT_TIMEOUT_SECONDS
    stream_controller_module._COOPERATIVE_ABORT_TIMEOUT_SECONDS = 0.05
    try:
        await agent.shutdown_self()
    finally:
        stream_controller_module._COOPERATIVE_ABORT_TIMEOUT_SECONDS = original_timeout

    assert len(abort_calls) == 1, "harness.abort must be called exactly once"
    assert real_task.done(), "stuck task must be terminated by the fallback cancel"
    sentinel = await agent._stream_controller.stream_queue.get()
    assert sentinel is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_teammate_round_completion_wakes_mailbox_after_interrupt_clears():
    """Deferred mailbox messages should be retried immediately after interrupt clears."""
    agent = _make_teammate()
    # _make_teammate wires a real TeamBackend (with a default sqlite DB path)
    # during configure(). _update_status would then hit the DB on BUSY/READY
    # transitions -- skip it by detaching the team_member handle for this unit
    # test, which is scoped to the mailbox-wake behavior in _run_one_round.
    agent._state.team_member = None
    agent.coordination_loop.enqueue = AsyncMock()
    agent._stream_controller._execute_round = AsyncMock(return_value=None)
    agent._stream_controller.has_pending_interrupt = lambda: False

    await agent._stream_controller._run_one_round("continue work")

    agent.coordination_loop.enqueue.assert_awaited_once()
    event = agent.coordination_loop.enqueue.await_args.args[0]
    assert event.event_type == InnerEventType.POLL_MAILBOX


@pytest.mark.level0
def test_first_iter_gate_single_instance_registered_on_deep_agent():
    """Regression: gate awaited by coordination must be the same instance
    registered as a rail on the underlying agent. A previous refactor
    created two gates -- registered one, awaited the other -- causing
    teammates to hang on the initial mailbox poll forever.
    """
    agent = _make_leader()
    gate = agent._configurator.first_iter_gate
    assert gate is not None
    assert agent.harness is not None
    inner = agent.harness.inner_agent
    all_rails = list(inner._pending_rails) + list(inner._registered_rails)
    assert gate in all_rails


@pytest.mark.level0
def test_streaming_session_id_reads_from_contextvar():
    """Regression: StreamController must read session_id from the
    agent_teams contextvar (the single source of truth), not a stale local
    field. Previously a separate cached field on the state was the read
    source, which silently fell out of sync with the contextvar that tools
    were already consuming.
    """
    from openjiuwen.agent_teams.context import (
        reset_session_id,
        set_session_id,
    )

    agent = _make_leader()
    # Force a known-clean baseline. Sibling tests can leave the contextvar
    # populated (e.g. ``TeamAgent.recover_from_session`` writes the
    # contextvar without taking a Token), so we cannot assume "" here.
    baseline = set_session_id("")
    try:
        assert agent.session_id is None

        token = set_session_id("sess-xyz")
        try:
            assert agent.session_id == "sess-xyz"
        finally:
            reset_session_id(token)
    finally:
        reset_session_id(baseline)


def _wire_completion_handler(agent: TeamAgent, snapshot_result):
    """Point the team_completion handler at a stub backend + messager.

    ``snapshot_result`` is forwarded to ``is_team_completed``: pass a
    ``TeamCompletionSnapshot`` (or ``None``) for a fixed return, or a list
    for a per-call ``side_effect``. Returns the captured messager mock.
    """
    handler = agent._coordination.dispatcher.team_completion
    backend = MagicMock()
    backend.team_name = "test-team"
    if isinstance(snapshot_result, list):
        backend.is_team_completed = AsyncMock(side_effect=snapshot_result)
    else:
        backend.is_team_completed = AsyncMock(return_value=snapshot_result)
    messager = AsyncMock()
    handler._infra.team_backend = backend
    handler._infra.messager = messager
    agent._is_agent_running = lambda: False
    return messager


@pytest.mark.level0
def test_dispatcher_registers_team_completion_handler():
    """EventDispatcher exposes team_completion and registers its event keys."""
    agent = _make_leader()
    handler = agent._coordination.dispatcher.team_completion
    assert handler is not None
    callbacks = handler.get_callbacks()
    assert InnerEventType.POLL_TASK.value in callbacks
    assert TeamEvent.TASK_LIST_DRAINED in callbacks
    assert TeamEvent.TEAM_COMPLETED in callbacks


@pytest.mark.asyncio
@pytest.mark.level0
async def test_team_completion_emits_on_idle_leader_when_complete():
    """An idle leader emits TEAM_COMPLETED once the backend reports completion."""
    agent = _make_leader()
    snapshot = TeamCompletionSnapshot(member_count=2, task_count=3)
    messager = _wire_completion_handler(agent, snapshot)

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    messager.publish.assert_awaited_once()
    published = messager.publish.await_args.kwargs["message"]
    assert published.event_type == TeamEvent.TEAM_COMPLETED
    assert published.payload["member_count"] == 2
    assert published.payload["task_count"] == 3


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_not_re_emitted_on_repeated_tick():
    """A still-completed team does not re-emit TEAM_COMPLETED on the next tick."""
    agent = _make_leader()
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    messager = _wire_completion_handler(agent, snapshot)

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    assert messager.publish.await_count == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_re_arms_after_falling_edge():
    """Leaving and re-entering the completed state emits TEAM_COMPLETED again."""
    agent = _make_leader()
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    messager = _wire_completion_handler(agent, [snapshot, None, snapshot])

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    assert messager.publish.await_count == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_never_emits_for_teammate():
    """Only the leader owns the team-level conclusion; a teammate never emits."""
    agent = _make_teammate()
    agent._state.team_member = None
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    messager = _wire_completion_handler(agent, snapshot)

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    messager.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_skipped_when_round_in_flight():
    """A leader mid-round must not emit — its own status is BUSY anyway."""
    agent = _make_leader()
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    messager = _wire_completion_handler(agent, snapshot)

    in_flight = MagicMock()
    in_flight.done = MagicMock(return_value=False)
    agent._stream_controller.agent_task = in_flight

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    messager.publish.assert_not_awaited()
    agent._coordination.dispatcher.team_completion._infra.team_backend.is_team_completed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_consumers_accept_their_events():
    """on_task_list_drained / on_team_completed decode their payloads cleanly.

    With no completion callbacks registered (the default), on_task_list_drained
    is log-only and must not raise.
    """
    agent = _make_leader()
    handler = agent._coordination.dispatcher.team_completion

    drained = EventMessage.from_event(TaskListDrainedEvent(team_name="test-team", task_count=4))
    completed = EventMessage.from_event(TeamCompletedEvent(team_name="test-team", member_count=2, task_count=4))

    await handler.on_task_list_drained(drained)
    await handler.on_team_completed(completed)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_drained_fires_registered_completion_callbacks():
    """on_task_list_drained fires every registered completion callback."""
    agent = _make_leader()
    handler = agent._coordination.dispatcher.team_completion
    callback = AsyncMock()
    handler.register_completion_callback(callback)

    drained = EventMessage.from_event(TaskListDrainedEvent(team_name="test-team", task_count=2))
    await handler.on_task_list_drained(drained)

    callback.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_drained_callback_failure_is_isolated():
    """One failing completion callback does not skip the others."""
    agent = _make_leader()
    handler = agent._coordination.dispatcher.team_completion
    failing = AsyncMock(side_effect=RuntimeError("boom"))
    healthy = AsyncMock()
    handler.register_completion_callback(failing)
    handler.register_completion_callback(healthy)

    drained = EventMessage.from_event(TaskListDrainedEvent(team_name="test-team", task_count=1))
    await handler.on_task_list_drained(drained)

    failing.assert_awaited_once()
    healthy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_register_team_completion_callbacks_wires_skill_rail():
    """_register_team_completion_callbacks extracts mounted rails and registers them."""
    agent = _make_leader()
    handler = agent._coordination.dispatcher.team_completion
    handler._completion_callbacks.clear()

    skill_rail = MagicMock(name="TeamSkillRail")
    skill_rail.notify_team_completed = AsyncMock()
    agent._configurator.harness.find_rails = MagicMock(return_value=[skill_rail])

    agent._register_team_completion_callbacks()

    assert skill_rail.notify_team_completed in handler._completion_callbacks
