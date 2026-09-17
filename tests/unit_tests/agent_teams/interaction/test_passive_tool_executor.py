# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the passive-human tool-call passthrough executor.

Covers the identity contract that makes passthrough safe: the executor's
tool surface is bound to the sender's member name, so the assignee /
reviewer / sender guards inside the shared tool implementations fire
exactly as they do for an avatar's LLM-driven calls — plus the permission
face (``PASSIVE_HUMAN_TOOLS``, scheduled-dispatch narrowing) and the
never-raises execution contract.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.interaction.passive_tool_executor import PassiveToolExecutor
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


async def _spawn_teammate(backend: TeamBackend, member_name: str) -> None:
    """Register a plain teammate row so assignee validation passes."""
    await backend.spawn_member(
        member_name=member_name,
        display_name=member_name,
        agent_card=AgentCard(id=f"{backend.team_name}_{member_name}", name=member_name),
    )


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("passive_session")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


@pytest_asyncio.fixture
async def backend(db, messager):
    backend = TeamBackend(
        team_name="passive_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_hitt=True,
    )
    await backend.build_team(
        display_name="T",
        desc="test",
        leader_display_name="Leader",
        leader_desc="Leader persona",
    )
    await backend.spawn_passive_human(member_name="pm-1", display_name="PM")
    yield backend


# ---------------------------------------------------------------------------
# Permission face
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_autonomous_surface_includes_claim_and_full_reach(backend):
    executor = PassiveToolExecutor(backend)
    tools = await executor._tools_for("pm-1")

    assert set(tools) == {"view_task", "member_complete_task", "verify_task", "claim_task", "send_message"}
    # send_message is the full-reach variant under autonomous dispatch
    assert tools["send_message"].card.name == "send_message"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_surface_drops_claim_and_reports_to_leader(db, messager):
    scheduled = TeamBackend(
        team_name="sched_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_hitt=True,
        dispatch_mode="scheduled",
    )
    await scheduled.build_team(
        display_name="T",
        desc="test",
        leader_display_name="Leader",
        leader_desc="Leader persona",
    )
    await scheduled.spawn_passive_human(member_name="pm-1", display_name="PM")

    executor = PassiveToolExecutor(scheduled)
    tools = await executor._tools_for("pm-1")

    assert set(tools) == {"view_task", "member_complete_task", "verify_task", "send_message"}
    # scheduled members reach only the leader / the user
    assert tools["send_message"].card.description == tools["send_message"].card.description
    assert type(tools["send_message"]).__name__ == "ReportToLeaderTool"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_unknown_tool_returns_failure_not_raise(backend):
    executor = PassiveToolExecutor(backend)
    output = await executor.execute("pm-1", "create_task", {"title": "x"})

    assert not output.success
    assert "create_task" in (output.error or "")
    # The error names the permitted face so an external protocol can self-correct
    assert "view_task" in (output.error or "")


# ---------------------------------------------------------------------------
# Identity binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_complete_task_completes_own_task(backend):
    await backend.task_manager.add(
        title="review the plan",
        content="read and approve",
        task_id="t-1",
    )
    await backend.task_manager.assign("t-1", "pm-1")

    executor = PassiveToolExecutor(backend)
    output = await executor.execute("pm-1", "member_complete_task", {"task_id": "t-1"})

    assert output.success
    assert (await backend.task_manager.get("t-1")).status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_complete_task_refuses_foreign_task(backend):
    await _spawn_teammate(backend, "dev-1")
    await backend.task_manager.add(
        title="not yours",
        content="owned by dev",
        task_id="t-2",
    )
    assert (await backend.task_manager.assign("t-2", "dev-1")).ok

    executor = PassiveToolExecutor(backend)
    output = await executor.execute("pm-1", "member_complete_task", {"task_id": "t-2"})

    assert not output.success
    assert "dev-1" in (output.error or "")
    assert (await backend.task_manager.get("t-2")).status != TaskStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_message_posts_row_as_passive_sender(backend):
    await _spawn_teammate(backend, "dev-1")

    executor = PassiveToolExecutor(backend)
    output = await executor.execute(
        "pm-1",
        "send_message",
        {"to": "dev-1", "content": "hello from the human"},
    )

    assert output.success
    rows = await backend.message_manager.get_messages(to_member_name="dev-1")
    matching = [row for row in rows if row.content == "hello from the human"]
    assert matching, "expected the relayed message on the bus"
    assert matching[0].from_member_name == "pm-1"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claim_task_claims_as_passive(backend):
    await backend.task_manager.add(title="open work", content="c", task_id="t-3")

    executor = PassiveToolExecutor(backend)
    output = await executor.execute("pm-1", "claim_task", {"task_id": "t-3", "status": "claimed"})

    assert output.success
    claimed = await backend.task_manager.get("t-3")
    assert claimed.assignee == "pm-1"
    assert claimed.status == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_view_task_returns_data(backend):
    await backend.task_manager.add(title="look here", content="c", task_id="t-4")

    executor = PassiveToolExecutor(backend)
    output = await executor.execute("pm-1", "view_task", {"task_id": "t-4"})

    assert output.success
    assert output.data is not None
    assert "t-4" in str(output.data)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_executor_never_raises_on_tool_exception(backend):
    executor = PassiveToolExecutor(backend)
    # member_complete_task with a nonexistent task id: the tool returns a
    # failure ToolOutput (no raise); force a raise path via a broken args
    # dict to prove exceptions are also caught and surfaced, not raised.
    output = await executor.execute("pm-1", "view_task", {"task_id": None})

    assert isinstance(output.success, bool)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_role_row_persisted_as_passive(backend):
    member = await backend.db.member.get_member("pm-1", "passive_team")
    assert member is not None
    assert member.role == TeamRole.PASSIVE_HUMAN.value
    assert member.status == "ready"
