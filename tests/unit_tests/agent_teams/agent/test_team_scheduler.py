# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the F_62 TeamScheduler (leader-side scheduled dispatch)."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
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
)
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskCompletedEvent,
    TaskListDrainedEvent,
)
from openjiuwen.agent_teams.schema.status import MemberMode, MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import _get_task_model
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard

TEAM = "sched_team"
LEADER = "leader"


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
        self.harness = SimpleNamespace(find_rails=lambda _rail_type: [])

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.leader_inputs.append(str(content))

    async def auto_start_member(self, member_name: str) -> bool:
        self.started_members.append(member_name)
        return True


def _build_scheduler(
    db,
    bus,
    *,
    build_context=None,
    review_feedback_rail=None,
    skill_create_rail=None,
    **spec_overrides,
):
    """Assemble a TeamScheduler over a real task manager and fake host/mail."""
    task_manager = TeamTaskManager(
        team_name=TEAM,
        member_name=LEADER,
        db=db,
        messager=bus,
        dispatch_mode="scheduled",
    )
    message_manager = AsyncMock()
    message_manager.member_name = LEADER
    message_manager.send_message = AsyncMock(return_value="mid-1")
    # The handoff scan asks the mailbox what the owner already holds; an
    # empty mailbox is the default, tests seed rows via ``_mailbox_handoff``.
    message_manager.get_messages = AsyncMock(return_value=[])
    infra = TeamInfra()
    infra.task_manager = task_manager
    infra.message_manager = message_manager
    spec = SimpleNamespace(
        team_name=TEAM,
        default_max_review_rounds=spec_overrides.get("default_max_review_rounds", 3),
        review_stall_timeout=spec_overrides.get("review_stall_timeout", 1800),
    )
    spec.agents = None
    infra.team_backend = AsyncMock(team_name=TEAM)
    blueprint = SimpleNamespace(spec=spec, team_name=TEAM)
    host = FakeHost()
    if review_feedback_rail is not None:
        def _find_rails(rail_type):
            if rail_type.__name__ == "TeamSkillEvolutionRail":
                return [review_feedback_rail]
            if rail_type.__name__ == "TeamSkillCreateRail" and skill_create_rail is not None:
                return [skill_create_rail]
            return []

        host.harness = SimpleNamespace(find_rails=_find_rails)
    scheduler = TeamScheduler(
        host,
        blueprint=blueprint,
        infra=infra,
        build_context=build_context,
    )
    return scheduler, host, message_manager, task_manager


def _dm_targets(message_manager) -> list[tuple[str, dict]]:
    """(recipient, delivery meta) pairs of every leader-identity handoff sent.

    The scheduler decides *which template binds to which task* and stores that
    in ``meta``; the wording is rendered from the template files at delivery
    (F_63). So these assertions target the decision, not the prose — the
    rendering contract is covered by the message-template tests.
    """
    calls = []
    for call in message_manager.send_message.await_args_list:
        calls.append((call.kwargs["to_member_name"], call.kwargs["meta"]))
    return calls


