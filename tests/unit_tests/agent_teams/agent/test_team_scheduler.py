# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the F_62 TeamScheduler (leader-side scheduled dispatch)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update

from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.scheduling import TeamScheduler
from openjiuwen.agent_teams.agent.scheduling.verdict import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNDECIDED,
    judge,
)
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskCompletedEvent,
    TaskListDrainedEvent,
)
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import _get_task_model
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard

TEAM = "sched_team"
LEADER = "leader"


# ---------------------------------------------------------------------------
# verdict.judge — pure math
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_judge_quorum_math():
    # 3 reviewers @ 2/3 -> quorum 2.
    assert judge(2, 0, 3, 2 / 3) == VERDICT_PASS
    assert judge(1, 1, 3, 2 / 3) == VERDICT_UNDECIDED
    assert judge(0, 2, 3, 2 / 3) == VERDICT_FAIL  # pass unreachable
    assert judge(1, 2, 3, 2 / 3) == VERDICT_FAIL
    # Single reviewer degenerates to first-verdict-wins.
    assert judge(1, 0, 1, 2 / 3) == VERDICT_PASS
    assert judge(0, 1, 1, 2 / 3) == VERDICT_FAIL
    assert judge(0, 0, 1, 2 / 3) == VERDICT_UNDECIDED
    # 2 reviewers @ 2/3 -> quorum 2: one fail dooms the round.
    assert judge(1, 1, 2, 2 / 3) == VERDICT_FAIL
    # Unanimity threshold.
    assert judge(2, 0, 3, 1.0) == VERDICT_UNDECIDED
    assert judge(0, 1, 3, 1.0) == VERDICT_FAIL
    # Defensive: no reviewers -> undecided.
    assert judge(0, 0, 0, 2 / 3) == VERDICT_UNDECIDED


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    token = set_session_id("sched_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM,
            display_name="Sched Team",
            leader_member_name=LEADER,
            dispatch_mode="scheduled",
        )
        members = {
            "dev-1": MemberMode.BUILD_MODE,
            "dev-2": MemberMode.BUILD_MODE,
            "planner": MemberMode.PLAN_MODE,
            "rev-1": MemberMode.BUILD_MODE,
            "rev-2": MemberMode.BUILD_MODE,
            "rev-3": MemberMode.BUILD_MODE,
        }
        for name, mode in members.items():
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


class FakeHost:
    """Records the scheduler's two host effects."""

    def __init__(self):
        self.leader_inputs: list[str] = []
        self.started_members: list[str] = []

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.leader_inputs.append(str(content))

    async def auto_start_member(self, member_name: str) -> bool:
        self.started_members.append(member_name)
        return True


def _build_scheduler(db, bus, **spec_overrides):
    """Assemble a TeamScheduler over a real task manager and fake host/mail."""
    task_manager = TeamTaskManager(
        team_name=TEAM,
        member_name=LEADER,
        db=db,
        messager=bus,
        dispatch_mode="scheduled",
    )
    message_manager = AsyncMock()
    message_manager.send_message = AsyncMock(return_value="mid-1")
    infra = TeamInfra()
    infra.task_manager = task_manager
    infra.message_manager = message_manager
    spec = SimpleNamespace(
        verify_vote_threshold=spec_overrides.get("verify_vote_threshold", 2 / 3),
        default_max_review_rounds=spec_overrides.get("default_max_review_rounds", 3),
        review_stall_timeout=spec_overrides.get("review_stall_timeout", 1800),
    )
    blueprint = SimpleNamespace(spec=spec, team_name=TEAM)
    host = FakeHost()
    scheduler = TeamScheduler(host, blueprint=blueprint, infra=infra)
    return scheduler, host, message_manager, task_manager


def _dm_targets(message_manager) -> list[tuple[str, str]]:
    """(recipient, content) pairs of every leader-identity handoff sent."""
    calls = []
    for call in message_manager.send_message.await_args_list:
        calls.append((call.kwargs["to_member_name"], call.kwargs["content"]))
    return calls


