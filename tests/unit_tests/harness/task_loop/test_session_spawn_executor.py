# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for async browser capability delegation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.task_loop.session_spawn_executor import SessionSpawnExecutor


@pytest.mark.asyncio
async def test_executor_forwards_browser_capabilities_to_subagent_creation() -> None:
    task_manager = MagicMock()
    task_manager.get_task = AsyncMock(
        return_value=[
            SimpleNamespace(
                metadata={
                    "subagent_type": "browser_agent",
                    "task_description": "save the page",
                    "sub_session_id": "browser-session",
                    "browser_capabilities": ["pdf"],
                }
            )
        ]
    )
    subagent = MagicMock()
    subagent.invoke = AsyncMock(return_value={"output": "done"})
    deep_agent = MagicMock()
    deep_agent.create_subagent.return_value = subagent
    dependencies = SimpleNamespace(
        config=MagicMock(),
        ability_manager=MagicMock(),
        context_engine=MagicMock(),
        task_manager=task_manager,
        event_queue=MagicMock(),
    )
    executor = SessionSpawnExecutor(dependencies, deep_agent)

    chunks = [chunk async for chunk in executor.execute_ability("task-1", MagicMock())]

    assert len(chunks) == 1
    deep_agent.create_subagent.assert_called_once_with(
        "browser_agent",
        "browser-session",
        browser_capabilities=["pdf"],
    )
