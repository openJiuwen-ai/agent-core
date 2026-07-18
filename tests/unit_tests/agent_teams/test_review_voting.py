# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for F_62 review voting: vote facts, tally, settle, rounds."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard

TEAM = "vote_team"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("vote_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM,
            display_name="Vote Team",
            leader_member_name="leader",
            dispatch_mode="scheduled",
        )
        members = {
            "author": MemberMode.BUILD_MODE,
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
                status="BUSY",
                mode=mode.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


def _mgr(db, bus, member_name, dispatch_mode="scheduled"):
    return TeamTaskManager(
        team_name=TEAM,
        member_name=member_name,
        db=db,
        messager=bus,
        dispatch_mode=dispatch_mode,
    )


def _published(bus, event_type: str) -> list:
    events = []
    for call in bus.publish.await_args_list:
        message = call.kwargs.get("message")
        if message is not None and message.event_type == event_type:
            events.append(message)
    return events


async def _seed_in_review(db, bus, task_id="t1", reviewer=("rev-1", "rev-2", "rev-3")):
    """Create an assigned reviewed task, start it, and submit it for review."""
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [TaskGraphSpec(title="work", content="c", task_id=task_id, assignee="author", reviewer=tuple(reviewer))]
    )
    assert graph.ok, graph.reason
    assert (await leader.start_task(task_id)).ok
    author = _mgr(db, bus, "author")
    assert (await author.complete(task_id)).ok
    task = await db.task.get_task(task_id)
    assert task.status == TaskStatus.IN_REVIEW.value
    return leader


# ---------------------------------------------------------------------------
# Round counter + vote facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_submit_for_review_bumps_round(db, bus):
    await _seed_in_review(db, bus)
    task = await db.task.get_task("t1")
    assert task.review_round == 1

    leader = _mgr(db, bus, "leader")
    assert (await leader.settle_review("t1", "fail", "redo")).ok
    author = _mgr(db, bus, "author")
    assert (await author.complete("t1")).ok
    task = await db.task.get_task("t1")
    assert task.status == TaskStatus.IN_REVIEW.value
    assert task.review_round == 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_verify_records_vote_without_transition(db, bus):
    await _seed_in_review(db, bus)
    reviewer = _mgr(db, bus, "rev-1")

    result = await reviewer.verify_task("t1", "pass")
    assert result.ok
    assert result.data is not None
    assert result.data["pass_count"] == 1
    assert result.data["fail_count"] == 0
    assert result.data["reviewer_count"] == 3

    # The task stays in the gate — no verdict transition happened.
    task = await db.task.get_task("t1")
    assert task.status == TaskStatus.IN_REVIEW.value
    assert _published(bus, TeamEvent.TASK_VERIFIED) == []
    votes = _published(bus, TeamEvent.TASK_REVIEW_VOTE)
    assert len(votes) == 1
    payload = votes[0].get_payload()
    assert payload.reviewer == "rev-1"
    assert payload.decision == "pass"
    assert payload.review_round == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_autonomous_verify_keeps_first_verdict_wins(db, bus):
    await _seed_in_review(db, bus, task_id="ta", reviewer=("rev-1", "rev-2"))
    reviewer = _mgr(db, bus, "rev-1", dispatch_mode="autonomous")

    assert (await reviewer.verify_task("ta", "pass")).ok
    assert (await db.task.get_task("ta")).status == TaskStatus.COMPLETED.value
    assert len(_published(bus, TeamEvent.TASK_VERIFIED)) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_revote_latest_wins_in_tally(db, bus):
    await _seed_in_review(db, bus)
    reviewer = _mgr(db, bus, "rev-1")
    assert (await reviewer.verify_task("t1", "fail", "nope")).ok
    assert (await reviewer.verify_task("t1", "pass")).ok

    task = await db.task.get_task("t1")
    tally = await reviewer.get_review_tally(task)
    assert tally["pass_count"] == 1
    assert tally["fail_count"] == 0
    assert tally["voted"] == ["rev-1"]
    assert tally["fail_feedback"] == {}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_round_isolation_voids_previous_round_votes(db, bus):
    await _seed_in_review(db, bus)
    rev1 = _mgr(db, bus, "rev-1")
    assert (await rev1.verify_task("t1", "fail", "redo it")).ok

    leader = _mgr(db, bus, "leader")
    assert (await leader.settle_review("t1", "fail", "redo it")).ok
    author = _mgr(db, bus, "author")
    assert (await author.complete("t1")).ok

    task = await db.task.get_task("t1")
    assert task.review_round == 2
    tally = await rev1.get_review_tally(task)
    assert tally["pass_count"] == 0
    assert tally["fail_count"] == 0
    assert tally["voted"] == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_tally_collects_fail_feedback(db, bus):
    await _seed_in_review(db, bus)
    assert (await _mgr(db, bus, "rev-1").verify_task("t1", "fail", "broken tests")).ok
    assert (await _mgr(db, bus, "rev-2").verify_task("t1", "fail", "missing docs")).ok

    task = await db.task.get_task("t1")
    tally = await _mgr(db, bus, "leader").get_review_tally(task)
    assert tally["fail_count"] == 2
    assert tally["fail_feedback"] == {"rev-1": "broken tests", "rev-2": "missing docs"}