def _mailbox_handoff(to_member: str, task_id: str, *, template: str = "scheduler_task_start") -> SimpleNamespace:
    """A stored leader start handoff row as ``get_messages`` would return it."""
    meta = json.dumps({"template": template, "refs": {"task": task_id}})
    return SimpleNamespace(from_member_name=LEADER, to_member_name=to_member, content="", meta=meta)


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
    assert all(meta["template"] == "scheduler_task_start" for _, meta in handoffs)
    assert {meta["refs"]["task"] for _, meta in handoffs} == {"a", "b"}
    # Delivery lazily starts the member runtime.
    assert set(host.started_members) == {"dev-1", "dev-2"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_handoffs_carry_no_body_only_meta(db, bus):
    """A templated handoff stores no text: meta is the single source (F_63)."""
    scheduler, _host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="do one", task_id="a", assignee="dev-1")])

    await scheduler.activate()

    sent = mm.send_message.await_args_list
    assert sent
    for call in sent:
        assert call.kwargs["content"] == ""
        assert call.kwargs["meta"]["template"]
        assert call.kwargs["meta"]["refs"]["task"] == "a"


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
    assert handoffs[0][1]["template"] == "scheduler_task_start_plan"


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
    # Reviewers are dispatched as fire-and-forget temp harnesses.
    # One-vote veto: all three reviewers must vote; any fail fails the round.
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "pass")).ok
    assert (await _reviewer_mgr(db, bus, "rev-2").verify_task("r", "pass")).ok
    assert (await _reviewer_mgr(db, bus, "rev-3").verify_task("r", "pass")).ok
    author_dms_before = [meta for to, meta in _dm_targets(mm) if to == "dev-1"]
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    assert (await tm.get("r")).status == TaskStatus.COMPLETED.value
    # A pass does not wake the author (F_119); the leader gets the digest.
    assert [meta for to, meta in _dm_targets(mm) if to == "dev-1"] == author_dms_before
    assert any("[r]" in text for text in host.leader_inputs)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_review_fail_settles_rework_with_feedback(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus, default_max_review_rounds=3)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2"))

    # One-vote veto: one fail among all reviewers fails the round.
    # Both reviewers must vote before settlement.
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "fail", "broken build")).ok
    assert (await _reviewer_mgr(db, bus, "rev-2").verify_task("r", "pass")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    task = await tm.get("r")
    assert task.status == TaskStatus.IN_PROGRESS.value
    rework_dms = [(to, meta) for to, meta in _dm_targets(mm) if to == "dev-1" and meta["template"] == "scheduler_rework"]
    assert rework_dms
    # The aggregated fail feedback rides in params — a vote-round aggregate the
    # task row cannot answer at delivery time.
    assert "broken build" in rework_dms[0][1]["params"]["feedback"]
    assert rework_dms[0][1]["params"]["max_rounds"] == "3"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_review_fail_dispatches_aggregated_feedback_to_mounted_rail(db, bus):
    rail = SimpleNamespace(
        review_feedback_evolution_enabled=True,
        handle_review_feedback=AsyncMock(),
        bind_review_feedback_skill_create_rail=lambda _rail: None,
    )
    scheduler, _host, _mm, tm = _build_scheduler(
        db,
        bus,
        build_context=SimpleNamespace(session_id="sched_session"),
        review_feedback_rail=rail,
        default_max_review_rounds=3,
    )
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2"))
    assert (await _reviewer_mgr(db, bus, "rev-1").verify_task("r", "fail", "broken build")).ok
    assert (await _reviewer_mgr(db, bus, "rev-2").verify_task("r", "pass")).ok

    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    await asyncio.sleep(0)
    if scheduler._review_feedback_tasks:
        await asyncio.gather(*scheduler._review_feedback_tasks)

    rail.handle_review_feedback.assert_awaited_once()
    payload = rail.handle_review_feedback.await_args.args[0]
    assert payload["task_id"] == "r"
    assert payload["review_round"] == 1
    assert payload["session_id"] == "sched_session"
    assert "broken build" in payload["feedback"]


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

    handoffs_to_silent = [(to, meta) for to, meta in _dm_targets(mm) if to == "rev-2"]
    templates = [meta["template"] for _, meta in handoffs_to_silent]
    # Renudge still delivered via DM.
    assert "scheduler_review_renudge" in templates


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
async def test_completion_digest_carries_no_board_count_snapshot(db, bus):
    """The digest is queued behind the leader's round, so it must not freeze a board count.

    A count taken at completion reads as current progress when the digest
    lands after newer tasks were created; the leader queries the live board.
    """
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1"),
            TaskGraphSpec(title="two", content="c", task_id="b", assignee="dev-1"),
        ]
    )
    await scheduler.activate()
    author = _reviewer_mgr(db, bus, "dev-1")
    assert (await author.complete("a")).ok

    await scheduler.on_event(EventMessage.from_event(TaskCompletedEvent(team_name=TEAM, task_id="a", member_name="dev-1")))

    digest = next(text for text in host.leader_inputs if "[a]" in text)
    assert "剩余" not in digest
    assert "view_task" in digest


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_list_drained_announces_once(db, bus):
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await scheduler.activate()
    event = EventMessage.from_event(TaskListDrainedEvent(team_name=TEAM, task_count=3))
    await scheduler.on_event(event)
    await scheduler.on_event(event)
    assert len(host.leader_inputs) == 1


# ---------------------------------------------------------------------------
# settle_review_tally — pure math (F_73)
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_settle_review_tally_binary_pool():
    """Binary pool: all voted, any fail → FAIL."""
    from openjiuwen.agent_teams.agent.scheduling.verdict import settle_review_tally

    # 3 verifiers, all pass
    assert settle_review_tally({
        "verdict_pass_count": 3, "verdict_fail_count": 0,
        "verdict_total": 3, "verdict_voted": 3,
        "inspector_count": 0, "inspector_voted": 0, "inspector_avg": None,
    }) == VERDICT_PASS

    # 3 verifiers, 1 fail → FAIL
    assert settle_review_tally({
        "verdict_pass_count": 2, "verdict_fail_count": 1,
        "verdict_total": 3, "verdict_voted": 3,
        "inspector_count": 0, "inspector_voted": 0, "inspector_avg": None,
    }) == VERDICT_FAIL

    # 3 verifiers, only 2 voted → UNDECIDED
    assert settle_review_tally({
        "verdict_pass_count": 2, "verdict_fail_count": 0,
        "verdict_total": 3, "verdict_voted": 2,
        "inspector_count": 0, "inspector_voted": 0, "inspector_avg": None,
    }) == VERDICT_UNDECIDED


