# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for F_62 dispatch-mode handler selection and behavior.

Each dispatch mode owns its handler classes; selection happens where the
``EventDispatcher`` assembles handlers (construction, from the static spec
mode), never inside a handler method.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update

from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.coordination.handlers import (
    ScheduledStaleTaskHandler,
    ScheduledTaskBoardHandler,
    StaleTaskHandler,
    TaskBoardHandler,
)
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskCreatedEvent,
    TaskSubmittedForReviewEvent,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import _get_task_model
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard

TEAM = "mode_team"
LEADER = "leader"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("mode_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name=TEAM, display_name="Mode Team", leader_member_name=LEADER)
        for name in (LEADER, "dev-1", "rev-1"):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


class FakeHost:
    """Duck-typed DispatcherHost recording round deliveries."""

    def __init__(self):
        self.delivered: list = []

    def is_agent_ready(self) -> bool:
        return True

    def is_agent_running(self) -> bool:
        return False

    def has_in_flight_round(self) -> bool:
        return False

    def has_pending_interrupt(self) -> bool:
        return False

    async def cancel_agent(self) -> None:
        return None

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.delivered.append(str(content))

    async def resume_interrupt(self, user_input) -> None:
        return None

    async def shutdown_self(self) -> None:
        return None

    async def conclude_completed_round(self, member_count: int, task_count: int) -> None:
        return None


class FakePoll:
    def __init__(self):
        self.resumed = 0

    async def pause_polls(self) -> None:
        return None

    async def resume_polls(self) -> None:
        self.resumed += 1


def _blueprint(member_name: str, role: TeamRole):
    return SimpleNamespace(
        member_name=member_name,
        role=role,
        lifecycle="temporary",
        team_spec=SimpleNamespace(dispatch_mode="autonomous"),
        spec=SimpleNamespace(reliability=None),
    )


def _infra(db, bus, member_name: str) -> TeamInfra:
    infra = TeamInfra()
    infra.task_manager = TeamTaskManager(team_name=TEAM, member_name=member_name, db=db, messager=bus)
    return infra


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


async def _age_task(db, task_id: str, seconds: int) -> None:
    model = _get_task_model()
    async with db.session_local() as session:
        await session.execute(
            update(model)
            .where(model.task_id == task_id)
            .values(updated_at=get_current_time() - seconds * 1000)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Dispatcher-level selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispatcher_selects_handler_classes_by_mode(db, bus):
    host, poll = FakeHost(), FakePoll()
    blueprint = _blueprint(LEADER, TeamRole.LEADER)
    infra = _infra(db, bus, LEADER)

    autonomous = EventDispatcher(host, blueprint, infra, poll)
    assert type(autonomous.task_board) is TaskBoardHandler
    assert type(autonomous.stale_task) is StaleTaskHandler
    assert autonomous.dispatch_mode == "autonomous"

    scheduled = EventDispatcher(host, blueprint, infra, poll, dispatch_mode="scheduled")
    assert type(scheduled.task_board) is ScheduledTaskBoardHandler
    assert type(scheduled.stale_task) is ScheduledStaleTaskHandler
    assert scheduled.dispatch_mode == "scheduled"

    with pytest.raises(KeyError):
        EventDispatcher(host, blueprint, infra, poll, dispatch_mode="bogus")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_dispatch_routes_by_mode_handler_set(db, bus):
    """The same reviewer-targeted submit steers under autonomous, not scheduled."""
    host, poll = FakeHost(), FakePoll()
    blueprint = _blueprint("rev-1", TeamRole.TEAMMATE)
    infra = _infra(db, bus, "rev-1")
    event = EventMessage.from_event(
        TaskSubmittedForReviewEvent(team_name=TEAM, task_id="t1", member_name="dev-1", reviewer=["rev-1"])
    )

    autonomous = EventDispatcher(host, blueprint, infra, poll)
    await autonomous.dispatch(event)
    assert len(host.delivered) == 1  # autonomous: reviewer is steered to verify_task

    host.delivered.clear()
    scheduled = EventDispatcher(host, blueprint, infra, poll, dispatch_mode="scheduled")
    await scheduled.dispatch(event)
    assert host.delivered == []  # scheduled: the scheduler's mail owns the handoff


# ---------------------------------------------------------------------------
# Scheduled handler behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_board_event_keeps_polls_without_nudge(db, bus):
    host, poll = FakeHost(), FakePoll()
    blueprint = _blueprint(LEADER, TeamRole.LEADER)
    infra = _infra(db, bus, LEADER)
    graph = await infra.task_manager.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="t1", assignee="dev-1")]
    )
    assert graph.ok
    event = EventMessage.from_event(TaskCreatedEvent(team_name=TEAM, task_id="t1", status="pending"))

    autonomous = TaskBoardHandler(host, blueprint, infra, poll)
    await autonomous.on_task_board_event(event)
    assert len(host.delivered) == 1  # the leader surveys the board

    host.delivered.clear()
    scheduled = ScheduledTaskBoardHandler(host, blueprint, infra, poll)
    await scheduled.on_task_board_event(event)
    assert host.delivered == []  # awareness belongs to the scheduler
    assert poll.resumed >= 2  # polling stays alive in both modes


@pytest.mark.asyncio
@pytest.mark.level1
async def test_scheduled_poll_skips_leader_pending_sweep(db, bus):
    host, poll = FakeHost(), FakePoll()
    blueprint = _blueprint(LEADER, TeamRole.LEADER)
    infra = _infra(db, bus, LEADER)
    graph = await infra.task_manager.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="t1", assignee="dev-1")]
    )
    assert graph.ok
    await _age_task(db, "t1", seconds=3600)
    tick = InnerEventMessage(event_type=InnerEventType.POLL_TASK)

    autonomous = StaleTaskHandler(host, blueprint, infra, poll)
    await autonomous.on_poll_task(tick)
    assert len(host.delivered) == 1  # the stale-pending self-prompt fires

    host.delivered.clear()
    scheduled = ScheduledStaleTaskHandler(host, blueprint, infra, poll)
    await scheduled.on_poll_task(tick)
    assert host.delivered == []  # queued PENDING(assignee) is normal here


@pytest.mark.asyncio
@pytest.mark.level1
async def test_review_vote_key_is_scheduled_only(db, bus):
    assert TeamEvent.TASK_REVIEW_VOTE not in TaskBoardHandler.EVENT_METHOD_MAP
    assert ScheduledTaskBoardHandler.EVENT_METHOD_MAP[TeamEvent.TASK_REVIEW_VOTE] == "on_task_board_event"
    for key in (TeamEvent.TASK_SUBMITTED_FOR_REVIEW, TeamEvent.TASK_VERIFIED, TeamEvent.TASK_REVISION_REQUESTED):
        assert ScheduledTaskBoardHandler.EVENT_METHOD_MAP[key] == "on_task_board_event"
