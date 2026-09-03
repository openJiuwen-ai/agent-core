# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime SubagentSessionManager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError, ExecutionError, build_error
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.execution_subject import ExecutionSubject, execution_subject_scope
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind, UserInputOp
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from tests.unit_tests.harness.subagent_runtime.test_instance import MockAgent


@dataclass
class MockSession:
    pre_run_error: BaseException | None = None
    pre_run_calls: int = 0
    close_stream_calls: int = 0
    commit_calls: int = 0

    async def pre_run(self, **kwargs) -> None:
        self.pre_run_calls += 1
        if self.pre_run_error is not None:
            raise self.pre_run_error

    async def close_stream(self) -> None:
        self.close_stream_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1


@dataclass
class MockSubAgent:
    card: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(id="sub-card"))


@dataclass
class MockParentAgent:
    subagent: MockSubAgent | None = None
    create_error: BaseException | None = None
    create_calls: list[tuple[str, str, list[str] | None]] = field(default_factory=list)

    def create_subagent(
        self,
        subagent_type: str,
        subsession_id: str,
        browser_capabilities: list[str] | None = None,
    ) -> MockSubAgent:
        self.create_calls.append((subagent_type, subsession_id, browser_capabilities))
        if self.create_error is not None:
            raise self.create_error
        return self.subagent or MockSubAgent()


def _manager(
    *,
    parent: MockParentAgent | None = None,
    config: SubagentRuntimeConfig | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> SubagentSessionManager:
    return SubagentSessionManager(
        parent or MockParentAgent(),
        config or SubagentRuntimeConfig(),
        semaphore or asyncio.Semaphore(5),
    )


def _patch_create_session(*sessions: MockSession):
    created = list(sessions)

    def _factory(**kwargs) -> MockSession:
        if created:
            return created.pop(0)
        return MockSession()

    return patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        side_effect=_factory,
    )


@pytest.mark.asyncio
async def test_create_main_path() -> None:
    manager = _manager()

    with _patch_create_session():
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )

    assert instance.subagent_id == "parent_sub_explore"
    assert instance.display_name == "Explorer"
    assert instance.role == "researcher"
    assert instance.agent_status().kind is SubagentStatusKind.PENDING_INIT
    assert manager.find("parent_sub_explore") is instance


@pytest.mark.asyncio
async def test_create_captures_nested_parent_execution_subject() -> None:
    manager = _manager()
    parent_subject = ExecutionSubject(
        subject_id="subagent:parent",
        display_name="Parent",
        kind="subagent",
        parent_subject_id="main",
        session_id="parent-subsession",
    )

    with execution_subject_scope(parent_subject), _patch_create_session():
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_nested",
            parent_session_id="parent",
            display_name="Nested",
            role="researcher",
        )

    assert instance.execution_subject.parent_subject_id == "subagent:parent"
    assert instance.execution_subject.subject_id == "subagent:parent_sub_nested"


@pytest.mark.asyncio
async def test_create_subagent_failure_leaves_table_empty() -> None:
    parent = MockParentAgent(
        create_error=build_error(
            StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
            error_msg="missing",
        ),
    )
    manager = _manager(parent=parent)

    with pytest.raises(AgentError):
        await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )

    assert manager.list_ids() == []


@pytest.mark.asyncio
async def test_pre_run_failure_surfaces_as_errored_on_first_turn() -> None:
    parent = MockParentAgent(subagent=MockSubAgent())
    manager = _manager(parent=parent)

    with _patch_create_session(MockSession(pre_run_error=RuntimeError("pre_run failed"))):
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )
        instance._agent = MockAgent()
        await instance.enqueue(UserInputOp(query="hello", task_id="t1"))
        await asyncio.sleep(0.05)

    assert manager.list_ids() == ["parent_sub_explore"]
    assert instance.agent_status().kind is SubagentStatusKind.ERRORED
    assert instance.agent_status().message == "pre_run failed"


@pytest.mark.asyncio
async def test_find_and_get() -> None:
    manager = _manager()

    with _patch_create_session():
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )

    assert manager.find("parent_sub_explore") is instance
    assert manager.find("missing") is None
    assert manager.get("parent_sub_explore") is instance

    with pytest.raises(AgentError):
        manager.get("missing")


@pytest.mark.asyncio
async def test_remove_closes_instance_and_pops_table() -> None:
    manager = _manager()

    with _patch_create_session():
        await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )

    removed = await manager.remove("parent_sub_explore", reason="manual")

    assert removed is not None
    assert removed.is_closed()
    assert manager.list_ids() == []