@pytest.mark.level0
def test_settle_review_tally_inspector_pool():
    """Inspector pool: all voted, avg ≥ 0.85 → PASS."""
    from openjiuwen.agent_teams.agent.scheduling.verdict import settle_review_tally

    # 2 inspectors, avg 0.90 → PASS
    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 2, "inspector_voted": 2, "inspector_avg": 0.90,
    }) == VERDICT_PASS

    # 2 inspectors, avg 0.80 → FAIL
    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 2, "inspector_voted": 2, "inspector_avg": 0.80,
    }) == VERDICT_FAIL

    # 2 inspectors, only 1 voted → UNDECIDED
    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 2, "inspector_voted": 1, "inspector_avg": 0.95,
    }) == VERDICT_UNDECIDED


@pytest.mark.level0
def test_settle_review_tally_mixed():
    """Mixed binary + inspector pool: both must pass."""
    from openjiuwen.agent_teams.agent.scheduling.verdict import settle_review_tally

    # Both pass
    assert settle_review_tally({
        "verdict_pass_count": 2, "verdict_fail_count": 0,
        "verdict_total": 2, "verdict_voted": 2,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": 0.87,
    }) == VERDICT_PASS

    # Binary pass, inspector fail
    assert settle_review_tally({
        "verdict_pass_count": 2, "verdict_fail_count": 0,
        "verdict_total": 2, "verdict_voted": 2,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": 0.72,
    }) == VERDICT_FAIL

    # Binary fail, inspector pass
    assert settle_review_tally({
        "verdict_pass_count": 1, "verdict_fail_count": 1,
        "verdict_total": 2, "verdict_voted": 2,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": 0.90,
    }) == VERDICT_FAIL

    # Binary not yet fully voted → UNDECIDED (even if inspector is done)
    assert settle_review_tally({
        "verdict_pass_count": 1, "verdict_fail_count": 0,
        "verdict_total": 2, "verdict_voted": 1,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": 0.90,
    }) == VERDICT_UNDECIDED

    # No reviewers at all → PASS (edge case)
    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 0, "inspector_voted": 0, "inspector_avg": None,
    }) == VERDICT_PASS


@pytest.mark.level0
def test_settle_review_tally_boundary():
    """Inspector avg exactly at threshold (0.85) → PASS."""
    from openjiuwen.agent_teams.agent.scheduling.verdict import settle_review_tally

    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": 0.85,
    }) == VERDICT_PASS


@pytest.mark.level0
def test_settle_review_tally_inspector_avg_none():
    """Inspector voted but avg is None (broken tally) → FAIL (safety)."""
    from openjiuwen.agent_teams.agent.scheduling.verdict import settle_review_tally

    assert settle_review_tally({
        "verdict_total": 0, "verdict_voted": 0, "verdict_pass_count": 0, "verdict_fail_count": 0,
        "inspector_count": 1, "inspector_voted": 1, "inspector_avg": None,
    }) == VERDICT_FAIL


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_list_drained_waits_for_task_feedback_then_finalizes_team_feedback(db, bus):
    order: list[str] = []

    class _FeedbackRail:
        review_feedback_evolution_enabled = True

        def bind_review_feedback_skill_create_rail(self, _rail):
            return None

        async def handle_review_feedback(self, _payload):
            order.append("task")

        async def finalize_review_feedback(self, payload):
            order.append(f"team:{payload['team_id']}")

    rail = _FeedbackRail()
    build_context = SimpleNamespace(session_id="sched_session")
    scheduler, _host, _mm, _tm = _build_scheduler(
        db,
        bus,
        build_context=build_context,
        review_feedback_rail=rail,
    )
    await scheduler.activate()
    pending = asyncio.create_task(
        scheduler._invoke_review_feedback_rail(
            rail.handle_review_feedback,
            {"task_id": "r", "review_round": 1},
        )
    )
    scheduler._review_feedback_tasks.add(pending)
    pending.add_done_callback(scheduler._review_feedback_tasks.discard)

    event = EventMessage.from_event(TaskListDrainedEvent(team_name=TEAM, task_count=1))
    await scheduler.on_event(event)
    await scheduler.on_event(event)

    assert order == ["task", f"team:{TEAM}"]


# ---------------------------------------------------------------------------
# F_118: retirement scan — request retirement of task-scoped members
# ---------------------------------------------------------------------------


