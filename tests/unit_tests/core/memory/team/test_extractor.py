# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team memory extractor."""

from __future__ import annotations

import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.team.extractor import extract_team_memories


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
    mock_db.get_team_messages = AsyncMock(return_value=[mock_msg])

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
    mock_db.get_team_messages = AsyncMock(return_value=[mock_msg])

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
