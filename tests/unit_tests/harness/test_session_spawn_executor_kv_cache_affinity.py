# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for SessionSpawnExecutor KV-cache affinity lifecycle."""

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
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.session.agent import Session
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


def _make_task(
        *,
        task_id: str = "task-1",
        sub_session_id: str | None = "parent_session_sub_meta",
) -> Task:
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


def _make_deep_agent(*, model: object, subagent: object, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        deep_config=SimpleNamespace(
            model=model,
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled),
        ),
        create_subagent=MagicMock(return_value=subagent),
    )


async def _make_executor(
        *,
        task: Task | None,
        subagent: object,
        enabled: bool = True,
) -> tuple[SessionSpawnExecutor, object]:
    task_manager = TaskManager(config=ControllerConfig())
    if task is not None:
        await task_manager.add_task(task)
    model = SimpleNamespace(name="model")
    executor = SessionSpawnExecutor(
        _make_deps(task_manager),
        _make_deep_agent(model=model, subagent=subagent, enabled=enabled),
    )
    return executor, model


async def _collect_execute(executor: SessionSpawnExecutor, task_id: str):
    return [chunk async for chunk in executor.execute_ability(task_id, Session(session_id="parent_session"))]


@pytest.mark.asyncio
async def test_success_evicts_subagent_session_and_returns_task_completion() -> None:
    subagent = SimpleNamespace(invoke=AsyncMock(return_value={"output": "done"}))
    executor, model = await _make_executor(task=_make_task(), subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        chunks = await _collect_execute(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    subagent.invoke.assert_awaited_once()
    assert subagent.invoke.await_args.args[0]["conversation_id"] == "parent_session_sub_meta"
    evict_mock.assert_awaited_once_with(
        model,
        session_id="parent_session_sub_meta",
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_failure_evicts_subagent_session_and_returns_task_failed() -> None:
    subagent = SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError("boom")))
    executor, model = await _make_executor(task=_make_task(), subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        chunks = await _collect_execute(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_FAILED
    evict_mock.assert_awaited_once_with(
        model,
        session_id="parent_session_sub_meta",
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_cancel_evicts_subagent_session_and_returns_true() -> None:
    executor, model = await _make_executor(task=_make_task(), subagent=SimpleNamespace())

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        result = await executor.cancel("task-1", Session(session_id="parent_session"))

    assert result is True
    evict_mock.assert_awaited_once_with(
        model,
        session_id="parent_session_sub_meta",
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_missing_task_cancel_does_not_evict_and_returns_true() -> None:
    executor, _ = await _make_executor(task=None, subagent=SimpleNamespace())

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        result = await executor.cancel("missing-task", Session(session_id="parent_session"))

    assert result is True
    evict_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_metadata_sub_session_uses_same_fallback_for_invoke_and_evict() -> None:
    subagent = SimpleNamespace(invoke=AsyncMock(return_value={"output": "done"}))
    executor, model = await _make_executor(
        task=_make_task(task_id="legacy-task", sub_session_id=None),
        subagent=subagent,
    )

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        chunks = await _collect_execute(executor, "legacy-task")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    fallback_id = "parent_session_sub_legacy-task"
    assert subagent.invoke.await_args.args[0]["conversation_id"] == fallback_id
    evict_mock.assert_awaited_once_with(
        model,
        session_id=fallback_id,
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_cancel_uses_fallback_sub_session_when_metadata_is_missing() -> None:
    executor, model = await _make_executor(
        task=_make_task(task_id="legacy-task", sub_session_id=None),
        subagent=SimpleNamespace(),
    )

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        result = await executor.cancel("legacy-task", Session(session_id="parent_session"))

    assert result is True
    evict_mock.assert_awaited_once_with(
        model,
        session_id="parent_session_sub_legacy-task",
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_kvc_helper_failure_preserves_task_completion_result() -> None:
    subagent = SimpleNamespace(invoke=AsyncMock(return_value={"output": "done"}))
    executor, _ = await _make_executor(task=_make_task(), subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(side_effect=RuntimeError("evict failed")),
    ):
        chunks = await _collect_execute(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION


@pytest.mark.asyncio
async def test_affinity_disabled_preserves_baseline_invoke_and_skips_evict() -> None:
    subagent = SimpleNamespace(invoke=AsyncMock(return_value={"output": "done"}))
    executor, _ = await _make_executor(task=_make_task(), subagent=subagent, enabled=False)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=False),
    ) as evict_mock:
        chunks = await _collect_execute(executor, "task-1")

    assert chunks[-1].payload.type == EventType.TASK_COMPLETION
    assert subagent.invoke.await_args.args[0] == {
        "query": "do work",
        "conversation_id": "parent_session_sub_meta",
    }
    evict_mock.assert_not_awaited()