@pytest.mark.asyncio
async def test_remove_missing_id_is_noop() -> None:
    manager = _manager()

    assert await manager.remove("missing") is None


@pytest.mark.asyncio
async def test_list_ids_reflects_created_instances() -> None:
    manager = _manager()

    with _patch_create_session():
        await manager.create(
            subagent_type="explore",
            subagent_id="sid-1",
            parent_session_id="parent",
            display_name="One",
            role="a",
        )
        await manager.create(
            subagent_type="explore",
            subagent_id="sid-2",
            parent_session_id="parent",
            display_name="Two",
            role="b",
        )

    assert set(manager.list_ids()) == {"sid-1", "sid-2"}


@pytest.mark.asyncio
async def test_restore_returns_live_instance_without_recreating() -> None:
    manager = _manager()

    with _patch_create_session():
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="sid-1",
            parent_session_id="parent",
            display_name="One",
            role="a",
        )

        restored = await manager.restore(
            subagent_type="explore",
            subagent_id="sid-1",
            parent_session_id="parent",
            display_name="One",
            role="a",
        )

    assert restored is instance


@pytest.mark.asyncio
async def test_restore_requires_checkpointer_history() -> None:
    manager = _manager()

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.CheckpointerFactory.get_checkpointer",
    ) as get_checkpointer:
        checkpointer = AsyncMock()
        checkpointer.session_exists = AsyncMock(return_value=False)
        get_checkpointer.return_value = checkpointer

        with pytest.raises(AgentError):
            await manager.restore(
                subagent_type="explore",
                subagent_id="sid-1",
                parent_session_id="parent",
                display_name="One",
                role="a",
            )


@pytest.mark.asyncio
async def test_restore_rebuilds_from_checkpointer() -> None:
    manager = _manager()

    with _patch_create_session(), patch(
        "openjiuwen.harness.subagent_runtime.session_manager.CheckpointerFactory.get_checkpointer",
    ) as get_checkpointer:
        checkpointer = AsyncMock()
        checkpointer.session_exists = AsyncMock(return_value=True)
        get_checkpointer.return_value = checkpointer

        restored = await manager.restore(
            subagent_type="explore",
            subagent_id="sid-1",
            parent_session_id="parent",
            display_name="One",
            role="a",
        )

    assert restored.subagent_id == "sid-1"
    assert manager.find("sid-1") is restored


@pytest.mark.asyncio
async def test_session_factory_creates_new_session_per_turn() -> None:
    manager = _manager()
    created_sessions: list[MockSession] = []

    def _factory(**kwargs) -> MockSession:
        session = MockSession()
        created_sessions.append(session)
        return session

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        side_effect=_factory,
    ):
        instance = await manager.create(
            subagent_type="explore",
            subagent_id="parent_sub_explore",
            parent_session_id="parent",
            display_name="Explorer",
            role="researcher",
        )
        instance._agent = MockAgent()
        await instance.enqueue(UserInputOp(query="first", task_id="t1"))
        await instance.enqueue(UserInputOp(query="second", task_id="t2"))
        await asyncio.sleep(0.05)

    assert len(created_sessions) == 2
    assert created_sessions[0] is not created_sessions[1]
    assert created_sessions[0].pre_run_calls == 1
    assert created_sessions[1].pre_run_calls == 1


@pytest.mark.asyncio
async def test_kv_cache_lifecycle_called_when_affinity_enabled() -> None:
    from openjiuwen.core.kv_cache import KVCacheAffinityConfig

    parent = MockParentAgent()
    parent.deep_config = SimpleNamespace(
        kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True),
    )
    manager = _manager(parent=parent)

    with _patch_create_session(), patch(
        "openjiuwen.harness.subagent_runtime.session_manager.kv_cache_subagent_lifecycle.prepare_subagent",
        new=AsyncMock(),
    ) as prepare_mock, patch(
        "openjiuwen.harness.subagent_runtime.session_manager.kv_cache_subagent_lifecycle.finish_subagent",
        new=AsyncMock(),
    ) as finish_mock:
        instance = await manager.create(
            subagent_type="browser_agent",
            subagent_id="parent_sub_browser_agent",
            parent_session_id="parent",
            display_name="Browser",
            role="automation",
        )
        instance._agent = MockAgent()
        await instance.enqueue(UserInputOp(query="hello", task_id="t1"))
        await asyncio.sleep(0.05)

    prepare_mock.assert_awaited_once()
    finish_mock.assert_awaited_once()
    assert finish_mock.await_args.kwargs["succeeded"] is True
    assert instance._include_parent_session_id is True
