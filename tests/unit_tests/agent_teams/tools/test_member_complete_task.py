# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MemberCompleteTaskTool (`team.member_complete_task`).

Self-only completion tool used by Human Agent (and any other member who
wants a "complete the task assigned to me" capability without inheriting
``claim_task``'s autonomous-claim semantics).
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import (
    MemberMode,
    TaskStatus,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team_tools import MemberCompleteTaskTool
from openjiuwen.agent_teams.tools.tool_task import ViewTaskToolV2
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "test_team"
HUMAN_NAME = "human_alice"
OTHER_NAME = "teammate_bob"


@pytest_asyncio.fixture
async def db():
    """Provide an initialized in-memory database."""
    token = set_session_id("session_id")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Test Team",
            leader_member_name="leader1",
        )
        await database.member.create_member(
            member_name=HUMAN_NAME,
            team_name=TEAM_NAME,
            display_name="Alice",
            agent_card=AgentCard().model_dump_json(),
            status="READY",
            mode=MemberMode.BUILD_MODE.value,
        )
        await database.member.create_member(
            member_name=OTHER_NAME,
            team_name=TEAM_NAME,
            display_name="Bob",
            agent_card=AgentCard().model_dump_json(),
            status="READY",
            mode=MemberMode.BUILD_MODE.value,
        )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def caller_task_manager(db):
    """Task manager bound to HUMAN_NAME (the caller)."""
    bus = AsyncMock(spec=Messager)
    return TeamTaskManager(
        team_name=TEAM_NAME,
        member_name=HUMAN_NAME,
        db=db,
        messager=bus,
    )


@pytest_asyncio.fixture
async def other_task_manager(db):
    """Task manager bound to OTHER_NAME, used to seed assignments."""
    bus = AsyncMock(spec=Messager)
    return TeamTaskManager(
        team_name=TEAM_NAME,
        member_name=OTHER_NAME,
        db=db,
        messager=bus,
    )


@pytest_asyncio.fixture
async def tool(caller_task_manager):
    """Wire the tool with the human caller's task manager."""
    return MemberCompleteTaskTool(caller_task_manager, make_translator("en"))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_complete_own_assigned_task(tool, caller_task_manager):
    """Caller completes a task that was previously assigned to them."""
    task = await caller_task_manager.add(title="Done by human", content="...")
    assign_result = await caller_task_manager.assign(task.task_id, HUMAN_NAME)
    assert assign_result.ok

    result = await tool.invoke({"task_id": task.task_id, "note": "done"})

    assert result.success, result.error
    assert result.data["task_id"] == task.task_id
    assert result.data["status"] == "completed"
    assert "note" not in result.data

    refreshed = await caller_task_manager.get(task.task_id)
    assert refreshed.status == TaskStatus.COMPLETED.value
    assert refreshed.note == "done"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reject_when_assignee_is_someone_else(tool, caller_task_manager, other_task_manager):
    """A member must not complete a task that is assigned to a peer."""
    task = await caller_task_manager.add(title="For Bob", content="...")
    await caller_task_manager.assign(task.task_id, OTHER_NAME)

    result = await tool.invoke({"task_id": task.task_id})

    assert not result.success
    assert OTHER_NAME in (result.error or "")
    assert HUMAN_NAME in (result.error or "")
    refreshed = await caller_task_manager.get(task.task_id)
    assert refreshed.status == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reject_when_task_unassigned(tool, caller_task_manager):
    """An unassigned (PENDING) task cannot be completed by anyone."""
    task = await caller_task_manager.add(title="Floating", content="...")

    result = await tool.invoke({"task_id": task.task_id})

    assert not result.success
    assert "<unassigned>" in (result.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_missing_task_id_rejected(tool):
    """task_id is required."""
    result = await tool.invoke({"task_id": ""})
    assert not result.success
    assert "task_id" in (result.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_unknown_task_id_returns_not_found(tool):
    """Non-existent task surfaces a clear not-found error."""
    result = await tool.invoke({"task_id": "does-not-exist"})
    assert not result.success
    assert "does-not-exist" in (result.error or "")
    assert "not found" in (result.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_render_for_llm_does_not_echo_the_note(tool, caller_task_manager):
    """The LLM-facing string reports the outcome; the note stays in the call arguments."""
    task = await caller_task_manager.add(title="With note", content="...")
    await caller_task_manager.assign(task.task_id, HUMAN_NAME)

    result = await tool.invoke({"task_id": task.task_id, "note": "patched config"})
    text = tool.render_for_llm(result)

    assert task.task_id in text
    assert "completed" in text.lower()
    assert "patched config" not in text


# ---------------------------------------------------------------------------
# F_119: completion summary, the scheduled handoff notice, view_task(note)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_complete_persists_summary_without_echoing_it(tool, caller_task_manager):
    task = await caller_task_manager.add(title="With summary", content="...")
    await caller_task_manager.assign(task.task_id, HUMAN_NAME)

    result = await tool.invoke({"task_id": task.task_id, "summary": "report at out/r.md", "note": "detail"})

    assert result.success, result.error
    assert set(result.data) == {"task_id", "status"}
    refreshed = await caller_task_manager.get(task.task_id)
    assert (refreshed.summary, refreshed.note) == ("report at out/r.md", "detail")


@pytest.mark.level1
def test_input_schema_declares_summary(tool):
    assert "summary" in tool.card.input_params["properties"]


@pytest.mark.level1
def test_scheduled_result_tells_the_member_not_to_report(db):
    manager = TeamTaskManager(
        team_name=TEAM_NAME,
        member_name=HUMAN_NAME,
        db=db,
        messager=AsyncMock(spec=Messager),
        dispatch_mode="scheduled",
    )
    scheduled_tool = MemberCompleteTaskTool(manager, make_translator("en"))
    output = ToolOutput(success=True, data={"task_id": "t-1", "status": "completed"})

    text = scheduled_tool.render_for_llm(output)

    assert text.startswith("Task #t-1 completed")
    assert "do not send a separate completion report" in text


@pytest.mark.level1
def test_autonomous_result_carries_no_handoff_notice(tool):
    output = ToolOutput(success=True, data={"task_id": "t-1", "status": "in_review"})

    text = tool.render_for_llm(output)

    assert "completion report" not in text


@pytest.mark.asyncio
@pytest.mark.level0
async def test_view_task_note_returns_full_summary_and_note(caller_task_manager):
    task = await caller_task_manager.add(title="Noted", content="the content")
    await caller_task_manager.assign(task.task_id, HUMAN_NAME)
    long_note = "line one\n" + "y" * 600
    assert (await caller_task_manager.complete(task.task_id, summary="s" * 300, note=long_note)).ok
    view = ViewTaskToolV2(caller_task_manager, make_translator("en"))

    result = await view.invoke({"action": "note", "task_id": task.task_id})
    text = view.render_for_llm(result)

    assert result.success
    assert "content" not in result.data
    assert f"Summary: {'s' * 300}" in text
    assert f"Completion note:\n{long_note}" in text
    assert "the content" not in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_view_task_note_without_text_and_without_task_id(caller_task_manager):
    task = await caller_task_manager.add(title="Bare", content="c")
    view = ViewTaskToolV2(caller_task_manager, make_translator("en"))

    bare = view.render_for_llm(await view.invoke({"action": "note", "task_id": task.task_id}))
    missing = await view.invoke({"action": "note"})
    unknown = await view.invoke({"action": "note", "task_id": "nope"})

    assert f"Task #{task.task_id} has no completion summary or note." in bare
    assert not missing.success and "task_id required" in missing.error
    assert not unknown.success


@pytest.mark.asyncio
@pytest.mark.level1
async def test_view_task_get_shows_summary_and_note(caller_task_manager):
    task = await caller_task_manager.add(title="Both", content="c")
    await caller_task_manager.assign(task.task_id, HUMAN_NAME)
    assert (await caller_task_manager.complete(task.task_id, summary="short", note="long")).ok
    view = ViewTaskToolV2(caller_task_manager, make_translator("en"))

    text = view.render_for_llm(await view.invoke({"action": "get", "task_id": task.task_id}))

    assert "Summary: short" in text
    assert "Note: long" in text
