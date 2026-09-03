# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionSpawnExecutor coverage for Session-owned KVC cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules import TaskExecutorDependencies
from openjiuwen.core.controller.modules.event_queue import EventQueue
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.schema import EventType, Task, TaskStatus
from openjiuwen.core.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.task_loop.session_spawn_executor import SessionSpawnExecutor
from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE


def _make_deps(task_manager: TaskManager) -> TaskExecutorDependencies:
    config = ControllerConfig()
    return TaskExecutorDependencies(
        config=config,
        ability_manager=SimpleNamespace(),
        context_engine=ContextEngine(),
        task_manager=task_manager,
        event_queue=EventQueue(config),
    )


def _make_task(task_id: str = "task-1", sub_session_id: str | None = "parent_session_sub_meta") -> Task:
    metadata = {
        "subagent_type": "code",
        "task_description": "do work",
        "parent_session_id": "parent_session",
    }
    if sub_session_id is not None:
        metadata["sub_session_id"] = sub_session_id
    return Task(
        session_id="parent_session",
        task_id=task_id,
        task_type=SESSION_SPAWN_TASK_TYPE,
        description="do work",
        status=TaskStatus.SUBMITTED,
        metadata=metadata,
    )


def _subagent(*, error: Exception | None = None):
    invoke = AsyncMock(return_value={"output": "done"})
    if error is not None:
        invoke.side_effect = error
    return SimpleNamespace(
        card=AgentCard(id="child", name="child", description="child"),
        invoke=invoke,
    )


async def _make_executor(*, task: Task | None, subagent: object, enabled: bool = True) -> SessionSpawnExecutor:
    task_manager = TaskManager(config=ControllerConfig())
    if task is not None:
        await task_manager.add_task(task)
    deep_agent = SimpleNamespace(
        deep_config=SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled),
        ),
        create_subagent=MagicMock(return_value=subagent),
    )
    return SessionSpawnExecutor(_make_deps(task_manager), deep_agent)


async def _collect(executor: SessionSpawnExecutor, task_id: str):
    session = Session(session_id="parent_session")
    return [chunk async for chunk in executor.execute_ability(task_id, session)]


@pytest.mark.asyncio
async def test_success_passes_child_session_and_releases_it() -> None:
    subagent = _subagent()
    executor = await _make_executor(task=_make_task(), subagent=subagent)

    with patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        chunks = await _collect(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    child = subagent.invoke.await_args.kwargs["session"]
    assert child.get_session_id() == "parent_session_sub_meta"
    assert child.get_parent_session_id() == "parent_session"
    release.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_failure_releases_child_and_returns_task_failed() -> None:
    executor = await _make_executor(task=_make_task(), subagent=_subagent(error=RuntimeError("boom")))

    with patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        chunks = await _collect(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_FAILED
    release.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cancel_releases_resolved_child_session() -> None:
    executor = await _make_executor(task=_make_task(), subagent=_subagent())

    with patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        result = await executor.cancel("task-1", Session(session_id="parent_session"))

    assert result is True
    release.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_missing_task_cancel_is_noop() -> None:
    executor = await _make_executor(task=None, subagent=_subagent())

    with patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        result = await executor.cancel("missing", Session(session_id="parent_session"))

    assert result is True
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_sub_session_metadata_uses_stable_fallback() -> None:
    subagent = _subagent()
    executor = await _make_executor(
        task=_make_task(task_id="legacy-task", sub_session_id=None),
        subagent=subagent,
    )

    chunks = await _collect(executor, "legacy-task")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    assert subagent.invoke.await_args.args[0]["conversation_id"] == "parent_session_sub_legacy-task"
    assert subagent.invoke.await_args.kwargs["session"].get_session_id() == "parent_session_sub_legacy-task"


@pytest.mark.asyncio
async def test_affinity_disabled_preserves_baseline_invoke() -> None:
    subagent = _subagent()
    executor = await _make_executor(task=_make_task(), subagent=subagent, enabled=False)

    chunks = await _collect(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    assert "session" not in subagent.invoke.await_args.kwargs
    assert subagent.invoke.await_args.args[0] == {
        "query": "do work",
        "conversation_id": "parent_session_sub_meta",
    }
