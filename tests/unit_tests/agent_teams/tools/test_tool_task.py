# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the auto-start funnel on the task-creation path.

Under autonomous dispatch a task landing on the board is work being handed
to members, so it has to bring unstarted members up the same way an outgoing
message does — otherwise a leader that creates tasks and never broadcasts
leaves the whole roster parked at UNSTARTED with nothing subscribed to the
board. Scheduled dispatch is deliberately excluded: there the scheduler owns
every hand-off, and starting members here would double up on it.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode, MemberStatus
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_task import ScheduledTaskCreateTool, TaskCreateTool
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "task_team"
LEADER_NAME = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"


@pytest_asyncio.fixture
async def db():
    """In-memory team DB whose two teammates are registered but UNSTARTED."""
    token = set_session_id("task_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Task Team",
            leader_member_name=LEADER_NAME,
        )
        await database.member.create_member(
            member_name=LEADER_NAME,
            team_name=TEAM_NAME,
            display_name=LEADER_NAME,
            agent_card=AgentCard().model_dump_json(),
            status=MemberStatus.READY.value,
            mode=MemberMode.BUILD_MODE.value,
        )
        for name in (DEV_1, DEV_2):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status=MemberStatus.UNSTARTED.value,
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _leader_backend(db, on_member_started, dispatch_mode: str = "autonomous") -> TeamBackend:
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        dispatch_mode=dispatch_mode,
        on_member_started=on_member_started,
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_starts_unstarted_members(db):
    """An unassigned task starts everyone — anyone may claim from the pool."""
    on_created = AsyncMock()
    backend = _leader_backend(db, on_created)
    tool = TaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1"}]})

    assert result.success is True
    assert sorted(call[0][0] for call in on_created.await_args_list) == [DEV_1, DEV_2]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_starts_members_for_assigned_task(db):
    """A pre-assigned task starts the roster too, assignee included."""
    on_created = AsyncMock()
    backend = _leader_backend(db, on_created)
    tool = TaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is True
    assert DEV_1 in [call[0][0] for call in on_created.await_args_list]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_does_not_start_members_on_failure(db):
    """Nothing landed on the board, so there is nothing to be started for."""
    on_created = AsyncMock()
    backend = _leader_backend(db, on_created)
    tool = TaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": []})

    assert result.success is False
    on_created.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_survives_a_spawn_failure(db):
    """The tasks are committed; a spawn failure must not fail the tool call.

    Reporting failure here would invite the model to create the same tasks a
    second time. The round-idle reconcile picks the members up instead.
    """
    on_created = AsyncMock(side_effect=RuntimeError("spawn exploded"))
    backend = _leader_backend(db, on_created)
    tool = TaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1"}]})

    assert result.success is True
    assert result.data["title"] == "T1"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_create_task_starts_nobody(db):
    """Scheduled dispatch hands off through the scheduler, not through here."""
    on_created = AsyncMock()
    backend = _leader_backend(db, on_created, dispatch_mode="scheduled")
    tool = ScheduledTaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is True
    on_created.assert_not_awaited()


async def _set_status(db: TeamDatabase, member_name: str, *statuses: MemberStatus) -> None:
    for status in statuses:
        assert await db.member.update_member_status(member_name, TEAM_NAME, status.value)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_rejects_shut_down_assignee(db):
    """A SHUTDOWN member can never be started again: the task would sit on the board unworked."""
    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    await _set_status(db, DEV_1, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN)
    tool = ScheduledTaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is False
    assert DEV_1 in result.error and "shut down" in result.error
    assert await backend.task_manager.open_task_counts_by_assignee() == {}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_create_task_accepts_dormant_assignee(db):
    """A DORMANT member stays assignable: the scheduler's start handoff wakes it."""
    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    await _set_status(db, DEV_1, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.DORMANT)
    tool = ScheduledTaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_update_task_rejects_reassigning_to_shut_down_member(db):
    """Reassignment goes through the same assignee check as creation."""
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec
    from openjiuwen.agent_teams.tools.tool_task import UpdateTaskTool

    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    graph = await backend.task_manager.add_graph([TaskGraphSpec(title="T1", content="C1", task_id="t1", assignee=DEV_1)])
    assert graph.ok
    await _set_status(db, DEV_2, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN)
    tool = UpdateTaskTool(backend, make_translator("cn"), dispatch_mode="scheduled")

    result = await tool.invoke({"task_id": "t1", "assignee": DEV_2})

    assert result.success is False
    assert DEV_2 in result.error
    assert (await backend.task_manager.get("t1")).assignee == DEV_1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_rejects_member_settling_into_shutdown(db):
    """A member still shutting down may end in SHUTDOWN: refuse it until it has settled."""
    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    await _set_status(db, DEV_1, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED)
    tool = ScheduledTaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is False
    assert DEV_1 in result.error and "shutting down" in result.error


@pytest.mark.asyncio
@pytest.mark.level1
async def test_create_task_rejects_dormant_lifetime_member_force_shut_down(db):
    """A force shutdown ends in SHUTDOWN even for a dormant lifetime, so the settling window is refused too."""
    from sqlalchemy import update

    from openjiuwen.agent_teams.tools.models import TeamMember

    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    async with db.session_local() as session:
        await session.execute(
            update(TeamMember)
            .where(TeamMember.team_name == TEAM_NAME, TeamMember.member_name == DEV_1)
            .values(options='{"lifetime": "dormant"}')
        )
        await session.commit()
    await _set_status(db, DEV_1, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED)
    tool = ScheduledTaskCreateTool(backend, make_translator("cn"))

    result = await tool.invoke({"tasks": [{"title": "T1", "content": "C1", "assignee": DEV_1}]})

    assert result.success is False
    assert await backend.task_manager.open_task_counts_by_assignee() == {}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_update_task_rejected_reassignment_leaves_the_edit_unapplied(db):
    """A combined edit + reassignment to a retired member changes nothing."""
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec
    from openjiuwen.agent_teams.tools.tool_task import UpdateTaskTool

    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    graph = await backend.task_manager.add_graph([TaskGraphSpec(title="T1", content="C1", task_id="t1", assignee=DEV_1)])
    assert graph.ok
    await _set_status(db, DEV_2, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN)
    tool = UpdateTaskTool(backend, make_translator("cn"), dispatch_mode="scheduled")

    result = await tool.invoke({"task_id": "t1", "title": "T1 edited", "content": "C1 edited", "assignee": DEV_2})

    assert result.success is False
    task = await backend.task_manager.get("t1")
    assert (task.title, task.content, task.assignee) == ("T1", "C1", DEV_1)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_update_task_can_cancel_a_task_whose_owner_shut_down(db):
    """Cancelling hands nothing to anyone, so naming the retired owner does not block it."""
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec
    from openjiuwen.agent_teams.tools.tool_task import UpdateTaskTool

    backend = _leader_backend(db, AsyncMock(), dispatch_mode="scheduled")
    graph = await backend.task_manager.add_graph([TaskGraphSpec(title="T1", content="C1", task_id="t1", assignee=DEV_1)])
    assert graph.ok
    await _set_status(db, DEV_1, MemberStatus.STARTING, MemberStatus.READY, MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN)
    tool = UpdateTaskTool(backend, make_translator("cn"), dispatch_mode="scheduled")

    result = await tool.invoke({"task_id": "t1", "status": "cancelled", "assignee": DEV_1})

    assert result.success is True
    assert (await backend.task_manager.get("t1")).status == "cancelled"