def _retirement_scheduler(db, bus, *, candidates, unread=frozenset()):
    """Scheduler whose backend reports ``candidates`` as retirable and ``unread`` as pending input."""
    from openjiuwen.agent_teams.tools.team import MemberOpResult

    scheduler, host, mm, tm = _build_scheduler(db, bus)
    backend = scheduler._infra.team_backend
    live = list(candidates)
    backend.list_retirable_members = AsyncMock(side_effect=lambda: list(live))

    async def _request(member_name: str):
        # The real row flips to SHUTDOWN_REQUESTED and leaves the live set;
        # mirror that so the fixpoint loop converges like production.
        live[:] = [row for row in live if row[0] != member_name]
        return MemberOpResult.success()

    backend.shutdown_member = AsyncMock(side_effect=_request)
    mm.members_with_unread = AsyncMock(return_value=set(unread))
    return scheduler, backend, tm


@pytest.mark.asyncio
@pytest.mark.level0
async def test_retirement_requested_once_board_is_empty(db, bus):
    """READY + no open task + nothing unread -> one ordinary shutdown_member request."""
    scheduler, backend, tm = _retirement_scheduler(db, bus, candidates=[("dev-1", "ready", "dormant")])

    await scheduler.activate()

    backend.shutdown_member.assert_awaited_once_with("dev-1")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_retirement_waits_for_open_tasks_including_review(db, bus):
    """A task under review (or pending / blocked) keeps its author around."""
    scheduler, backend, tm = _retirement_scheduler(db, bus, candidates=[("dev-1", "ready", "shutdown")])
    reviewer = ({"type": "verifier", "reviewer_id": "v1", "instruction": ""},)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1", reviewer=reviewer)])
    await scheduler.activate()  # starts the task; dev-1 now has IN_PROGRESS work
    backend.shutdown_member.assert_not_awaited()

    await _reviewer_mgr(db, bus, "dev-1").complete("a")  # IN_PROGRESS -> IN_REVIEW
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    assert (await tm.get("a")).status == TaskStatus.IN_REVIEW.value
    backend.shutdown_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_retirement_waits_for_unread_input(db, bus):
    """The just-woken window: READY before the waking message was read must not retire."""
    scheduler, backend, _tm = _retirement_scheduler(
        db, bus, candidates=[("dev-1", "ready", "dormant")], unread={"dev-1"}
    )

    await scheduler.activate()

    backend.shutdown_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_retirement_ignores_members_without_policy(db, bus):
    """Nothing to retire -> no board queries, no requests."""
    scheduler, backend, _tm = _retirement_scheduler(db, bus, candidates=[])

    await scheduler.activate()

    backend.shutdown_member.assert_not_awaited()
    scheduler._infra.message_manager.members_with_unread.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_handoff_to_dormant_member_wakes_it_through_auto_start(db, bus):
    """A new task for a retired member is an explicit address: the handoff starts it."""
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await db.member.update_member_status("dev-1", TEAM, MemberStatus.SHUTDOWN_REQUESTED.value)
    await db.member.update_member_status("dev-1", TEAM, MemberStatus.DORMANT.value)
    await tm.add_graph([TaskGraphSpec(title="again", content="c", task_id="z", assignee="dev-1")])

    await scheduler.activate()

    assert (await tm.get("z")).status == TaskStatus.IN_PROGRESS.value
    assert host.started_members == ["dev-1"]


# ---------------------------------------------------------------------------
# Handoff scan: reassignment of an active task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reassigned_active_task_is_handed_to_its_new_owner(db, bus):
    """An ``update_task`` reassignment keeps the task IN_PROGRESS, so the start
    scan never fires for the new owner; the handoff scan mails it the same
    start instruction and lazily starts it."""
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1")])
    await scheduler.activate()
    assert _dm_targets(mm) == [("dev-1", {"template": "scheduler_task_start", "refs": {"task": "a"}})]

    assert (await tm.reassign("a", "dev-2")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    task = await tm.get("a")
    assert task.status == TaskStatus.IN_PROGRESS.value
    assert task.assignee == "dev-2"
    assert _dm_targets(mm)[1:] == [("dev-2", {"template": "scheduler_task_start", "refs": {"task": "a"}})]
    assert host.started_members == ["dev-1", "dev-2"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_handoff_scan_sends_each_owner_its_start_once(db, bus):
    """Repeated wakes do not re-mail an owner that already holds its handoff."""
    scheduler, host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1")])
    await scheduler.activate()
    assert (await tm.reassign("a", "dev-2")).ok

    for _ in range(3):
        await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))

    assert [target for target, _ in _dm_targets(mm)] == ["dev-1", "dev-2"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_restarted_scheduler_trusts_the_mailbox_before_re_sending_a_handoff(db, bus):
    """A fresh scheduler (empty memo) re-learns handoffs from the mailbox: an
    owner whose start row is already there gets nothing, an owner without one
    gets its start. Read or unread does not matter."""
    seeded, _host, mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1"),
            TaskGraphSpec(title="two", content="c", task_id="b", assignee="dev-2"),
        ]
    )
    await seeded.activate()
    assert (await tm.reassign("b", "planner")).ok

    scheduler, host, mm, tm = _build_scheduler(db, bus)

    async def _get_messages(*, to_member_name: str, from_member_name: str):
        assert from_member_name == LEADER
        if to_member_name == "dev-1":
            return [_mailbox_handoff("dev-1", "a")]
        return []

    mm.get_messages = AsyncMock(side_effect=_get_messages)
    await scheduler.activate()

    assert _dm_targets(mm) == [("planner", {"template": "scheduler_task_start", "refs": {"task": "b"}})]
    assert host.started_members == ["planner"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_handoff_scan_recognises_a_plan_gate_start(db, bus):
    """A plan-mode owner's start row is the plan template; it still counts as
    the owner having been handed the task."""
    seeded, _host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="plan it", content="c", task_id="p", assignee="planner")])
    await seeded.activate()
    assert (await tm.get("p")).status == TaskStatus.PLANNING.value

    scheduler, host, mm, tm = _build_scheduler(db, bus)
    mm.get_messages = AsyncMock(return_value=[_mailbox_handoff("planner", "p", template="scheduler_task_start_plan")])
    await scheduler.activate()

    assert _dm_targets(mm) == []
    assert host.started_members == []


