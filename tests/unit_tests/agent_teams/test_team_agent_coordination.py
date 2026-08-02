# coding: utf-8
"""Tests for TeamAgent coordination lifecycle wiring."""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.coordination import InnerEventType
from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.status import TaskStatus
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
    TeamCleanedEvent,
    ToolApprovalResultEvent,
)
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


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
            desc="PM",
        ),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        desc="PM",
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
        desc="dev",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    return agent


def _dispatcher(agent: TeamAgent) -> EventDispatcher:
    dispatcher = agent.coordination.dispatcher
    assert dispatcher is not None
    return dispatcher


def _idle_for(agent: TeamAgent, seconds: float) -> None:
    agent._state.idle_since = time.monotonic() - seconds


def test_event_bus_created_on_configure():
    """configure() wires an EventBus for the member role."""
    agent = _make_leader()
    assert agent.coordination_loop is not None
    assert agent.coordination_loop.role == TeamRole.LEADER


@pytest.mark.asyncio
async def test_start_stop_coordination():
    """_start/_stop manage the event bus lifecycle."""
    agent = _make_leader()
    await agent._start_coordination(session=None)
    assert agent.coordination_loop.is_running is True
    await agent._stop_coordination()
    assert agent.coordination_loop.is_running is False


@pytest.mark.asyncio
async def test_interact_enqueues_user_input():
    """interact() pushes USER_INPUT onto the coordination event bus."""
    agent = _make_leader()
    agent.coordination.enqueue_user_input = AsyncMock(wraps=agent.coordination.enqueue_user_input)

    await agent.interact("普通消息")

    agent.coordination.enqueue_user_input.assert_awaited_once_with("普通消息")


@pytest.mark.asyncio
async def test_tool_approval_event_resumes_interrupt():
    """Tool approval result event should resume teammate HITL with InteractiveInput."""
    team_spec = TeamSpec(
        team_name="test-team",
        display_name="test-team",
        leader_member_name="leader-1")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="test-team")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE, member_name="dev-1", desc="dev", team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    agent._configurator.resources.harness = MagicMock()
    agent.resume_interrupt = AsyncMock()

    event = EventMessage.from_event(ToolApprovalResultEvent(
        team_name="test-team",
        member_name="dev-1",
        tool_call_id="call-1",
        approved=True,
        feedback="ok",
        auto_confirm=True,
    ))
    await _dispatcher(agent).dispatch(event)

    agent.resume_interrupt.assert_awaited_once()
    interactive_input = agent.resume_interrupt.await_args.args[0]
    assert interactive_input.user_inputs["call-1"]["approved"] is True
    assert interactive_input.user_inputs["call-1"]["feedback"] == "ok"
    assert interactive_input.user_inputs["call-1"]["auto_confirm"] is True


@pytest.mark.asyncio
async def test_mailbox_messages_deferred_while_interrupt_pending():
    """Normal mailbox messages should not preempt a pending tool interrupt."""
    agent = _make_leader()
    agent._configurator.infra.message_manager = MagicMock()
    agent._configurator.infra.message_manager.mark_message_read = AsyncMock(return_value=True)
    agent.deliver_input = AsyncMock()
    agent.has_pending_interrupt = lambda: True

    fake_msg = MagicMock()
    fake_msg.message_id = "msg-normal"
    fake_msg.from_member_name = "dev-2"
    fake_msg.broadcast = False
    fake_msg.timestamp = 1000
    fake_msg.content = "normal mailbox message"
    _dispatcher(agent).message._read_all_unread = AsyncMock(side_effect=[[fake_msg]])

    await _dispatcher(agent).message._process_unread_messages("leader-1")

    agent._configurator.infra.message_manager.mark_message_read.assert_not_called()
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
async def test_resume_interrupt_sends_to_harness():
    """Approval resume is forwarded to the member harness."""
    agent = _make_leader()
    harness = MagicMock()
    harness.send = AsyncMock()
    agent._configurator.resources.harness = harness
    agent._stream_controller.is_valid_interrupt_resume = MagicMock(return_value=True)

    interactive_input = InteractiveInput()
    interactive_input.update("call-1", {"approved": True, "feedback": "ok", "auto_confirm": False})

    await agent.resume_interrupt(interactive_input)

    harness.send.assert_awaited_once_with(interactive_input)