# ---------------------------------------------------------------------------
# settle_review (framework verdict application)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_settle_pass_completes_and_unblocks(db, bus):
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [
            TaskGraphSpec(title="up", content="c", task_id="up", assignee="author", reviewer=("rev-1",)),
            TaskGraphSpec(title="down", content="c", task_id="down", assignee="rev-3", depends_on=("up",)),
        ]
    )
    assert graph.ok
    assert (await leader.start_task("up")).ok
    assert (await _mgr(db, bus, "author").complete("up")).ok

    assert (await leader.settle_review("up", "pass")).ok
    assert (await db.task.get_task("up")).status == TaskStatus.COMPLETED.value
    assert (await db.task.get_task("down")).status == TaskStatus.PENDING.value
    assert len(_published(bus, TeamEvent.TASK_VERIFIED)) == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_settle_fail_reverts_with_feedback(db, bus):
    await _seed_in_review(db, bus)
    leader = _mgr(db, bus, "leader")

    assert (await leader.settle_review("t1", "fail", "- rev-1: broken")).ok
    task = await db.task.get_task("t1")
    assert task.status == TaskStatus.IN_PROGRESS.value
    assert task.assignee == "author"
    revisions = _published(bus, TeamEvent.TASK_REVISION_REQUESTED)
    assert len(revisions) == 1
    assert revisions[0].get_payload().feedback == "- rev-1: broken"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_settle_guards(db, bus):
    await _seed_in_review(db, bus)
    leader = _mgr(db, bus, "leader")

    bad = await leader.settle_review("t1", "maybe")
    assert not bad.ok and "pass" in bad.reason

    missing = await leader.settle_review("ghost", "pass")
    assert not missing.ok and "not found" in missing.reason

    assert (await leader.settle_review("t1", "pass")).ok
    twice = await leader.settle_review("t1", "pass")
    assert not twice.ok and "not under review" in twice.reason


# ---------------------------------------------------------------------------
# Scheduled start: plan gate + round ceiling storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_task_plan_mode_lands_planning(db, bus):
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [TaskGraphSpec(title="plan work", content="c", task_id="p1", assignee="planner")]
    )
    assert graph.ok
    assert (await leader.start_task("p1")).ok
    task = await db.task.get_task("p1")
    assert task.status == TaskStatus.PLANNING.value
    assert task.assignee == "planner"
    # Idempotent re-start of the plan gate is a no-op success.
    assert (await leader.start_task("p1")).ok


@pytest.mark.asyncio
@pytest.mark.level1
async def test_max_review_rounds_persist_and_update(db, bus):
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [
            TaskGraphSpec(
                title="w",
                content="c",
                task_id="m1",
                assignee="author",
                reviewer=("rev-1",),
                max_review_rounds=2,
            )
        ]
    )
    assert graph.ok
    task = await db.task.get_task("m1")
    assert task.max_review_rounds == 2

    assert (await leader.set_max_review_rounds("m1", 5)).ok
    assert (await db.task.get_task("m1")).max_review_rounds == 5

    missing = await leader.set_max_review_rounds("ghost", 3)
    assert not missing.ok