# ---------------------------------------------------------------------------
# Temp reviewer trajectory attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_runs_under_its_own_trajectory_subject(db, bus, monkeypatch):
    from openjiuwen.agent_teams.agent.scheduling.scheduler import TeamScheduler as _Scheduler
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness
    from openjiuwen.agent_teams.rails.elements import OBSERVABILITY, TEAM_OBSERVABILITY
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.harness.execution_subject import current_execution_subject

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    scheduler._blueprint.spec.agents = {"teammate": DeepAgentSpec()}
    scheduler._blueprint.spec.dispatch_mode = "scheduled"
    scheduler._blueprint.language = "cn"
    scheduler._infra.team_backend.db = db
    scheduler._infra.team_backend.workspace_cache = None
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",))
    # The scan dispatches a real temp reviewer; keep it from building one.
    monkeypatch.setattr(_Scheduler, "_dispatch_to_reviewer", AsyncMock())

    built_specs = []
    run_subjects = []
    run_sessions = []

    class _FakeHarness:
        async def run_once(self, _prompt, *, team_session=None):
            run_subjects.append(current_execution_subject())
            run_sessions.append(team_session)
            return {"output": "voted"}

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, **_kwargs):
        built_specs.append(agent_spec)
        return _FakeHarness()

    monkeypatch.setattr(TeamHarness, "build", staticmethod(_fake_build))

    await scheduler._spawn_temp_reviewer("rev-1", await tm.get("r"))
    await scheduler._spawn_temp_reviewer("rev-1", await tm.get("r"))

    # Runs on the team session, so the observability rail finds the run root.
    assert run_sessions[0].get_session_id() == "sched_session"
    # Each run gets its own card id, so no run restores another's context.
    card_ids = {spec.card.id for spec in built_specs}
    assert len(card_ids) == 2
    assert all(spec.card.name == "rev-1" for spec in built_specs)
    rail_types = [rail.type for rail in built_specs[0].rails]
    assert TEAM_OBSERVABILITY in rail_types
    assert OBSERVABILITY in rail_types
    subject = run_subjects[0]
    review_round = (await tm.get("r")).review_round
    assert subject.subject_id == f"team-reviewer:sched_session:{TEAM}:r:rev-1:r{review_round}"
    assert subject.display_name == f"rev-1 · r · R{review_round}"
    # Still a team lane, parented on the author whose deliverable is reviewed.
    assert subject.kind == "team_member"
    assert subject.parent_subject_id == f"team-member:sched_session:{TEAM}:dev-1"
    assert subject.session_id == "sched_session"
    # The scope is released once the reviewer run returns.
    assert current_execution_subject() is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_subject_is_per_task_round_and_attempt(db, bus, monkeypatch):
    """Every fresh-context reviewer run owns its subject.

    ``reviewer_id`` is numbered per task, so two tasks' first reviewers share a
    name and may run concurrently; and each round / retry attempt is a new
    harness with a fresh context. Sharing one subject would interleave their
    windows in one trajectory lane.
    """
    from openjiuwen.agent_teams.agent.scheduling.scheduler import TeamScheduler as _Scheduler
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.harness.execution_subject import current_execution_subject

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    scheduler._blueprint.spec.agents = {"teammate": DeepAgentSpec()}
    scheduler._blueprint.spec.dispatch_mode = "scheduled"
    scheduler._blueprint.language = "cn"
    scheduler._infra.team_backend.db = db
    scheduler._infra.team_backend.workspace_cache = None
    monkeypatch.setattr(_Scheduler, "_dispatch_to_reviewer", AsyncMock())
    # Two tasks whose first reviewer carries the same per-task id.
    await tm.add_graph(
        [
            TaskGraphSpec(title="deliver a", content="c", task_id="a", assignee="dev-1", reviewer=("verifier_1",)),
            TaskGraphSpec(title="deliver b", content="c", task_id="b", assignee="dev-2", reviewer=("verifier_1",)),
        ]
    )
    await scheduler.activate()
    for task_id, author in (("a", "dev-1"), ("b", "dev-2")):
        assert (await _reviewer_mgr(db, bus, author).complete(task_id)).ok

    run_subjects = []
    outputs = ["181001 transient", "voted", "voted", "voted"]

    class _FakeHarness:
        async def run_once(self, _prompt, *, team_session=None):
            run_subjects.append(current_execution_subject())
            return {"output": outputs[len(run_subjects) - 1]}

        async def dispose(self):
            return None

    monkeypatch.setattr(TeamHarness, "build", staticmethod(lambda **_kwargs: _FakeHarness()))

    task_a = await tm.get("a")
    # Round N fails once transiently and is retried, then task b, then round N+1.
    await scheduler._spawn_temp_reviewer("verifier_1", task_a)
    await scheduler._spawn_temp_reviewer("verifier_1", await tm.get("b"))
    await scheduler._spawn_temp_reviewer(
        "verifier_1",
        task_a.model_copy(update={"review_round": task_a.review_round + 1}),
    )

    ids = [subject.subject_id for subject in run_subjects]
    round_a = task_a.review_round
    assert ids == [
        f"team-reviewer:sched_session:{TEAM}:a:verifier_1:r{round_a}",
        f"team-reviewer:sched_session:{TEAM}:a:verifier_1:r{round_a}:a1",
        f"team-reviewer:sched_session:{TEAM}:b:verifier_1:r{(await tm.get('b')).review_round}",
        f"team-reviewer:sched_session:{TEAM}:a:verifier_1:r{round_a + 1}",
    ]
    assert len(set(ids)) == len(ids)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_view_task_reads_the_board(db, bus, monkeypatch):
    """The temp reviewer's view_task must be bound to a task manager, not the backend."""
    from openjiuwen.agent_teams.agent.scheduling.scheduler import TeamScheduler as _Scheduler
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.tools.tool_task import ViewTaskToolV2

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    scheduler._blueprint.spec.agents = {"teammate": DeepAgentSpec()}
    scheduler._blueprint.spec.dispatch_mode = "scheduled"
    scheduler._blueprint.language = "cn"
    scheduler._infra.team_backend.db = db
    scheduler._infra.team_backend.workspace_cache = None
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",))
    monkeypatch.setattr(_Scheduler, "_dispatch_to_reviewer", AsyncMock())

    view_tools = []

    class _FakeHarness:
        async def run_once(self, _prompt, *, team_session=None):
            return {"output": "voted"}

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, **_kwargs):
        view_tools.extend(tool for tool in agent_spec.tools if isinstance(tool, ViewTaskToolV2))
        return _FakeHarness()

    monkeypatch.setattr(TeamHarness, "build", staticmethod(_fake_build))

    await scheduler._spawn_temp_reviewer("rev-1", await tm.get("r"))

    assert len(view_tools) == 1
    view_tool = view_tools[0]
    # The reviewer's copy marks view_task as a fallback: the request already has the task.
    assert "验收兜底" in view_tool.card.description
    detail = await view_tool.invoke({"action": "get", "task_id": "r"})
    assert detail.success
    assert detail.data["task_id"] == "r"
    listed = await view_tool.invoke({"action": "list"})
    assert listed.success
    assert [task["task_id"] for task in listed.data["tasks"]] == ["r"]
    in_review = await view_tool.invoke({"action": "in_review"})
    assert in_review.success
    assert [task["task_id"] for task in in_review.data["tasks"]] == ["r"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_prompt_carries_its_previous_round_feedback(db, bus):
    from openjiuwen.agent_teams.agent.scheduling import render

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1", "rev-2"))
    first_round = await tm.get("r")
    # The first round has no previous vote, so the section is left out.
    assert await tm.get_previous_round_vote(first_round, "rev-1") is None
    first_prompt = await render.render_review_request_for_harness(first_round, language="en", reviewer="rev-1")
    assert "previous round" not in first_prompt
    assert "{{task.previous_feedback}}" not in first_prompt

    rev_1 = _reviewer_mgr(db, bus, "rev-1")
    assert (await rev_1.verify_task("r", "fail", "too vague")).ok
    assert (await rev_1.verify_task("r", "fail", "missing the auth section")).ok
    assert (await _reviewer_mgr(db, bus, "rev-2").verify_task("r", "pass")).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert (await tm.get("r")).status == TaskStatus.IN_PROGRESS.value
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("r")).ok
    second_round = await tm.get("r")
    assert second_round.review_round == first_round.review_round + 1

    # The reviewer's latest vote of the previous round wins.
    previous = await tm.get_previous_round_vote(second_round, "rev-1")
    assert previous.decision == "fail"
    assert previous.feedback == "missing the auth section"
    prompt = await render.render_review_request_for_harness(
        second_round,
        language="en",
        reviewer="rev-1",
        previous_vote=previous,
    )
    assert f"previous round (round {first_round.review_round})" in prompt
    assert "missing the auth section" in prompt
    assert "too vague" not in prompt


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_prompt_carries_the_submission_note(db, bus):
    """The reviewer locates the deliverable from the request, not a view_task call."""
    from openjiuwen.agent_teams.agent.scheduling import render

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [TaskGraphSpec(title="deliver", content="c", task_id="r", assignee="dev-1", reviewer=("rev-1",))]
    )
    await scheduler.activate()
    note = "Deliverable written to /abs/outputs/poem.md; rhyme group: 十一尤"
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("r", note=note)).ok

    prompt = await render.render_review_request_for_harness(await tm.get("r"), language="en", reviewer="rev-1")

    assert "## The assignee's submission note" in prompt
    assert note in prompt
    assert "The above is the task's current, complete content." in prompt
    assert prompt.index("## Goal and acceptance criteria") < prompt.index(note) < prompt.index("## How to review")
    assert "{{task." not in prompt
    assert "\n\n\n" not in prompt


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_prompt_leaves_out_an_absent_submission_note(db, bus):
    from openjiuwen.agent_teams.agent.scheduling import render

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",))

    prompt = await render.render_review_request_for_harness(await tm.get("r"), language="cn", reviewer="rev-1")

    assert "提交说明" not in prompt
    assert "{{task." not in prompt
    assert "\n\n\n" not in prompt


