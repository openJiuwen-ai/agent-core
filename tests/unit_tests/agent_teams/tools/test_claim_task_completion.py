# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the autonomous claim_task completion text and upstream view (F_120)."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.tool_task import ClaimTaskTool, ViewTaskToolV2
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "claim_team"
MEMBER = "dev-1"
UPSTREAM_MEMBER = "dev-2"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("claim_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name=TEAM_NAME, display_name="Claim Team", leader_member_name="leader1")
        for name in (MEMBER, UPSTREAM_MEMBER):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _manager(db, member_name: str) -> TeamTaskManager:
    return TeamTaskManager(team_name=TEAM_NAME, member_name=member_name, db=db, messager=AsyncMock(spec=Messager))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_complete_writes_summary_and_note(db):
    manager = _manager(db, MEMBER)
    task = await manager.add(title="T", content="c")
    tool = ClaimTaskTool(manager, make_translator("en"))
    assert (await tool.invoke({"task_id": task.task_id, "status": "claimed"})).success

    result = await tool.invoke(
        {"task_id": task.task_id, "status": "completed", "summary": "report at out/r.md", "note": "detail"},
    )

    assert result.success
    row = await manager.get(task.task_id)
    assert row.status == TaskStatus.COMPLETED.value
    assert (row.summary, row.note) == ("report at out/r.md", "detail")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_claim_ignores_completion_fields_with_a_hint(db):
    manager = _manager(db, MEMBER)
    task = await manager.add(title="T", content="c")
    tool = ClaimTaskTool(manager, make_translator("en"))

    result = await tool.invoke({"task_id": task.task_id, "status": "claimed", "summary": "too early"})

    assert result.success
    assert (await manager.get(task.task_id)).summary is None
    assert "only take effect with status=completed" in tool.render_for_llm(result)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claim_lists_direct_upstream_summaries(db):
    leader = _manager(db, MEMBER)
    assert (
        await leader.add_graph(
            [
                TaskGraphSpec(title="root", content="c", task_id="root"),
                TaskGraphSpec(title="draft", content="c", task_id="up", depends_on=("root",)),
                TaskGraphSpec(title="collect", content="c", task_id="down", depends_on=("up",)),
            ]
        )
    ).ok
    upstream_dev = _manager(db, UPSTREAM_MEMBER)
    for task_id, summary in (("root", "root summary"), ("up", "draft at /abs/up.md")):
        assert await upstream_dev.claim(task_id)
        assert (await upstream_dev.complete(task_id, summary=summary, note="long detail")).ok
    tool = ClaimTaskTool(_manager(db, MEMBER), make_translator("en"))

    result = await tool.invoke({"task_id": "down", "status": "claimed"})
    text = tool.render_for_llm(result)

    assert result.success
    assert "Upstream deliveries (this task's direct dependencies):" in text
    assert f"- up · draft · {UPSTREAM_MEMBER} · draft at /abs/up.md" in text
    # Only the direct dependency, never the upstream's own upstream.
    assert "root summary" not in text
    assert "long detail" not in text
    assert "view_task(action='note', task_id=...)" in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_claim_without_upstream_adds_no_section(db):
    manager = _manager(db, MEMBER)
    task = await manager.add(title="T", content="c")
    tool = ClaimTaskTool(manager, make_translator("en"))

    text = tool.render_for_llm(await tool.invoke({"task_id": task.task_id, "status": "claimed"}))

    assert "Upstream deliveries" not in text


@pytest.mark.level1
def test_claim_schema_declares_summary_and_note(db):
    tool = ClaimTaskTool(_manager(db, MEMBER), make_translator("cn"))

    properties = tool.card.input_params["properties"]

    assert {"summary", "note"} <= set(properties)
    assert tool.card.input_params["required"] == ["task_id", "status"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_view_task_get_lists_upstream_deliveries(db):
    manager = _manager(db, MEMBER)
    assert (
        await manager.add_graph(
            [
                TaskGraphSpec(title="draft", content="c", task_id="up"),
                TaskGraphSpec(title="collect", content="c", task_id="down", depends_on=("up",)),
            ]
        )
    ).ok
    upstream_dev = _manager(db, UPSTREAM_MEMBER)
    assert await upstream_dev.claim("up")
    assert (await upstream_dev.complete("up", summary="draft at /abs/up.md")).ok
    view = ViewTaskToolV2(manager, make_translator("en"))

    text = view.render_for_llm(await view.invoke({"action": "get", "task_id": "down"}))

    assert "Upstream deliveries:" in text
    assert f"- up · draft · {UPSTREAM_MEMBER} · draft at /abs/up.md" in text