@pytest.mark.asyncio
async def test_member_ready_does_not_nudge_assignee():
    """Leader observes member transitions; stale nudges are self-only elsewhere."""
    agent = _make_leader()
    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.get_tasks_by_assignee = AsyncMock()
    agent._configurator.infra.message_manager = MagicMock()
    agent._configurator.infra.message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="busy",
        new_status="ready",
    ))
    await _dispatcher(agent).member._handle_leader_member_event(event)

    agent._configurator.infra.task_manager.get_tasks_by_assignee.assert_not_called()
    agent._configurator.infra.message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_member_error_does_not_nudge_assignee():
    """ERROR transitions are observed only; no cross-process nudge is sent."""
    agent = _make_leader()
    agent._configurator.infra.message_manager = MagicMock()
    agent._configurator.infra.message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="busy",
        new_status="error",
    ))
    await _dispatcher(agent).member._handle_leader_member_event(event)

    agent._configurator.infra.message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_member_status_unchanged_skips_side_effects():
    """Redundant READY → READY transitions should not touch task or message managers."""
    agent = _make_leader()
    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.get_tasks_by_assignee = AsyncMock()
    agent._configurator.infra.message_manager = MagicMock()
    agent._configurator.infra.message_manager.send_message = AsyncMock()

    event = EventMessage.from_event(MemberStatusChangedEvent(
        team_name="test-team",
        member_name="dev-1",
        old_status="ready",
        new_status="ready",
    ))
    await _dispatcher(agent).member._handle_leader_member_event(event)

    agent._configurator.infra.task_manager.get_tasks_by_assignee.assert_not_called()
    agent._configurator.infra.message_manager.send_message.assert_not_called()


def _make_active_task(
    task_id: str,
    assignee: str,
    *,
    title: str = "Fix bug",
) -> Any:
    task = MagicMock()
    task.task_id = task_id
    task.title = title
    task.content = f"Work on {task_id}"
    task.status = TaskStatus.IN_PROGRESS.value
    task.assignee = assignee
    task.updated_at = 0
    return task


def _make_pending_task(
    task_id: str,
    *,
    title: str = "Pending work",
) -> Any:
    task = MagicMock()
    task.task_id = task_id
    task.title = title
    task.content = f"Work on {task_id}"
    task.status = TaskStatus.PENDING.value
    task.assignee = None
    task.updated_at = 0
    return task


def _list_tasks_side_effect(tasks_by_status: dict[str | None, list[Any]]):
    async def _list_tasks(*, status: str | None = None):
        return list(tasks_by_status.get(status, []))

    return _list_tasks


@pytest.mark.asyncio
async def test_stale_claim_teammate_self_nudges_when_idle():
    """Teammate nudges itself when idle too long on an owned active task."""
    agent = _make_teammate()
    agent._team_member = None
    own_task = _make_active_task("task-3", assignee="dev-1")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PLANNING.value: [],
            TaskStatus.IN_PROGRESS.value: [own_task],
        }),
    )
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_claimed_tasks()

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "task-3" in content


@pytest.mark.asyncio
async def test_stale_claim_skips_when_busy():
    """A busy member (no idle clock) is never self-nudged for stale claims."""
    agent = _make_teammate()
    agent._team_member = None
    own_task = _make_active_task("task-4", assignee="dev-1")
    agent._state.idle_since = None

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PLANNING.value: [],
            TaskStatus.IN_PROGRESS.value: [own_task],
        }),
    )
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_claimed_tasks()

    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
async def test_stale_claim_fresh_idle_does_not_nudge():
    """A member idle below the threshold should not be nudged."""
    agent = _make_teammate()
    agent._team_member = None
    own_task = _make_active_task("task-2", assignee="dev-1")
    _idle_for(agent, 10)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PLANNING.value: [],
            TaskStatus.IN_PROGRESS.value: [own_task],
        }),
    )
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_claimed_tasks()

    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