async def _capture_reviewer_spec(scheduler, tm, monkeypatch, db) -> Any:
    """Spawn one temp reviewer and return the agent spec its harness was built with."""
    from openjiuwen.agent_teams.agent.scheduling.scheduler import TeamScheduler as _Scheduler
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness

    scheduler._blueprint.spec.dispatch_mode = "scheduled"
    scheduler._blueprint.language = "cn"
    scheduler._infra.team_backend.db = db
    scheduler._infra.team_backend.workspace_cache = None
    monkeypatch.setattr(_Scheduler, "_dispatch_to_reviewer", AsyncMock())
    built = []

    class _FakeHarness:
        async def run_once(self, _prompt, *, team_session=None):
            return {"output": "voted"}

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, **_kwargs):
        built.append(agent_spec)
        return _FakeHarness()

    monkeypatch.setattr(TeamHarness, "build", staticmethod(_fake_build))
    await scheduler._spawn_temp_reviewer("rev-1", await tm.get("r"))
    assert len(built) == 1
    return built[0]


def _tool_names(agent_spec) -> set[str]:
    return {getattr(tool, "type", None) or tool.card.name for tool in agent_spec.tools}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_prefers_the_host_reviewer_spec(db, bus, monkeypatch):
    from openjiuwen.agent_teams.schema.deep_agent_spec import BuiltinToolSpec, DeepAgentSpec

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    scheduler._blueprint.spec.agents = {"teammate": DeepAgentSpec(tools=[BuiltinToolSpec(type="host.cron")])}
    scheduler._blueprint.spec.reviewer_agent = DeepAgentSpec(tools=[BuiltinToolSpec(type="host.web_fetch")])
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",))

    agent_spec = await _capture_reviewer_spec(scheduler, tm, monkeypatch, db)

    assert _tool_names(agent_spec) == {"host.web_fetch", "verify_task", "view_task"}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_falls_back_to_a_trimmed_teammate_spec(db, bus, monkeypatch):
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, RailSpec, SubAgentSpec
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    scheduler._blueprint.spec.reviewer_agent = None
    scheduler._blueprint.spec.agents = {
        "teammate": DeepAgentSpec(
            rails=[RailSpec(type="core.sys_operation")],
            subagents=[SubAgentSpec(agent_card=AgentCard(name="helper"), system_prompt="")],
            enable_task_planning=True,
            add_general_purpose_agent=True,
        )
    }
    await _seed_review(db, bus, scheduler, tm, reviewers=("rev-1",))

    agent_spec = await _capture_reviewer_spec(scheduler, tm, monkeypatch, db)

    sys_rails = [rail for rail in agent_spec.rails if rail.type == "core.sys_operation"]
    assert sys_rails[0].params["read_only"] is True
    assert agent_spec.subagents is None
    assert agent_spec.add_general_purpose_agent is False
    assert agent_spec.enable_task_planning is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_temp_reviewer_prompt_lists_upstream_tasks(db, bus):
    from openjiuwen.agent_teams.agent.scheduling import render
    from openjiuwen.agent_teams.message_template import task_links

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="draft", content="c", task_id="up", assignee="dev-2"),
            TaskGraphSpec(
                title="collect",
                content="c",
                task_id="r",
                assignee="dev-1",
                reviewer=("rev-1",),
                depends_on=("up",),
            ),
        ]
    )
    await scheduler.activate()
    assert (await _reviewer_mgr(db, bus, "dev-2").complete("up", note="poem at /abs/up.md")).ok
    # The completed upstream unblocks "r"; the next scan starts it.
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("r")).ok
    upstream, _downstream = await task_links(db, "r")

    prompt = await render.render_review_request_for_harness(
        await tm.get("r"),
        language="en",
        reviewer="rev-1",
        upstream=upstream,
    )

    assert "## Upstream tasks" in prompt
    assert "- up · draft · dev-2 · poem at /abs/up.md" in prompt
    assert "{{task." not in prompt



