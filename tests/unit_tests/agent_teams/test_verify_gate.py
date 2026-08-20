# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the F_59 verify gate (reviewer / IN_REVIEW / verify_task)."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard


@pytest_asyncio.fixture
async def db():
    token = set_session_id("session_id")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name="vt", display_name="VT", leader_member_name="leader")
        for name in ("author", "reviewer", "reviewer2"):
            await database.member.create_member(
                member_name=name,
                team_name="vt",
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="BUSY",
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


def _mgr(db, bus, member_name):
    return TeamTaskManager(team_name="vt", member_name=member_name, db=db, messager=bus)


def _published(bus, event_type: str) -> list:
    events = []
    for call in bus.publish.await_args_list:
        message = call.kwargs.get("message")
        if message is not None and message.event_type == event_type:
            events.append(message)
    return events


async def _seed_reviewed_task(db, bus, task_id="t1", reviewer=("reviewer",), assignee="author"):
    """Create a task assigned to ``assignee`` with reviewers, moved in progress."""
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [TaskGraphSpec(title="work", content="c", task_id=task_id, assignee=assignee, reviewer=tuple(reviewer))]
    )
    assert graph.ok, graph.reason
    # Owner starts executing (scheduled-style start; build member is in progress).
    assert (await leader.start_task(task_id)).ok
    assert (await db.task.get_task(task_id)).status == TaskStatus.IN_PROGRESS.value
    return leader


