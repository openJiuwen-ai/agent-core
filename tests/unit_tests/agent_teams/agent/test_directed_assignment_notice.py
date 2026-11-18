# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Directed assignment notices in autonomous dispatch (issues #6719 / #6720).

``TeamTaskManager.assign`` moves a task into its entry state (``in_progress``,
or ``planning`` for a PLAN_MODE assignee) *before* publishing TASK_CLAIMED,
and ``reassign`` keeps a running task's state — so the assignee's
``TaskBoardHandler.on_task_claimed`` must deliver the notice for an entered
task too, not only for ``pending``. A pending-only gate dropped every directed
assignment: the member was never woken and the team round never finished.

An ``in_progress`` notice renders the owner nudge (``stale_claim_self``) — it
names the task and points at ``view_task`` instead of telling an owner to
``claim`` work it already holds. The plan gate (``planning``) is not announced
by this handler.

Scheduled dispatch is deliberately excluded from the widening: the
scheduler's handoff already mails the owner of an entered task its start
instruction, so the scheduled handler keeps announcing pending assignments
only.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.handlers import (
    ScheduledTaskBoardHandler,
    TaskBoardHandler,
)
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import EventMessage, TaskClaimedEvent
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard

TEAM = "assign_notice_team"
LEADER = "team-leader"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("assign_notice_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM, display_name=TEAM, leader_member_name=LEADER
        )
        for name, mode in (
            (LEADER, MemberMode.BUILD_MODE),
            ("dev-1", MemberMode.BUILD_MODE),
            ("dev-2", MemberMode.BUILD_MODE),
        ):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                mode=mode.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


class FakeHost:
    """Duck-typed DispatcherHost recording round deliveries."""

    def __init__(self) -> None:
        self.delivered: list[str] = []
        self.steers: list[bool] = []

    def is_agent_ready(self) -> bool:
        return True

    def is_agent_running(self) -> bool:
        return False

    def idle_seconds(self) -> float | None:
        return 0.0

    def has_in_flight_round(self) -> bool:
        return False

    def has_pending_interrupt(self) -> bool:
        return False

    async def cancel_agent(self) -> None:
        return None

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.delivered.append(str(content))
        self.steers.append(use_steer)

    async def resume_interrupt(self, user_input) -> None:
        return None

    async def shutdown_self(self) -> None:
        return None

    async def conclude_completed_round(self, member_count: int, task_count: int) -> None:
        return None


class FakePoll:
    def __init__(self) -> None:
        self.resumed = 0

    async def pause_polls(self) -> None:
        return None

    async def resume_polls(self) -> None:
        self.resumed += 1


def _blueprint(member_name: str, dispatch_mode: str = "autonomous"):
    return SimpleNamespace(
        member_name=member_name,
        role=TeamRole.TEAMMATE,
        lifecycle="temporary",
        team_spec=SimpleNamespace(dispatch_mode=dispatch_mode),
        spec=SimpleNamespace(stale_claim_idle_timeout=600, stale_pending_idle_timeout=600),
    )


def _infra(db, bus, member_name: str) -> TeamInfra:
    infra = TeamInfra()
    infra.task_manager = TeamTaskManager(team_name=TEAM, member_name=member_name, db=db, messager=bus)
    backend = MagicMock()
    backend.is_human_agent = AsyncMock(return_value=False)
    backend.is_passive_human = AsyncMock(return_value=False)
    infra.team_backend = backend
    return infra


def _claimed_event(task_id: str, member_name: str) -> EventMessage:
    return EventMessage.from_event(
        TaskClaimedEvent(team_name=TEAM, task_id=task_id, member_name=member_name)
    )


def _handler(handler_cls, db, bus, member_name: str, dispatch_mode: str = "autonomous"):
    host = FakeHost()
    handler = handler_cls(
        host, _blueprint(member_name, dispatch_mode), _infra(db, bus, member_name), FakePoll()
    )
    return host, handler