# ---------------------------------------------------------------------------
# F_119: the leader digest carries the completion summary
# ---------------------------------------------------------------------------


async def _digest_for(db, bus, *, summary: str | None, note: str | None) -> str:
    scheduler, host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph([TaskGraphSpec(title="one", content="c", task_id="a", assignee="dev-1")])
    await scheduler.activate()
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("a", summary=summary, note=note)).ok
    await scheduler.on_event(
        EventMessage.from_event(TaskCompletedEvent(team_name=TEAM, task_id="a", member_name="dev-1"))
    )
    digests = [text for text in host.leader_inputs if "[a]" in text]
    assert len(digests) == 1
    return digests[0]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_digest_carries_summary_and_points_to_the_note(db, bus):
    digest = await _digest_for(db, bus, summary="report at out/r.md", note="full detail\nsecond line")

    assert "report at out/r.md" in digest
    assert "full detail" not in digest
    assert "view_task(action='note', task_id='a')" in digest


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_digest_without_note_or_cut_omits_the_note_hint(db, bus):
    digest = await _digest_for(db, bus, summary="short summary", note=None)

    assert "short summary" in digest
    assert "action='note'" not in digest


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_digest_without_completion_text_is_the_bare_line(db, bus):
    digest = await _digest_for(db, bus, summary=None, note=None)

    assert "action='note'" not in digest
    assert digest.count("\n") == 1  # headline + board hint only


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_digest_points_to_the_note_when_the_summary_is_cut(db, bus):
    digest = await _digest_for(db, bus, summary="z" * 300, note=None)

    assert "z" * 200 + "…" in digest
    assert "view_task(action='note', task_id='a')" in digest