@pytest.mark.asyncio
@pytest.mark.level0
async def test_complete_with_reviewer_enters_in_review(db, bus):
    await _seed_reviewed_task(db, bus)
    author = _mgr(db, bus, "author")

    assert (await author.complete("t1")).ok

    task = await db.task.get_task("t1")
    assert task.status == TaskStatus.IN_REVIEW.value
    # The verify gate does not resolve the task yet, so no completion event.
    assert _published(bus, TeamEvent.TASK_COMPLETED) == []
    submitted = _published(bus, TeamEvent.TASK_SUBMITTED_FOR_REVIEW)
    assert len(submitted) == 1
    assert submitted[0].get_payload().reviewer == ["reviewer"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_no_reviewer_completes_directly(db, bus):
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="t1", assignee="author")]
    )
    assert graph.ok
    assert (await leader.start_task("t1")).ok

    author = _mgr(db, bus, "author")
    assert (await author.complete("t1")).ok

    assert (await db.task.get_task("t1")).status == TaskStatus.COMPLETED.value
    assert _published(bus, TeamEvent.TASK_SUBMITTED_FOR_REVIEW) == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_pass_completes_and_unblocks(db, bus):
    leader = _mgr(db, bus, "leader")
    graph = await leader.add_graph(
        [
            TaskGraphSpec(title="up", content="c", task_id="up", assignee="author", reviewer=("reviewer",)),
            TaskGraphSpec(title="down", content="c", task_id="down", depends_on=("up",)),
        ]
    )
    assert graph.ok
    assert (await db.task.get_task("down")).status == TaskStatus.BLOCKED.value
    assert (await leader.start_task("up")).ok
    await _mgr(db, bus, "author").complete("up")
    assert (await db.task.get_task("up")).status == TaskStatus.IN_REVIEW.value

    reviewer = _mgr(db, bus, "reviewer")
    result = await reviewer.verify_task("up", "pass")
    assert result.ok

    assert (await db.task.get_task("up")).status == TaskStatus.COMPLETED.value
    # Downstream unblocked by the pass-verified completion.
    assert (await db.task.get_task("down")).status == TaskStatus.PENDING.value
    verified = _published(bus, TeamEvent.TASK_VERIFIED)
    assert len(verified) == 1
    assert verified[0].get_payload().member_name == "author"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_fail_sends_back_for_rework(db, bus):
    await _seed_reviewed_task(db, bus)
    await _mgr(db, bus, "author").complete("t1")

    reviewer = _mgr(db, bus, "reviewer")
    result = await reviewer.verify_task("t1", "fail", feedback="tests missing")
    assert result.ok

    task = await db.task.get_task("t1")
    assert task.status == TaskStatus.IN_PROGRESS.value
    assert task.assignee == "author"  # author still holds it
    revision = _published(bus, TeamEvent.TASK_REVISION_REQUESTED)
    assert len(revision) == 1
    assert revision[0].get_payload().feedback == "tests missing"

    # The author reworks and resubmits; it re-enters the verify gate.
    assert (await _mgr(db, bus, "author").complete("t1")).ok
    assert (await db.task.get_task("t1")).status == TaskStatus.IN_REVIEW.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_rejects_non_reviewer(db, bus):
    await _seed_reviewed_task(db, bus, reviewer=("reviewer",))
    await _mgr(db, bus, "author").complete("t1")

    # reviewer2 is not on this task's reviewer list.
    result = await _mgr(db, bus, "reviewer2").verify_task("t1", "pass")
    assert not result.ok
    assert "not a reviewer" in result.reason
    assert (await db.task.get_task("t1")).status == TaskStatus.IN_REVIEW.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_author_cannot_self_verify(db, bus):
    # A task whose author is also (wrongly) in the reviewer list: the manager
    # still refuses self-verification.
    leader = _mgr(db, bus, "leader")
    await leader.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="t1", assignee="author", reviewer=("author",))]
    )
    await leader.start_task("t1")
    await _mgr(db, bus, "author").complete("t1")

    result = await _mgr(db, bus, "author").verify_task("t1", "pass")
    assert not result.ok
    assert "cannot verify their own task" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_rejects_task_not_in_review(db, bus):
    await _seed_reviewed_task(db, bus)  # still IN_PROGRESS, not submitted
    result = await _mgr(db, bus, "reviewer").verify_task("t1", "pass")
    assert not result.ok
    assert "not under review" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_rejects_bad_decision(db, bus):
    await _seed_reviewed_task(db, bus)
    await _mgr(db, bus, "author").complete("t1")
    result = await _mgr(db, bus, "reviewer").verify_task("t1", "maybe")
    assert not result.ok
    assert "must be 'pass' or 'fail'" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_in_review_counts_as_one_active_task(db, bus):
    await _seed_reviewed_task(db, bus)
    await _mgr(db, bus, "author").complete("t1")
    assert (await db.task.get_task("t1")).status == TaskStatus.IN_REVIEW.value

    # The author still owns the IN_REVIEW task, so it is not free to start a new one.
    busy = await _mgr(db, bus, "author").get_other_active_task_id("author", exclude_task_id="other")
    assert busy == "t1"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_get_review_tasks_filters_by_reviewer(db, bus):
    # Distinct authors so each owns exactly one active task (one-active invariant).
    await _seed_reviewed_task(db, bus, task_id="a", reviewer=("reviewer",), assignee="author")
    await _seed_reviewed_task(db, bus, task_id="b", reviewer=("author",), assignee="reviewer2")
    await _mgr(db, bus, "author").complete("a")
    await _mgr(db, bus, "reviewer2").complete("b")

    mine = await _mgr(db, bus, "reviewer").get_review_tasks("reviewer")
    assert {t.task_id for t in mine} == {"a"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_set_reviewer_persists_and_reads_back(db, bus):
    leader = _mgr(db, bus, "leader")
    await leader.add_graph([TaskGraphSpec(title="w", content="c", task_id="t1")])

    assert (await leader.set_reviewer("t1", ["reviewer", "reviewer2"])).ok
    detail = await leader.get_task_detail("t1")
    assert detail.reviewer == ["reviewer", "reviewer2"]

    # Clearing the gate.
    assert (await leader.set_reviewer("t1", [])).ok
    assert (await leader.get_task_detail("t1")).reviewer == []