async def test_stale_claim_throttles_follow_up_polls():
    """After one nudge, follow-up sweeps in the same window do not re-nudge."""
    agent = _make_teammate()
    agent._team_member = None
    stale_task = _make_active_task("task-1b", assignee="dev-1")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PLANNING.value: [],
            TaskStatus.IN_PROGRESS.value: [stale_task],
        }),
    )
    agent.deliver_input = AsyncMock()

    stale_handler = _dispatcher(agent).stale_task
    await stale_handler._check_stale_claimed_tasks()
    await stale_handler._check_stale_claimed_tasks()

    agent.deliver_input.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_claim_throttle_drops_unrelated_entries():
    """Throttle bookkeeping drops tasks that left the owned-active set."""
    agent = _make_teammate()
    agent._team_member = None
    still_active = _make_active_task("task-5", assignee="dev-1")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PLANNING.value: [],
            TaskStatus.IN_PROGRESS.value: [still_active],
        }),
    )
    agent.deliver_input = AsyncMock()

    stale_handler = _dispatcher(agent).stale_task
    stale_handler._last_stale_nudge["task-5"] = 0.0
    stale_handler._last_stale_nudge["task-6"] = 0.0

    await stale_handler._check_stale_claimed_tasks()

    assert "task-6" not in stale_handler._last_stale_nudge
    assert "task-5" in stale_handler._last_stale_nudge


@pytest.mark.asyncio
async def test_stale_pending_leader_self_nudges_with_hint():
    """Leader self-prompts about stale pending tasks when a member is free."""
    agent = _make_leader()
    stale = _make_pending_task("p-1", title="Argue for ACP")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PENDING.value: [stale],
        }),
    )
    team_backend = MagicMock()
    team_backend.list_member_roster = AsyncMock(return_value=[
        MagicMock(status="ready"),
    ])
    agent._configurator.infra.team_backend = team_backend
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_pending_tasks()

    agent._configurator.infra.task_manager.list_tasks.assert_awaited_once_with(status=TaskStatus.PENDING.value)
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "p-1" in content
    assert "p-1" in _dispatcher(agent).stale_task._last_pending_nudge


@pytest.mark.asyncio
async def test_stale_pending_fresh_idle_skipped():
    """A leader idle below the threshold should not self-prompt."""
    agent = _make_leader()
    stale = _make_pending_task("p-3")
    _idle_for(agent, 10)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PENDING.value: [stale],
        }),
    )
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_pending_tasks()

    agent.deliver_input.assert_not_called()
    assert "p-3" not in _dispatcher(agent).stale_task._last_pending_nudge


@pytest.mark.asyncio
async def test_stale_pending_throttled_after_first_nudge():
    """Follow-up sweeps inside the same window should not re-nudge."""
    agent = _make_leader()
    stale = _make_pending_task("p-4")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock(
        side_effect=_list_tasks_side_effect({
            TaskStatus.PENDING.value: [stale],
        }),
    )
    team_backend = MagicMock()
    team_backend.list_member_roster = AsyncMock(return_value=[
        MagicMock(status="ready"),
    ])
    agent._configurator.infra.team_backend = team_backend
    agent.deliver_input = AsyncMock()

    stale_handler = _dispatcher(agent).stale_task
    await stale_handler._check_stale_pending_tasks()
    await stale_handler._check_stale_pending_tasks()

    agent.deliver_input.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_pending_teammate_skips_check():
    """Only the leader should self-prompt about pending tasks."""
    agent = _make_teammate()
    agent._team_member = None
    stale = _make_pending_task("p-5")
    _idle_for(agent, 700)

    agent._configurator.infra.task_manager = MagicMock()
    agent._configurator.infra.task_manager.list_tasks = AsyncMock()
    agent.deliver_input = AsyncMock()

    await _dispatcher(agent).stale_task._check_stale_pending_tasks()

    agent._configurator.infra.task_manager.list_tasks.assert_not_called()
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
async def test_team_cleaned_event_shuts_down_teammate():
    """A teammate receiving TEAM_CLEANED must call shutdown_self exactly once."""
    agent = _make_teammate()
    agent._team_member = None
    agent._configurator.resources.harness = MagicMock()
    agent.shutdown_self = AsyncMock()

    event = EventMessage.from_event(TeamCleanedEvent(team_name="test-team"))
    await _dispatcher(agent).dispatch(event)

    agent.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_cleaned_event_ignored_by_leader():
    """A leader must NEVER shutdown_self from its own CLEANED event."""
    agent = _make_leader()
    agent._configurator.resources.harness = MagicMock()
    agent.shutdown_self = AsyncMock()

    event = EventMessage.from_event(TeamCleanedEvent(team_name="test-team"))
    await _dispatcher(agent).dispatch(event)

    agent.shutdown_self.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_self_cancels_running_round_and_closes_stream():
    """shutdown_self cancels the in-flight agent task and unblocks stream()."""
    agent = _make_teammate()
    agent._state.team_member = None
    agent._stream_controller.stream_queue = asyncio.Queue()
    agent._stream_controller.cooperative_cancel = AsyncMock()

    await agent.shutdown_self()

    agent._stream_controller.cooperative_cancel.assert_awaited_once()
    sentinel = await agent._stream_controller.stream_queue.get()
    assert sentinel is None