@pytest.mark.asyncio
@pytest.mark.level1
async def test_review_request_shows_summary_and_full_note(db, bus):
    from openjiuwen.agent_teams.agent.scheduling import render

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [TaskGraphSpec(title="deliver", content="c", task_id="r", assignee="dev-1", reviewer=("rev-1",))]
    )
    await scheduler.activate()
    note = "first line\n" + "x" * 400
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("r", summary="poem at /abs/p.md", note=note)).ok

    prompt = await render.render_review_request_for_harness(await tm.get("r"), language="en", reviewer="rev-1")

    assert "Summary: poem at /abs/p.md" in prompt
    assert f"Detail:\n{note}" in prompt
    assert "{{task." not in prompt


@pytest.mark.asyncio
@pytest.mark.level1
async def test_review_request_upstream_lists_summaries_only(db, bus):
    from openjiuwen.agent_teams.agent.scheduling import render
    from openjiuwen.agent_teams.message_template import task_links

    scheduler, _host, _mm, tm = _build_scheduler(db, bus)
    await tm.add_graph(
        [
            TaskGraphSpec(title="draft", content="c", task_id="up", assignee="dev-2"),
            TaskGraphSpec(
                title="collect",
                content="c",
                task_id="r",
                assignee="dev-1",
                reviewer=("rev-1",),
                depends_on=("up",),
            ),
        ]
    )
    await scheduler.activate()
    assert (
        await _reviewer_mgr(db, bus, "dev-2").complete("up", summary="poem at /abs/up.md", note="long detail")
    ).ok
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))
    assert (await _reviewer_mgr(db, bus, "dev-1").complete("r")).ok
    upstream, _downstream = await task_links(db, "r")

    prompt = await render.render_review_request_for_harness(
        await tm.get("r"),
        language="en",
        reviewer="rev-1",
        upstream=upstream,
    )

    assert "- up · draft · dev-2 · poem at /abs/up.md" in prompt
    assert "long detail" not in prompt
    assert "view_task(action='note', task_id=...)" in prompt