def _reviewer_mgr(db, bus, name):
    return TeamTaskManager(
        team_name=TEAM,
        member_name=name,
        db=db,
        messager=bus,
        dispatch_mode="scheduled",
    )


async def _age_task(db, task_id: str, seconds: int) -> None:
    """Backdate a task's updated_at so stall windows elapse in tests."""
    model = _get_task_model()
    async with db.session_local() as session:
        await session.execute(
            update(model)
            .where(model.task_id == task_id)
            .values(updated_at=get_current_time() - seconds * 1000)
        )
        await session.commit()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


# ---------------------------------------------------------------------------
# Start scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_activate_starts_assigned_pending_and_hands_off(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    graph = await tm.add_graph(
        [
            TaskGraphSpec(title="one", content="do one", task_id="a", assignee="dev-1"),
            TaskGraphSpec(title="two", content="do two", task_id="b", assignee="dev-2"),
        ]
    )
    assert graph.ok

    await scheduler.activate()

    assert (await tm.get("a")).status == TaskStatus.IN_PROGRESS.value
    assert (await tm.get("b")).status == TaskStatus.IN_PROGRESS.value
    handoffs = _dm_targets(mm)
    assert {target for target, _ in handoffs} == {"dev-1", "dev-2"}
    assert all("[a]" in content or "[b]" in content for _, content in handoffs)
    # Delivery lazily starts the member runtime.
    assert set(host.started_members) == {"dev-1", "dev-2"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_one_active_per_member_queues_second_task(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="first", content="c", task_id="a", assignee="dev-1"),
            TaskGraphSpec(title="second", content="c", task_id="b", assignee="dev-1"),
        ]
    )
    await scheduler.activate()

    a_status = (await tm.get("a")).status
    b_status = (await tm.get("b")).status
    # Exactly one of the two started; the other queues behind one-active.
    assert {a_status, b_status} == {TaskStatus.IN_PROGRESS.value, TaskStatus.PENDING.value}
    running = "a" if a_status == TaskStatus.IN_PROGRESS.value else "b"
    queued = "b" if running == "a" else "a"

    # Completing the running task lets the next scan start the queued one.
    author = _reviewer_mgr(db, bus, "dev-1")
    assert (await author.complete(running)).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert (await tm.get(queued)).status == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
@pytest.mark.level1
async def test_plan_mode_member_starts_into_planning(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="plan it", content="c", task_id="p", assignee="planner")])

    await scheduler.activate()

    assert (await tm.get("p")).status == TaskStatus.PLANNING.value
    handoffs = _dm_targets(mm)
    assert len(handoffs) == 1
    assert handoffs[0][0] == "planner"
    assert "submit_plan" in handoffs[0][1]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_blocked_task_starts_after_dependency_completes(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="up", content="c", task_id="up", assignee="dev-1"),
            TaskGraphSpec(title="down", content="c", task_id="down", assignee="dev-2", depends_on=("up",)),
        ]
    )
    await scheduler.activate()
    assert (await tm.get("down")).status == TaskStatus.BLOCKED.value

    author = _reviewer_mgr(db, bus, "dev-1")
    assert (await author.complete("up")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert (await tm.get("down")).status == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
@pytest.mark.level1
async def test_inactive_scheduler_ignores_events(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1")])

    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.POLL_TASK))
    assert (await tm.get("a")).status == TaskStatus.PENDING.value
    assert _dm_targets(mm) == []

    scheduler.deactivate()
    assert not scheduler.is_active


# ---------------------------------------------------------------------------
# Review scan: dispatch, settle, escalate, stall
# ---------------------------------------------------------------------------


