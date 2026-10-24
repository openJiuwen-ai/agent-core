# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for hot-reload rail registration on the task-loop executor.

Regression: after a host-side ``configure()`` hot-reload queues new rails
into ``DeepAgent._pending_rails`` (e.g. a rebuilt PermissionInterruptRail
when the user switches full-access -> default), the scheduler-driven task
path must flush them via ``_ensure_initialized()`` before the inner
ReActAgent executes the round.  Previously ``execute_ability`` called
``react_agent.invoke()`` directly, so the rebuilt permission rail was
never registered and tools ran without any permission check.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.controller.modules.task_scheduler import (
    TaskExecutorDependencies,
)
from openjiuwen.harness.task_loop.task_loop_event_executor import (
    TaskLoopEventExecutor,
)


def _make_executor(deep_agent: MagicMock) -> TaskLoopEventExecutor:
    """Build a TaskLoopEventExecutor wired to the given DeepAgent mock."""
    task_manager = MagicMock()
    task_manager.get_task = AsyncMock(
        return_value=[
            SimpleNamespace(
                description="delete test.txt",
                metadata={
                    "is_follow_up": False,
                    "run_kind": None,
                    "run_context": None,
                },
                inputs=[],
            )
        ]
    )
    dependencies = TaskExecutorDependencies(
        config=MagicMock(),
        ability_manager=MagicMock(),
        context_engine=MagicMock(),
        task_manager=task_manager,
        event_queue=MagicMock(),
    )
    return TaskLoopEventExecutor(dependencies, deep_agent)


def _make_deep_agent() -> MagicMock:
    """Build a DeepAgent mock that returns a completed round."""
    deep_agent = MagicMock()
    deep_agent.react_agent.invoke = AsyncMock(
        return_value={
            "result_type": "task_completed",
            "output": "done",
        }
    )
    deep_agent.agent_callback_manager.execute = AsyncMock()
    deep_agent.load_state.return_value = SimpleNamespace(task_plan=None)
    deep_agent.loop_coordinator = None
    deep_agent.event_handler = None
    deep_agent._ensure_initialized = AsyncMock()
    return deep_agent


@pytest.mark.asyncio
async def test_execute_ability_flushes_pending_rails_before_react_invoke() -> None:
    """The scheduler path must run _ensure_initialized before react_agent.

    After a host-side hot-reload (``configure()``), ``_initialized`` is False
    and the rebuilt permission rail sits in ``_pending_rails``.  Only
    ``_ensure_initialized()`` registers it on the inner agent, so the
    executor must await it before delegating to ``react_agent.invoke``.
    """
    deep_agent = _make_deep_agent()
    executor = _make_executor(deep_agent)
    session = MagicMock()
    session.get_session_id.return_value = "web_test"

    chunks = [chunk async for chunk in executor.execute_ability("task-1", session)]

    assert len(chunks) == 1
    deep_agent._ensure_initialized.assert_awaited_once()
    deep_agent.react_agent.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_ability_initializes_before_react_invoke_order() -> None:
    """_ensure_initialized must complete before react_agent.invoke starts.

    Guarantees the pending permission rail is registered on the inner agent
    *before* any tool call of the round can be executed.
    """
    deep_agent = _make_deep_agent()
    executor = _make_executor(deep_agent)
    session = MagicMock()
    session.get_session_id.return_value = "web_test"

    call_log: list[str] = []

    async def _ensure_initialized() -> None:
        call_log.append("ensure_initialized")

    async def _react_invoke(*args, **kwargs) -> dict:
        call_log.append("react_invoke")
        return {
            "result_type": "task_completed",
            "output": "done",
        }

    deep_agent._ensure_initialized = AsyncMock(side_effect=_ensure_initialized)
    deep_agent.react_agent.invoke = AsyncMock(side_effect=_react_invoke)

    chunks = [chunk async for chunk in executor.execute_ability("task-1", session)]

    assert len(chunks) == 1
    assert call_log == ["ensure_initialized", "react_invoke"]