# ---------------------------------------------------------------------------
# Autonomous dispatch: assign / reassign must reach the assignee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_directed_assignment_wakes_the_assignee(db, bus):
    """``assign()`` must reach the assignee (issue #6719).

    ``assign`` CAS-es the task to ``in_progress`` before publishing
    TASK_CLAIMED, so the notice must survive that status; the assignee gets
    the owner nudge, which carries no claim instruction.
    """
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="angle-3", content="c", task_id="angle-3")]
        )
    ).ok

    assigned = await leader_infra.task_manager.assign("angle-3", "dev-2")
    assert assigned.ok, assigned.reason
    stored = await leader_infra.task_manager.get("angle-3")
    assert stored.status == "in_progress"
    assert stored.assignee == "dev-2"

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("angle-3", "dev-2"))

    assert len(host.delivered) == 1, "assignment notice must be delivered exactly once"
    assert "angle-3" in host.delivered[0]
    assert "claim_task(status=claimed)" not in host.delivered[0]
    assert host.steers == [False], "an entered owner is nudged, never steered"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reassigned_in_progress_task_is_announced_to_new_owner(db, bus):
    """A running task handed to a new owner must wake it (issue #6720).

    ``reassign`` only swaps the assignee and keeps ``in_progress``; the new
    owner gets TASK_CLAIMED with the task already entered, so the notice is
    the owner nudge — task id + title, no claim instruction.
    """
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="final-synthesis", content="c", task_id="t1", assignee="dev-1")]
        )
    ).ok
    assert (await leader_infra.task_manager.start_task("t1")).ok

    handled = await leader_infra.task_manager.reassign("t1", "dev-2")
    assert handled.ok, handled.reason
    task = await leader_infra.task_manager.get("t1")
    assert task.status == "in_progress"
    assert task.assignee == "dev-2"

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("t1", "dev-2"))

    assert len(host.delivered) == 1
    assert "t1" in host.delivered[0]
    assert "claim_task(status=claimed)" not in host.delivered[0]
    assert host.steers == [False], "a new owner already running is not interrupted"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pending_assignment_is_still_announced(db, bus):
    """Control: a pending assignment keeps the existing announcement path."""
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="fresh", content="c", task_id="t2", assignee="dev-2")]
        )
    ).ok

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("t2", "dev-2"))

    assert len(host.delivered) == 1
    assert "fresh" in host.delivered[0]
    assert "claim_task" in host.delivered[0]
    assert host.steers == [True], "a pending assignment is the start of the work"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pending_notice_for_task_no_longer_owned_is_not_announced(db, bus):
    """A pending notice that lost a race against a later handoff stays silent.

    ``assign`` writes the assignee before publishing TASK_CLAIMED; if the row
    moved on to another member by delivery time, waking this one would start
    duplicate work on a task it no longer owns.
    """
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="handed-over", content="c", task_id="t-race", assignee="dev-1")]
        )
    ).ok
    assert (await leader_infra.task_manager.reassign("t-race", "dev-2")).ok

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-1")
    await handler.on_task_claimed(_claimed_event("t-race", "dev-1"))

    assert host.delivered == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_plan_gate_assignment_is_not_announced(db, bus):
    """A plan-gate assignment is not announced by this handler."""
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="draft-plan", content="c", task_id="t-plan")]
        )
    ).ok
    # Seed a plan-mode assignee directly: add_graph seeds PENDING, then a
    # plain assign() would enter planning — but a plan-mode member cannot be
    # seeded through the public path without one, so flip the row's assignee
    # and status through the same DAO assign() uses.
    await db.member.create_member(
        member_name="planner",
        team_name=TEAM,
        display_name="planner",
        agent_card=AgentCard().model_dump_json(),
        status="READY",
        mode=MemberMode.PLAN_MODE.value,
    )
    assigned = await leader_infra.task_manager.assign("t-plan", "planner")
    assert assigned.ok, assigned.reason
    stored = await leader_infra.task_manager.get("t-plan")
    assert stored.status == "planning"

    host, handler = _handler(TaskBoardHandler, db, bus, "planner")
    await handler.on_task_claimed(_claimed_event("t-plan", "planner"))

    assert host.delivered == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_blocked_assignment_is_not_announced(db, bus):
    """A blocked assignment stays silent; the board wakes the member on unblock."""
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [
                TaskGraphSpec(title="gate", content="c", task_id="t-gate"),
                TaskGraphSpec(title="waiting", content="c", task_id="t-wait", assignee="dev-2", depends_on=("t-gate",)),
            ]
        )
    ).ok
    stored = await leader_infra.task_manager.get("t-wait")
    assert stored.status == "blocked"

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("t-wait", "dev-2"))

    assert host.delivered == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_notice_for_task_no_longer_owned_is_not_announced(db, bus):
    """A notice that lost a race against a later reassignment stays silent.

    TASK_CLAIMED addressed to this member, but the task is already owned by
    someone else — waking the old assignee for foreign work would be wrong
    (its own TASK_REVOKED steers it off instead).
    """
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="mine-now", content="c", task_id="t3")]
        )
    ).ok
    assert (await leader_infra.task_manager.assign("t3", "dev-1")).ok

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("t3", "dev-2"))

    assert host.delivered == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_terminal_assignment_is_not_announced(db, bus):
    """A task that finished before the notice was processed needs no round."""
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="already-done", content="c", task_id="t4")]
        )
    ).ok
    assert (await leader_infra.task_manager.assign("t4", "dev-2")).ok
    assert (await _infra(db, bus, "dev-2").task_manager.complete("t4", summary="done")).ok

    host, handler = _handler(TaskBoardHandler, db, bus, "dev-2")
    await handler.on_task_claimed(_claimed_event("t4", "dev-2"))

    assert host.delivered == []


# ---------------------------------------------------------------------------
# Scheduled dispatch: the scheduler's handoff owns entered tasks (no double)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_mode_does_not_announce_an_entered_assignment(db, bus):
    """Scheduled dispatch keeps the pending-only gate (issues #6719 / #6720).

    The scheduler's handoff already mails the owner of an entered task its
    start instruction; announcing the transient TASK_CLAIMED next to that
    mail would double-deliver and burn a second round.
    """
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="entered", content="c", task_id="t5")]
        )
    ).ok
    assert (await leader_infra.task_manager.assign("t5", "dev-2")).ok

    host, handler = _handler(ScheduledTaskBoardHandler, db, bus, "dev-2", dispatch_mode="scheduled")
    await handler.on_task_claimed(_claimed_event("t5", "dev-2"))

    assert host.delivered == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_scheduled_mode_still_announces_a_pending_assignment(db, bus):
    """The pending announcement itself is unchanged under scheduled dispatch."""
    leader_infra = _infra(db, bus, LEADER)
    assert (
        await leader_infra.task_manager.add_graph(
            [TaskGraphSpec(title="queued", content="c", task_id="t6", assignee="dev-2")]
        )
    ).ok

    host, handler = _handler(ScheduledTaskBoardHandler, db, bus, "dev-2", dispatch_mode="scheduled")
    await handler.on_task_claimed(_claimed_event("t6", "dev-2"))

    assert len(host.delivered) == 1
    assert "t6" in host.delivered[0]