async def _seed_review(db, bus, scheduler, tm, *, task_id="r", reviewers=("rev-1", "rev-2", "rev-3"), max_rounds=None):
    await tm.add_graph(
        [
            TaskGraphSpec(
                title="deliver",
                content="c",
                task_id=task_id,
                assignee="dev-1",
                reviewer=tuple(reviewers),
                max_review_rounds=max_rounds,
            )
        ]
    )
    await scheduler.activate()
    author = _reviewer_mgr(db, bus, "dev-1")
    assert (await author.complete(task_id)).ok
    assert (await tm.get(task_id)).status == TaskStatus.IN_REVIEW.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_review_dispatch_once_per_round_then_settle_pass(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await _seed_review(db, bus, scheduler, tm)

    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    review_dms = [(to, content) for to, content in _dm_targets(mm) if "verify_task" in content]
    assert {to for to, _ in review_dms} == {"rev-1", "rev-2", "rev-3"}

    # A second scan does not re-dispatch the same round.
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    review_dms_after = [(to, content) for to, content in _dm_targets(mm) if "verify_task" in content]
    assert len(review_dms_after) == len(review_dms)

    # Two pass votes reach the 2/3 quorum; the scan settles.
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "pass")).ok
    assert (await _reviewer_mgr(db, bus, "rev-2").verify_task("r", "pass")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    assert (await tm.get("r")).status == TaskStatus.COMPLETED.value
    # The author is told to report to the leader; the leader gets digests.
    report_dms = [to for to, content in _dm_targets(mm) if "send_message" in content and to == "dev-1"]
    assert report_dms
    assert any("[r]" in text for text in host.leader_inputs)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_review_fail_settles_rework_with_feedback(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus, default_max_review_rounds=3)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2"))

    # 2 reviewers @ 2/3 -> quorum 2; one fail makes pass unreachable.
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "fail", "broken build")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    task = await tm.get("r")
    assert task.status == TaskStatus.IN_PROGRESS.value
    rework_dms = [(to, content) for to, content in _dm_targets(mm) if to == "dev-1" and "broken build" in content]
    assert rework_dms


@pytest.mark.asyncio
@pytest.mark.level0
async def test_round_ceiling_escalates_to_leader(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",), max_rounds=1)

    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "fail", "not acceptable")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    # Round 1 >= ceiling 1: no auto-rework — the task stays in review and the
    # leader receives exactly one escalation carrying the feedback.
    assert (await tm.get("r")).status == TaskStatus.IN_REVIEW.value
    escalations = [text for text in host.leader_inputs if "not acceptable" in text]
    assert len(escalations) == 1

    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    escalations_after = [text for text in host.leader_inputs if "not acceptable" in text]
    assert len(escalations_after) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stalled_round_escalates_with_vote_status(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus, review_stall_timeout=60)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2", "rev-3"))
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "pass")).ok

    await _age_task(db, "r", seconds=120)
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    assert (await tm.get("r")).status == TaskStatus.IN_REVIEW.value
    stalls = [text for text in host.leader_inputs if "rev-2" in text and "rev-3" in text]
    assert len(stalls) == 1
    # Deduplicated per round.
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert len([text for text in host.leader_inputs if "rev-2" in text and "rev-3" in text]) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_silent_reviewers_get_renudged_once_per_window(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus, review_stall_timeout=3600)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2"))
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "pass")).ok

    await _age_task(db, "r", seconds=700)
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    renudges = [(to, content) for to, content in _dm_targets(mm) if "rev-2" == to and "[r]" in content]
    # First DM is the review request, second is exactly one reminder.
    assert len(renudges) == 2


# ---------------------------------------------------------------------------
# Leader digests via transport events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_completion_event_digests_once(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1")])
    await scheduler.activate()
    author = _reviewer_mgr(db, bus, "dev-1")
    assert (await author.complete("a")).ok

    event = EventMessage.from_event(
        TaskCompletedEvent(team_name=TEAM, task_id="a", member_name="dev-1")
    )
    await scheduler.on_event(event)
    await scheduler.on_event(event)

    digests = [text for text in host.leader_inputs if "[a]" in text]
    assert len(digests) == 1
    # The board drained: the all-done wrap-up rides on the same digest pass.
    assert any("1" in text for text in host.leader_inputs if text not in digests)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_list_drained_announces_once(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await scheduler.activate()
    event = EventMessage.from_event(TaskListDrainedEvent(team_name=TEAM, task_count=3))
    await scheduler.on_event(event)
    await scheduler.on_event(event)
    assert len(host.leader_inputs) == 1
