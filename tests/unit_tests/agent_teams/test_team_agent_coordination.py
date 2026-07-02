# coding: utf-8
"""Tests for TeamAgent coordination lifecycle wiring."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.member import MemberHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.team_agent import (
    TeamAgent,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.events import (
    BroadcastEvent,
    EventMessage,
    MemberShutdownEvent,
    MemberStatusChangedEvent,
    MessageEvent,
    TaskClaimedEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskListDrainedEvent,
    TeamCleanedEvent,
    TeamCompletedEvent,
    TeamEvent,
    ToolApprovalResultEvent,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import (
    TeamCompletionSnapshot,
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)


def _make_leader(
    lifecycle: str = "temporary",
    *,
    team_name: str = "test-team",
    member_name: str = "leader-1",
) -> TeamAgent:
    team_spec = TeamSpec(
        team_name=team_name,
        display_name=team_name,
        leader_member_name=member_name,
    )

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name=team_name,
        lifecycle=lifecycle,
        leader=LeaderSpec(
            member_name=member_name,
            display_name="Leader",
            persona="PM",
        ),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name=member_name,
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


async def _prepare_backend_db(agent: TeamAgent) -> None:
    """Initialize the leader's in-memory db and seed its team row.

    ``_make_leader`` builds the agent without initializing storage. Tests that
    call ``spawn_member`` directly (instead of driving the coordination
    start-up path that would init the db) must initialize it and create the
    ``team_info`` row first, so the member insert satisfies the foreign key.
    """
    backend = agent.team_backend
    await backend.db.initialize()
    await backend.db.team.create_team(
        team_name=backend.team_name,
        display_name=backend.team_name,
        leader_member_name=backend.member_name,
    )


def _make_human_agent(member_name: str = "human_alice") -> TeamAgent:
    """Build a configured ``role=HUMAN_AGENT`` avatar runtime.

    Mirrors ``_make_leader`` but with a HITT-enabled spec and a
    HUMAN_AGENT runtime context, so dispatcher role-aware filtering can
    be exercised directly.
    """
    team_spec = TeamSpec(
        team_name="hitt-team",
        display_name="hitt-team",
        leader_member_name="leader-1",
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="hitt-team",
        lifecycle="temporary",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name=member_name,
                display_name="Alice",
                role_type=TeamRole.HUMAN_AGENT,
                persona="user avatar",
            ),
        ],
    )
    context = TeamRuntimeContext(
        role=TeamRole.HUMAN_AGENT,
        member_name=member_name,
        persona="user avatar",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
    )
    agent = TeamAgent(
        AgentCard(
            id=member_name,
            name=member_name,
            description="avatar",
        ),
    )
    agent.configure(spec, context)
    return agent


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_on_unstarted_avatar_enqueues_user_input():
    """Driving a spawned-but-unstarted avatar enqueues USER_INPUT safely.

    Regression: the ``$<name>`` drive used to call ``deliver_input`` →
    ``harness.send`` directly from the leader's coroutine. When the avatar
    was spawned but its run cycle had not yet started its harness (e.g. the
    leader's initial routed input), that raised "NativeHarness not started".
    Routing the drive through the avatar's own coordination (``interact`` →
    USER_INPUT) must not touch the harness — it only enqueues, and the
    avatar's loop consumes it after ``coordination.start`` starts the harness.
    """
    agent = _make_human_agent("human_alice")
    bus = agent.coordination_loop
    # Configured but coordination never started → harness not started.
    assert bus is not None
    assert bus.is_running is False

    # Must not raise (the old reach-in raised on the unstarted harness).
    await agent.interact("please summarise design.md")

    queued = await asyncio.wait_for(bus._event_queue.get(), timeout=1.0)
    assert queued.event_type == InnerEventType.USER_INPUT
    assert queued.payload["content"] == "please summarise design.md"


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
    # The message handler feeds unread messages to the runtime via deliver_input.
    agent.deliver_input = AsyncMock()

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
    agent.deliver_input.assert_called_once()


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
    # ``is_human_agent`` recognises the recipient. Persist to DB
    # so async queries find the row.
    await _prepare_backend_db(agent)
    await agent.team_backend.spawn_member(
        member_name="human_alice",
        display_name="Alice",
        agent_card=AgentCard(),
        desc="user avatar",
        role=TeamRole.HUMAN_AGENT,
    )

    received: list = []

    async def cb(evt):
        received.append(evt)

    await agent.team_backend.register_human_agent_inbound("human_alice", cb)

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
async def test_idle_human_agent_tears_down_on_self_shutdown():
    """An idle human agent must tear its avatar down on MEMBER_SHUTDOWN.

    Unlike a teammate, a human agent has no autonomous LLM round to
    consume the shutdown message and reach the round-end
    ``close_stream`` path. When idle there is no round to ride, so the
    dispatcher must let MEMBER_SHUTDOWN through and the member handler
    must call ``shutdown_self`` directly; otherwise the avatar's run
    cycle never ends and the member is stuck in SHUTDOWN_REQUESTED
    forever (notably blocking a temporary team's ``clean_team``).
    """
    agent = _make_human_agent("human_alice")
    agent.shutdown_self = AsyncMock()
    assert not agent.has_in_flight_round()

    event = EventMessage.from_event(
        MemberShutdownEvent(
            team_name="hitt-team",
            member_name="human_alice",
            force=False,
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_busy_human_agent_not_interrupted_on_self_shutdown():
    """A controller-driven round in flight must not be interrupted.

    ``shutdown_member`` already wrote SHUTDOWN_REQUESTED before this
    event, so the in-flight round closes the stream at its own round-end
    (the teammate path). The handler must therefore leave a non-forced
    shutdown alone rather than collapsing the avatar mid-turn.
    """
    agent = _make_human_agent("human_alice")
    agent.shutdown_self = AsyncMock()
    agent.has_in_flight_round = MagicMock(return_value=True)

    event = EventMessage.from_event(
        MemberShutdownEvent(
            team_name="hitt-team",
            member_name="human_alice",
            force=False,
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_forced_shutdown_collapses_busy_human_agent():
    """``force=True`` bypasses the grace period and tears down immediately."""
    agent = _make_human_agent("human_alice")
    agent.shutdown_self = AsyncMock()
    agent.has_in_flight_round = MagicMock(return_value=True)

    event = EventMessage.from_event(
        MemberShutdownEvent(
            team_name="hitt-team",
            member_name="human_alice",
            force=True,
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_ignores_other_member_shutdown():
    """A human agent only tears down on its own shutdown event.

    MEMBER_SHUTDOWN targeting a different member must not collapse the
    avatar — it observes nothing about other members' lifecycles.
    """
    agent = _make_human_agent("human_alice")
    agent.shutdown_self = AsyncMock()

    event = EventMessage.from_event(
        MemberShutdownEvent(
            team_name="hitt-team",
            member_name="dev-1",
            force=False,
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    agent.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_dispatch_delivers_message_broadcast_and_task_claimed():
    """F_14 team events must survive the human-agent whitelist in dispatch.

    The role-aware rendering branches in ``MessageHandler`` /
    ``TaskBoardHandler`` (``hitt.msg_received_for_human`` /
    ``hitt.task_assigned_to_self_human``) are only reachable when
    MESSAGE / BROADCAST / TASK_CLAIMED pass the coarse whitelist in
    ``dispatch``. F_14 shipped those handler branches but left the
    whitelist muting the events, so the controller-facing rendering
    never ran. Drive each event through the real ``dispatch`` and
    assert the framework is triggered with the matching event_type.
    """
    agent = _make_human_agent("human_alice")
    trigger = AsyncMock()
    agent._coordination.dispatcher._framework.trigger = trigger

    models = [
        MessageEvent(
            team_name="hitt-team",
            message_id="m1",
            from_member_name="leader-1",
            to_member_name="human_alice",
        ),
        BroadcastEvent(
            team_name="hitt-team",
            message_id="b1",
            from_member_name="leader-1",
        ),
        TaskClaimedEvent(
            team_name="hitt-team",
            member_name="human_alice",
            task_id="t1",
        ),
    ]
    for model in models:
        trigger.reset_mock()
        event = EventMessage.from_event(model)
        await agent._coordination.dispatcher.dispatch(event)
        trigger.assert_awaited_once()
        assert trigger.await_args.args[0] == event.event_type


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_dispatch_mutes_task_board_survey_events():
    """Task-board survey events stay muted for a human-agent avatar.

    TASK_CREATED / TASK_UPDATED / ... drive ``_nudge_idle_agent``, which
    would push the avatar to autonomously scan the board for claimable
    work. Only TASK_CLAIMED (a direct assignment to the avatar) is a
    controller notification; survey events must not reach the framework.
    """
    agent = _make_human_agent("human_alice")
    trigger = AsyncMock()
    agent._coordination.dispatcher._framework.trigger = trigger

    event = EventMessage.from_event(
        TaskCreatedEvent(
            team_name="hitt-team",
            task_id="t1",
            status="pending",
        )
    )
    await agent._coordination.dispatcher.dispatch(event)

    trigger.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_human_agent_ignores_other_member_task_claim():
    """A claim targeting someone else must not nudge a human-agent avatar.

    A regular teammate / leader falls through to the board nudge so an
    idle member sees the change (see
    ``test_task_claimed_for_other_member_falls_through_to_board_nudge``).
    A human-agent avatar never autonomously surveys the board, so
    ``on_task_claimed`` short-circuits for a claim addressed to another
    member — no ``list_tasks`` survey, no ``deliver_input``.
    """
    agent = _make_leader(team_name="human-claim-other-team", member_name="human-leader-other")
    await _prepare_backend_db(agent)
    # Role check consults backend.is_human_agent (not TeamRole); persist
    # a HUMAN_AGENT row so async DB queries find it.
    await agent.team_backend.spawn_member(
        member_name="human-leader-other",
        display_name="Leader",
        agent_card=AgentCard(),
        desc="leader as human",
        role=TeamRole.HUMAN_AGENT,
    )

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[])
    agent._is_agent_running = lambda: False
    agent._start_agent = AsyncMock()
    agent.steer = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="human-claim-other-team",
            member_name="dev-1",
            task_id="task-7",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.list_tasks.assert_not_awaited()
    agent._start_agent.assert_not_called()
    agent.steer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_teammate_does_not_self_shutdown_on_member_shutdown():
    """Teammates keep the round-end teardown path on MEMBER_SHUTDOWN.

    A teammate consumes its shutdown message through the mailbox drain
    and final round, so the member handler must not short-circuit it
    with a direct ``shutdown_self`` (that is the human-agent-only path).
    """
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
        db_config=DatabaseConfig(db_type="memory"),
    )
    agent = TeamAgent(AgentCard(id="dev-1", name="dev", description="test"))
    agent.configure(spec, ctx)
    agent.shutdown_self = AsyncMock()

    await agent._coordination.dispatcher.member._handle_teammate_member_event(
        EventMessage.from_event(
            MemberShutdownEvent(
                team_name="test-team",
                member_name="dev-1",
                force=False,
            )
        )
    )

    agent.shutdown_self.assert_not_awaited()


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
async def test_process_unread_delegates_raw_delivered_messages_to_manager():
    """The handler hands every delivered message object to the manager.

    Read-state semantics (direct ``is_read`` rows vs the single broadcast
    watermark) belong to ``TeamMessageManager.mark_messages_read``, not the
    handler. The handler therefore delivers every message to the agent and
    passes the raw delivered objects — all directs AND all broadcasts — to
    the manager in one batch; collapsing broadcasts to their watermark anchor
    is the manager's job (see ``test_message_manager`` for that guard).
    """
    agent = _make_leader()
    agent._configurator.message_manager = MagicMock()
    mark_messages_read = AsyncMock(return_value=3)
    agent._configurator.message_manager.mark_messages_read = mark_messages_read
    # deliver_input is a no-op so the delivered-object collection is exercised
    # without a live harness.
    agent.deliver_input = AsyncMock()
    agent._start_agent = AsyncMock()
    agent.has_pending_interrupt = lambda: False

    def _bc(msg_id: str, ts: int):
        msg = MagicMock()
        msg.message_id = msg_id
        msg.from_member_name = "dev-2"
        msg.broadcast = True
        msg.timestamp = ts
        msg.content = f"broadcast {msg_id}"
        return msg

    def _dm(msg_id: str, ts: int):
        msg = MagicMock()
        msg.message_id = msg_id
        msg.from_member_name = "dev-2"
        msg.broadcast = False
        msg.timestamp = ts
        msg.content = f"direct {msg_id}"
        return msg

    # Newest-first ordering (as _read_all_unread returns). Two directs and
    # three broadcasts — the handler forwards all five untouched.
    agent._coordination.dispatcher.message._read_all_unread = AsyncMock(
        side_effect=[
            [_bc("b3", 3000), _dm("dm-2", 2500), _bc("b2", 2000), _dm("dm-1", 1500), _bc("b1", 1000)],
            [],
        ]
    )

    await agent._coordination.dispatcher.message._process_unread_messages("leader-1")

    # Called once with the raw delivered message objects — every id present,
    # no dedup at the handler layer.
    mark_messages_read.assert_called_once()
    delivered = mark_messages_read.call_args.args[0]
    delivered_ids = {m.message_id for m in delivered}
    assert delivered_ids == {"b3", "b2", "b1", "dm-2", "dm-1"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_interrupt_forwards_to_runtime_send():
    """A valid interrupt resume is forwarded to the runtime via send.

    The single-supervisor runtime serialises the resume — starting the resume
    round when idle, or steering it into the active one — so there is no
    controller-side pending queue. The controller only validates the resume
    against the pending interrupt and forwards it.
    """
    agent = _make_leader()
    # Make the resume validate against a pending interrupt.
    agent._stream_controller.is_valid_interrupt_resume = lambda _: True
    agent._configurator.harness.send = AsyncMock()

    interactive_input = InteractiveInput()
    interactive_input.update("call-1", {"approved": True, "feedback": "ok", "auto_confirm": False})

    await agent.resume_interrupt(interactive_input)

    agent._configurator.harness.send.assert_awaited_once_with(interactive_input)


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
    incomplete_task.updated_at = 1_700_000_000_000

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[incomplete_task])
    agent._is_agent_running = lambda: False
    # The handler always routes through deliver_input; the supervisor decides
    # steer-vs-new-round by phase, so the test asserts on the delivery seam.
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="dev-1",
            task_id="task-7",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.list_tasks.assert_awaited_once_with()
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "task-7" in content
    assert "Investigate crash" in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_claimed_for_other_member_nudges_leader_via_steer_when_busy():
    """A leader already running a round is nudged via steer on a teammate claim.

    Previously the ``on_task_board_event`` guard on ``has_in_flight_round``
    silently dropped the nudge.  After the fix, ``deliver_input`` handles
    the busy state internally (steer / pending_inputs), so the board
    context still reaches the leader.
    """
    agent = _make_leader()
    incomplete_task = MagicMock()
    incomplete_task.task_id = "task-8"
    incomplete_task.title = "In progress"
    incomplete_task.content = "Working"
    incomplete_task.status = "claimed"
    incomplete_task.assignee = "dev-1"
    incomplete_task.updated_at = 1_700_000_000_000

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[incomplete_task])

    agent._is_agent_running = lambda: True
    # Busy or idle, the handler delivers the board context the same way; the
    # supervisor steers it into the running round instead of dropping it.
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="dev-1",
            task_id="task-8",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    # Leader is busy → board context still delivered (not silently dropped).
    agent._configurator.task_manager.list_tasks.assert_awaited_once()
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "task-8" in content


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
    assert "leader-1" not in await agent.team_backend.human_agent_names()

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(return_value=MagicMock(title="Fix bug"))
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="test-team",
            member_name="leader-1",
            task_id="task-42",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
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
    agent = _make_leader(team_name="human-claim-self-team", member_name="human-leader-self")
    await _prepare_backend_db(agent)
    # Register the leader's member_name as a human-agent member. The
    # role check in TaskBoardHandler only consults
    # ``backend.is_human_agent`` — persist a HUMAN_AGENT row so async
    # DB queries find it.
    await agent.team_backend.spawn_member(
        member_name="human-leader-self",
        display_name="Leader",
        agent_card=AgentCard(),
        desc="leader as human",
        role=TeamRole.HUMAN_AGENT,
    )

    task_row = MagicMock()
    task_row.title = "Write design doc"
    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(return_value=task_row)
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="human-claim-self-team",
            member_name="human-leader-self",
            task_id="task-7",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent._configurator.task_manager.get.assert_awaited_once_with("task-7")
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    # Controller-facing HITT event tag and inlined title.
    assert 'kind="task-assigned"' in content
    assert 'for="controller"' in content
    assert "task-7" in content
    assert "Write design doc" in content
    # Must not show the teammate guidance to autonomously call view_task.
    assert "view_task" not in content
    # Strict prohibition: the avatar LLM must see that autonomous behavior
    # is forbidden and that it should stay silent until the controller
    # explicitly instructs via Inbox. Without these keywords the model
    # tends to drift into autonomous tool calls or acknowledgements.
    assert "严格禁止" in content
    assert "保持静默" in content
    assert "send_message" in content
    assert "member_complete_task" in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_claimed_for_human_self_swallows_title_lookup_error():
    """Title lookup failure must not break the dispatch loop.

    ``task_manager.get`` raising is logged + swallowed by the handler;
    the prompt still goes out with an empty title placeholder so the
    avatar still gets notified.
    """
    agent = _make_leader(team_name="human-claim-error-team", member_name="human-leader-error")
    await _prepare_backend_db(agent)
    await agent.team_backend.spawn_member(
        member_name="human-leader-error",
        display_name="Leader",
        agent_card=AgentCard(),
        desc="leader as human",
        role=TeamRole.HUMAN_AGENT,
    )

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.get = AsyncMock(side_effect=RuntimeError("db down"))
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskClaimedEvent(
            team_name="human-claim-error-team",
            member_name="human-leader-error",
            task_id="task-9",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_claimed(event)

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert 'kind="task-assigned"' in content
    assert 'for="controller"' in content
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
    msg.timestamp = 1_700_000_000_000

    text = handler._format_message(msg, is_human_agent=False, now_ms=1_700_000_000_000)
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
    direct.timestamp = 1_700_000_000_000

    direct_text = handler._format_message(direct, is_human_agent=True, now_ms=1_700_000_000_000)
    assert 'type="direct"' in direct_text
    assert 'for="controller"' in direct_text
    assert "msg-direct" in direct_text
    assert "are you around?" in direct_text
    # Strict prohibition keywords — the body must explicitly forbid
    # autonomous replies (send_message), require the avatar to stay
    # silent, and frame the input as a controller-facing notification
    # rather than something to act on.
    assert "严格禁止" in direct_text
    assert "保持静默" in direct_text
    assert "send_message" in direct_text

    bcast = MagicMock()
    bcast.message_id = "msg-bcast"
    bcast.from_member_name = "leader"
    bcast.content = "stand-up in 5"
    bcast.broadcast = True
    bcast.timestamp = 1_700_000_000_000

    bcast_text = handler._format_message(bcast, is_human_agent=True, now_ms=1_700_000_000_000)
    assert 'type="broadcast"' in bcast_text
    assert 'for="controller"' in bcast_text
    assert "严格禁止" in bcast_text
    assert "保持静默" in bcast_text


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
    agent.deliver_input = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
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
    # The handler always delivers; the running round receives it as a steer
    # inside the supervisor, so the test asserts on the delivery seam.
    agent._is_agent_running = lambda: True
    agent.deliver_input = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_claimed_tasks()

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "task-4" in content


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
    agent.deliver_input = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent._configurator.task_manager.list_tasks.assert_awaited_once_with(status="pending")
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
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
    # The handler always delivers; a running leader receives it as a steer
    # inside the supervisor, so the test asserts on the delivery seam.
    agent._is_agent_running = lambda: True
    agent.deliver_input = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "p-2" in content


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
    agent.deliver_input = AsyncMock()

    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()
    await agent._coordination.dispatcher.stale_task._check_stale_pending_tasks()

    agent.deliver_input.assert_awaited_once()


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


def _make_member_status_handler(
    lifecycle: str,
    members: list[SimpleNamespace],
) -> tuple[MagicMock, MemberHandler]:
    backend = MagicMock()
    backend.list_members = AsyncMock(return_value=members)
    backend.clean_team = AsyncMock(return_value=True)
    handler = MemberHandler(
        host=SimpleNamespace(),
        blueprint=SimpleNamespace(
            role=TeamRole.LEADER,
            lifecycle=lifecycle,
            member_name="leader-1",
        ),
        infra=TeamInfra(team_backend=backend),
        poll_ctrl=SimpleNamespace(),
        stale_claim_throttle={},
    )
    return backend, handler


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_auto_cleans_after_all_teammates_shutdown():
    """Teams should clean once every non-leader member is SHUTDOWN."""
    backend, handler = _make_member_status_handler(
        "temporary",
        [
            SimpleNamespace(member_name="leader-1", status=MemberStatus.READY.value),
            SimpleNamespace(member_name="dev-code", status=MemberStatus.SHUTDOWN.value),
            SimpleNamespace(member_name="code-reviewer", status=MemberStatus.SHUTDOWN.value),
        ],
    )

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-code",
            old_status=MemberStatus.SHUTDOWN_REQUESTED.value,
            new_status=MemberStatus.SHUTDOWN.value,
        )
    )
    await handler.on_member_event(event)

    backend.clean_team.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_persistent_leader_does_not_auto_clean_after_teammate_shutdown():
    """A partially shut down persistent team must not clean early."""
    backend, handler = _make_member_status_handler(
        "persistent",
        [
            SimpleNamespace(member_name="leader-1", status=MemberStatus.READY.value),
            SimpleNamespace(member_name="dev-code", status=MemberStatus.SHUTDOWN.value),
            SimpleNamespace(member_name="code-reviewer", status=MemberStatus.READY.value),
        ],
    )

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="dev-code",
            old_status=MemberStatus.SHUTDOWN_REQUESTED.value,
            new_status=MemberStatus.SHUTDOWN.value,
        )
    )
    await handler.on_member_event(event)

    backend.list_members.assert_awaited_once_with()
    backend.clean_team.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_persistent_leader_auto_cleans_after_all_teammates_shutdown():
    """A persistent team disband request should still clean once all members shut down."""
    backend, handler = _make_member_status_handler(
        "persistent",
        [
            SimpleNamespace(member_name="leader-1", status=MemberStatus.READY.value),
            SimpleNamespace(member_name="dev-code", status=MemberStatus.SHUTDOWN.value),
            SimpleNamespace(member_name="code-reviewer", status=MemberStatus.SHUTDOWN.value),
        ],
    )

    event = EventMessage.from_event(
        MemberStatusChangedEvent(
            team_name="test-team",
            member_name="code-reviewer",
            old_status=MemberStatus.SHUTDOWN_REQUESTED.value,
            new_status=MemberStatus.SHUTDOWN.value,
        )
    )
    await handler.on_member_event(event)

    backend.clean_team.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_shutdown_self_cancels_running_round_and_closes_stream():
    """shutdown_self drives the cooperative cancel path and unblocks stream().

    The controller's ``cooperative_cancel`` forwards a graceful abort to the
    runtime; shutdown then closes the stream so the member's stream loop breaks
    on the None sentinel. (The abort→native forwarding is covered in
    test_stream_controller.)
    """
    agent = _make_teammate()
    agent._state.team_member = None
    agent._stream_controller.stream_queue = asyncio.Queue()

    cancel_calls: list[None] = []

    async def _coop_cancel() -> None:
        cancel_calls.append(None)

    agent._stream_controller.cooperative_cancel = _coop_cancel

    await agent.shutdown_self()

    assert cancel_calls == [None], "shutdown must drive the cooperative cancel path"
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
    agent._stream_controller.has_pending_interrupt = lambda: False

    # Round chain settled to IDLE: the controller's idle-settle hook wakes the
    # mailbox (re-poll) once the interrupt is cleared. Drive it directly rather
    # than the obsolete _run_one_round loop.
    await agent._stream_controller._on_idle_settled()

    agent.coordination_loop.enqueue.assert_awaited_once()
    event = agent.coordination_loop.enqueue.await_args.args[0]
    assert event.event_type == InnerEventType.POLL_MAILBOX


def _stub_coordination_for_invoke(agent: TeamAgent) -> None:
    """Stub the coordination seams so invoke/stream run without a real round.

    ``start`` / ``finalize_round`` are no-ops; ``enqueue_user_input`` is a
    spy; ``enqueue_initial_mailbox_poll`` closes the stream so the
    consume loop terminates on the None sentinel.
    """
    agent._coordination.start = AsyncMock()
    agent._coordination.finalize_round = AsyncMock()
    agent._coordination.enqueue_user_input = AsyncMock()

    async def _close_stream() -> None:
        await agent._stream_controller.stream_queue.put(None)

    agent._coordination.enqueue_initial_mailbox_poll = AsyncMock(side_effect=_close_stream)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_invoke_skips_first_round_when_query_empty():
    """An empty query must not drive a first round (no enqueue_user_input).

    Spawn / recover / resume with no input come up, subscribe, and idle —
    the mailbox poll still runs and delivers only real pending messages.
    """
    agent = _make_teammate()
    _stub_coordination_for_invoke(agent)

    await agent.invoke({"query": ""})

    agent._coordination.enqueue_user_input.assert_not_awaited()
    agent._coordination.enqueue_initial_mailbox_poll.assert_awaited_once()
    assert agent.pending_user_query == ""


@pytest.mark.asyncio
@pytest.mark.level0
async def test_invoke_drives_first_round_when_query_present():
    """A genuine first-start instruction drives the first round."""
    agent = _make_teammate()
    _stub_coordination_for_invoke(agent)

    await agent.invoke({"query": "do the work"})

    agent._coordination.enqueue_user_input.assert_awaited_once()
    agent._coordination.enqueue_initial_mailbox_poll.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_invoke_normalizes_none_query_to_empty():
    """A present-but-None query normalizes to "" and skips the first round."""
    agent = _make_teammate()
    _stub_coordination_for_invoke(agent)

    await agent.invoke({"query": None})

    agent._coordination.enqueue_user_input.assert_not_awaited()
    assert agent.pending_user_query == ""


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stream_skips_first_round_when_query_empty():
    """The streaming path applies the same empty-query gate as invoke."""
    agent = _make_teammate()
    _stub_coordination_for_invoke(agent)

    chunks = [chunk async for chunk in agent.stream({"query": ""})]

    assert chunks == []
    agent._coordination.enqueue_user_input.assert_not_awaited()
    agent._coordination.enqueue_initial_mailbox_poll.assert_awaited_once()


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
    agent.finalize_non_contributing_worktrees = AsyncMock()
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
    agent.finalize_non_contributing_worktrees.assert_awaited_once_with()


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

    # A live round is signalled by the runtime phase (RUNNING) surfaced through
    # is_agent_running; the handler short-circuits before any completion check.
    agent._is_agent_running = lambda: True

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    messager.publish.assert_not_awaited()
    agent._coordination.dispatcher.team_completion._infra.team_backend.is_team_completed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_skipped_when_interrupt_pending():
    """A pending interrupt is idle at the harness layer but must keep the stream open."""
    from openjiuwen.agent_teams.agent.coordination.handlers.team_completion import TeamCompletionHandler

    host = MagicMock()
    host.has_in_flight_round.return_value = False
    host.is_agent_running.return_value = False
    host.has_pending_interrupt.return_value = True
    host.conclude_completed_round = AsyncMock()
    blueprint = MagicMock()
    blueprint.role = TeamRole.LEADER
    blueprint.lifecycle = "persistent"
    backend = MagicMock()
    backend.team_name = "test-team"
    backend.is_team_completed = AsyncMock(return_value=TeamCompletionSnapshot(member_count=1, task_count=1))
    infra = MagicMock()
    infra.team_backend = backend
    infra.messager = AsyncMock()
    handler = TeamCompletionHandler(host, blueprint, infra, MagicMock())

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await handler.on_poll_task(event)

    infra.messager.publish.assert_not_awaited()
    backend.is_team_completed.assert_not_awaited()
    host.conclude_completed_round.assert_not_awaited()


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


# ------------------------------------------------------------------
# Regression: TASK_COMPLETED must reach leader even when busy
# ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_completed_nudges_leader_with_all_done_when_idle():
    """When the last task completes and the leader is idle, the
    "all tasks done" summary prompt is delivered via _start_agent."""
    agent = _make_leader()
    completed_task = MagicMock()
    completed_task.task_id = "task-5"
    completed_task.title = "Final task"
    completed_task.content = "Done"
    completed_task.status = "completed"
    completed_task.assignee = "member-5"

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[completed_task])
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskCompletedEvent(
            team_name="test-team",
            task_id="task-5",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_board_event(event)

    # Leader idle → the "all done" summary prompt is delivered.
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "完成" in content or "complete" in content.lower()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_completed_nudges_leader_via_steer_when_busy():
    """When the last task completes and the leader is busy, the
    "all tasks done" summary prompt is delivered via steer instead of
    being silently dropped.

    Regression guard: previously ``on_task_board_event`` returned early
    when ``has_in_flight_round()`` was True, causing the summary prompt
    to never reach the leader.  The fix delegates queuing to
    ``deliver_input`` which uses steer / pending_inputs internally.
    """
    agent = _make_leader()
    completed_task = MagicMock()
    completed_task.task_id = "task-5"
    completed_task.title = "Final task"
    completed_task.content = "Done"
    completed_task.status = "completed"
    completed_task.assignee = "member-5"

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(return_value=[completed_task])

    # Simulate leader busy (agent running)
    agent._is_agent_running = lambda: True
    agent._has_in_flight_round = lambda: True
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskCompletedEvent(
            team_name="test-team",
            task_id="task-5",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_board_event(event)

    # Leader busy → the "all done" summary prompt is still delivered (the
    # supervisor steers it into the running round, not silently dropped).
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "完成" in content or "complete" in content.lower()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_completed_with_incomplete_tasks_nudges_leader_board():
    """When a task completes but others remain incomplete, the leader
    receives the task board overview (not the "all done" prompt)."""
    agent = _make_leader()
    completed_task = MagicMock()
    completed_task.task_id = "task-1"
    completed_task.title = "Done task"
    completed_task.content = "Finished"
    completed_task.status = "completed"
    completed_task.assignee = "member-1"

    incomplete_task = MagicMock()
    incomplete_task.task_id = "task-2"
    incomplete_task.title = "Pending task"
    incomplete_task.content = "In progress"
    incomplete_task.status = "claimed"
    incomplete_task.assignee = "member-2"
    incomplete_task.updated_at = 1_700_000_000_000

    agent._configurator.task_manager = MagicMock()
    agent._configurator.task_manager.list_tasks = AsyncMock(
        return_value=[completed_task, incomplete_task]
    )
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()

    event = EventMessage.from_event(
        TaskCompletedEvent(
            team_name="test-team",
            task_id="task-1",
        )
    )
    await agent._coordination.dispatcher.task_board.on_task_board_event(event)

    # Not all done → leader gets the task board overview
    agent.deliver_input.assert_awaited_once()
    content = agent.deliver_input.await_args.args[0]
    assert "task-2" in content
    # Should NOT contain the "all done" summary prompt
    assert "完成" not in content or "task" in content.lower()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_persistent_completion_closes_leader_stream():
    """A completed persistent team emits a completion marker then closes the stream."""
    agent = _make_leader(lifecycle="persistent")
    snapshot = TeamCompletionSnapshot(member_count=2, task_count=3)
    _wire_completion_handler(agent, snapshot)
    agent._stream_controller.stream_queue = asyncio.Queue()

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    marker = agent._stream_controller.stream_queue.get_nowait()
    assert marker.payload["event_type"] == "team.completed"
    assert marker.payload["member_count"] == 2
    assert marker.payload["task_count"] == 3
    assert agent._stream_controller.stream_queue.get_nowait() is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temporary_completion_does_not_close_stream():
    """A temporary team still emits TEAM_COMPLETED but never closes its own stream."""
    agent = _make_leader()
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    messager = _wire_completion_handler(agent, snapshot)
    agent._stream_controller.stream_queue = asyncio.Queue()

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    messager.publish.assert_awaited_once()
    assert agent._stream_controller.stream_queue.empty()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_persistent_completion_concludes_once_per_rising_edge():
    """The completion marker is enqueued once until the team leaves completion."""
    agent = _make_leader(lifecycle="persistent")
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    _wire_completion_handler(agent, snapshot)
    agent._stream_controller.stream_queue = asyncio.Queue()

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)

    # marker + sentinel from the first tick only.
    assert agent._stream_controller.stream_queue.qsize() == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_rearm_allows_completion_to_conclude_again():
    """rearm() resets the guard so a resumed completed team concludes again."""
    agent = _make_leader(lifecycle="persistent")
    snapshot = TeamCompletionSnapshot(member_count=1, task_count=1)
    _wire_completion_handler(agent, snapshot)
    handler = agent._coordination.dispatcher.team_completion
    agent._stream_controller.stream_queue = asyncio.Queue()

    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})
    await handler.on_poll_task(event)
    handler.rearm()
    await handler.on_poll_task(event)

    # Two rising edges → two markers + two sentinels.
    assert agent._stream_controller.stream_queue.qsize() == 4
