# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime SubagentSessionManager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError, ExecutionError, build_error
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager


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


@pytest.mark.asyncio
async def test_create_main_path() -> None:
    manager = _manager()
    session = MockSession()

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=session,
    ):
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
    assert session.pre_run_calls == 1


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
async def test_pre_run_failure_closes_session_and_leaves_table_empty() -> None:
    manager = _manager()
    session = MockSession(pre_run_error=RuntimeError("pre_run failed"))

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=session,
    ):
        with pytest.raises(RuntimeError, match="pre_run failed"):
            await manager.create(
                subagent_type="explore",
                subagent_id="parent_sub_explore",
                parent_session_id="parent",
                display_name="Explorer",
                role="researcher",
            )

    assert session.close_stream_calls == 1
    assert manager.list_ids() == []


@pytest.mark.asyncio
async def test_find_and_get() -> None:
    manager = _manager()
    session = MockSession()

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=session,
    ):
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
    session = MockSession()

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=session,
    ):
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

    with patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=MockSession(),
    ):
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
async def test_restore_raises_runtime_error() -> None:
    manager = _manager()

    with pytest.raises(ExecutionError, match="restore not supported"):
        await manager.restore("sid-1")


@pytest.mark.asyncio
async def test_persist_is_noop() -> None:
    manager = _manager()
    await manager.persist("sid-1")
