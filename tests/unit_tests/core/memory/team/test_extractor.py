# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team memory extractor."""

from __future__ import annotations

import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_teams.memory.extractor import (
    extract_team_memories,
    run_team_memory_extraction,
    snapshot_team_extraction_context,
    _create_extraction_tools,
)


@pytest.fixture
def team_memory_dir():
    path = tempfile.mkdtemp(prefix="extract_team_mem_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_extract_team_memories_skips_when_model_none(team_memory_dir):
    mock_db = MagicMock()
    mock_tm = MagicMock()
    mock_tm.list_tasks = AsyncMock(return_value=[SimpleNamespace(title="t", status="open", assignee="", content="")])

    mock_msg = SimpleNamespace(
        timestamp=1.0,
        from_member_name="a",
        broadcast=True,
        to_member_name=None,
        content="hi",
    )
    mock_db.message.get_team_messages = AsyncMock(return_value=[mock_msg])

    with patch("openjiuwen.harness.factory.create_deep_agent") as mock_create:
        with patch("openjiuwen.core.runner.runner.Runner.run_agent", new_callable=AsyncMock):
            await extract_team_memories(
                team_name="team1",
                db=mock_db,
                task_manager=mock_tm,
                team_memory_dir=team_memory_dir,
                sys_operation=MagicMock(),
                model=None,
            )
            mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_extract_team_memories_does_not_propagate_task_manager_errors(team_memory_dir):
    mock_db = MagicMock()
    mock_tm = MagicMock()
    mock_tm.list_tasks = AsyncMock(side_effect=RuntimeError("boom"))

    mock_model = MagicMock()

    await extract_team_memories(
        team_name="team1",
        db=mock_db,
        task_manager=mock_tm,
        team_memory_dir=team_memory_dir,
        sys_operation=MagicMock(),
        model=mock_model,
    )


@pytest.mark.asyncio
async def test_extract_team_memories_success_path_calls_agent_and_runner(team_memory_dir):
    task = SimpleNamespace(title="task", status="done", assignee="u", content="body")
    mock_tm = MagicMock()
    mock_tm.list_tasks = AsyncMock(return_value=[task])

    mock_msg = SimpleNamespace(
        timestamp=100.0,
        from_member_name="alice",
        broadcast=False,
        to_member_name="bob",
        content="please review",
    )
    mock_db = MagicMock()
    mock_db.message.get_team_messages = AsyncMock(return_value=[mock_msg])

    mock_agent = MagicMock()
    mock_model = MagicMock()

    with patch("openjiuwen.harness.factory.create_deep_agent", return_value=mock_agent) as mock_create:
        with patch(
            "openjiuwen.core.runner.runner.Runner.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            await extract_team_memories(
                team_name="team1",
                db=mock_db,
                task_manager=mock_tm,
                team_memory_dir=team_memory_dir,
                sys_operation=MagicMock(),
                model=mock_model,
                tz_offset_hours=8.0,
            )

            mock_create.assert_called_once()
            mock_run.assert_awaited_once()
            call_kw = mock_create.call_args.kwargs
            assert call_kw["system_prompt"]
            assert call_kw["tools"]
            assert call_kw["enable_task_loop"] is False


@pytest.mark.asyncio
async def test_snapshot_returns_none_when_empty(team_memory_dir):
    mock_db = MagicMock()
    mock_db.message.get_team_messages = AsyncMock(return_value=[])
    mock_tm = MagicMock()
    mock_tm.list_tasks = AsyncMock(return_value=[])

    context = await snapshot_team_extraction_context(
        team_name="team1",
        db=mock_db,
        task_manager=mock_tm,
    )
    assert context is None


@pytest.mark.asyncio
async def test_run_team_memory_extraction_uses_local_fs_tools(team_memory_dir):
    mock_agent = MagicMock()
    mock_model = MagicMock()
    memory_path = os.path.join(team_memory_dir, "TEAM_MEMORY.md")

    with patch("openjiuwen.harness.factory.create_deep_agent", return_value=mock_agent) as mock_create:
        with patch(
            "openjiuwen.core.runner.runner.Runner.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            await run_team_memory_extraction(
                team_name="team1",
                team_memory_dir=team_memory_dir,
                context="# snap",
                model=mock_model,
            )
            mock_create.assert_called_once()
            mock_run.assert_awaited_once()
            tools = mock_create.call_args.kwargs["tools"]
            write_tool = next(t for t in tools if t.card.name == "write_memory_file")
            result = await write_tool._func(path="TEAM_MEMORY.md", content="### [lesson] ok\n")
            assert result["success"] is True
            assert os.path.exists(memory_path)
            with open(memory_path, encoding="utf-8") as f:
                assert "lesson" in f.read()


@pytest.mark.asyncio
async def test_extraction_tools_reject_path_traversal(team_memory_dir):
    tools = _create_extraction_tools(team_memory_dir, team_name="t")
    write_tool = next(t for t in tools if t.card.name == "write_memory_file")
    result = await write_tool._func(path="../escape.md", content="x")
    assert result == {"error": "Invalid path"}