@pytest.mark.asyncio
async def test_wake_mailbox_if_interrupt_cleared_enqueues_poll():
    """Deferred mailbox messages are retried after interrupt clears."""
    agent = _make_teammate()
    agent._team_member = None
    agent.coordination_loop.enqueue = AsyncMock()
    agent.has_pending_interrupt = lambda: False

    await agent._wake_mailbox_if_interrupt_cleared()

    agent.coordination_loop.enqueue.assert_awaited_once()
    event = agent.coordination_loop.enqueue.await_args.args[0]
    assert event.event_type == InnerEventType.POLL_MAILBOX


@pytest.mark.asyncio
async def test_dispatch_defers_wake_until_agent_ready():
    """Real wakes must not be dropped while harness is missing; POLL may drop."""
    from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
    from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage
    from openjiuwen.agent_teams.agent.infra import TeamInfra

    ready = {"value": False}
    host = MagicMock()
    host.is_agent_ready = lambda: ready["value"]
    host.is_agent_running = lambda: False
    host.idle_seconds = lambda: None
    host.has_in_flight_round = lambda: False
    host.has_pending_interrupt = lambda: False
    host.cancel_agent = AsyncMock()
    host.deliver_input = AsyncMock()
    host.resume_interrupt = AsyncMock()
    host.shutdown_self = AsyncMock()
    host.conclude_completed_round = AsyncMock()
    host.finalize_non_contributing_worktrees = AsyncMock()

    blueprint = MagicMock(spec=TeamAgentBlueprint)
    blueprint.role = TeamRole.LEADER
    blueprint.member_name = "leader-1"
    blueprint.spec = MagicMock(reliability=None)

    poll_ctrl = MagicMock()
    poll_ctrl.pause_polls = AsyncMock()
    poll_ctrl.resume_polls = AsyncMock()

    dispatcher = EventDispatcher(
        host,
        blueprint,
        TeamInfra(),
        poll_ctrl,
    )
    seen: list[Any] = []

    async def _capture(event_key: str, event: Any) -> None:
        seen.append((event_key, event))

    dispatcher._framework.trigger = AsyncMock(side_effect=_capture)

    user_wake = InnerEventMessage(
        event_type=InnerEventType.USER_INPUT,
        payload={"content": "hello"},
    )
    poll_wake = InnerEventMessage(event_type=InnerEventType.POLL_TASK)

    await dispatcher.dispatch(user_wake)
    await dispatcher.dispatch(poll_wake)
    assert seen == []
    assert dispatcher._deferred_wakes == [user_wake]

    ready["value"] = True
    await dispatcher.flush_deferred()
    assert seen == [(InnerEventType.USER_INPUT.value, user_wake)]
    assert dispatcher._deferred_wakes == []
