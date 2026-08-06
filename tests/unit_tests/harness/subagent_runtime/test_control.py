# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime SubagentControl."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError, ExecutionError, ValidationError, build_error
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.control import SubagentControl
from openjiuwen.harness.subagent_runtime.instance import SubagentInstance
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind, UserInputOp
from tests.unit_tests.harness.subagent_runtime.test_instance import MockAgent
from tests.unit_tests.harness.subagent_runtime.test_session_manager import MockParentAgent, MockSession as ManagerSession


@dataclass
class ControlParentAgent(MockParentAgent):
    mock_agent: MockAgent = field(default_factory=MockAgent)

    def create_subagent(
        self,
        subagent_type: str,
        subsession_id: str,
        browser_capabilities: list[str] | None = None,
    ) -> MockAgent:
        super().create_subagent(subagent_type, subsession_id, browser_capabilities)
        return self.mock_agent


def _control(
    *,
    parent: ControlParentAgent | None = None,
    config: SubagentRuntimeConfig | None = None,
    parent_session_id: str = "parent",
) -> SubagentControl:
    return SubagentControl(
        parent or ControlParentAgent(),
        parent_session_id,
        config=config,
    )


def _patch_create_session(session: ManagerSession | None = None):
    return patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        return_value=session or ManagerSession(),
    )


@pytest.mark.asyncio
async def test_spawn_returns_pending_init() -> None:
    control = _control()

    with _patch_create_session():
        result = await control.spawn("explore", "hello")

    assert result.status.kind is SubagentStatusKind.PENDING_INIT
    assert control.get_status(result.subagent_id).kind is SubagentStatusKind.PENDING_INIT
    assert control._registry.find_metadata(result.subagent_id) is not None


@pytest.mark.asyncio
async def test_spawn_create_failure_rolls_back_quota() -> None:
    parent = ControlParentAgent(
        create_error=build_error(
            StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
            error_msg="missing",
        ),
    )
    control = _control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1))

    with pytest.raises(AgentError):
        await control.spawn("explore", "hello")

    assert control._registry.count == 0


@pytest.mark.asyncio
async def test_spawn_enqueue_failure_releases_quota_and_removes_instance() -> None:
    control = _control(config=SubagentRuntimeConfig(max_subagents=1))
    calls = {"count": 0}
    original_enqueue = SubagentInstance.enqueue

    async def enqueue_maybe_fail(self, op: UserInputOp) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("enqueue failed")
        await original_enqueue(self, op)

    with _patch_create_session():
        with patch.object(SubagentInstance, "enqueue", enqueue_maybe_fail):
            with pytest.raises(RuntimeError, match="enqueue failed"):
                await control.spawn("explore", "hello")

    assert control._registry.count == 0
    assert control._manager.list_ids() == []


@pytest.mark.asyncio
async def test_wait_all_semantics() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.05))
    control = _control(parent=parent)

    with _patch_create_session():
        first = await control.spawn("explore", "one")
        second = await control.spawn("explore", "two")

    wait_result = await control.wait([first.subagent_id, second.subagent_id], timeout_ms=5_000)

    assert wait_result.timed_out is False
    assert wait_result.statuses[first.subagent_id].kind is SubagentStatusKind.COMPLETED
    assert wait_result.statuses[second.subagent_id].kind is SubagentStatusKind.COMPLETED
    assert set(wait_result.results) == {first.subagent_id, second.subagent_id}


@pytest.mark.asyncio
async def test_wait_not_found_does_not_short_circuit() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.05))
    control = _control(parent=parent)

    with _patch_create_session():
        live = await control.spawn("explore", "hello")

    wait_result = await control.wait(["missing-id", live.subagent_id], timeout_ms=5_000)

    assert wait_result.statuses["missing-id"].kind is SubagentStatusKind.NOT_FOUND
    assert wait_result.statuses[live.subagent_id].kind is SubagentStatusKind.COMPLETED
    assert wait_result.timed_out is False


@pytest.mark.asyncio
async def test_wait_timeout_keeps_subagent_running() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    control = _control(parent=parent)

    with _patch_create_session():
        spawned = await control.spawn("explore", "slow")

    with patch("openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN", 50):
        first_wait = await control.wait([spawned.subagent_id], timeout_ms=50)
    assert first_wait.timed_out is True
    assert first_wait.statuses[spawned.subagent_id].kind is SubagentStatusKind.RUNNING

    second_wait = await control.wait([spawned.subagent_id], timeout_ms=5_000)
    assert second_wait.timed_out is False
    assert second_wait.statuses[spawned.subagent_id].kind is SubagentStatusKind.COMPLETED


@pytest.mark.asyncio
async def test_wait_deduplicates_ids() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    control = _control(parent=parent)

    with _patch_create_session():
        spawned = await control.spawn("explore", "hello")

    wait_result = await control.wait([spawned.subagent_id, spawned.subagent_id], timeout_ms=5_000)

    assert set(wait_result.statuses) == {spawned.subagent_id}
    assert wait_result.timed_out is False


@pytest.mark.asyncio
async def test_get_status_and_subscribe_status() -> None:
    control = _control()

    assert control.get_status("missing").kind is SubagentStatusKind.NOT_FOUND

    with pytest.raises(AgentError):
        control.subscribe_status("missing")

    with _patch_create_session():
        spawned = await control.spawn("explore", "hello")

    receiver = control.subscribe_status(spawned.subagent_id)
    assert receiver.current().kind is SubagentStatusKind.PENDING_INIT


@pytest.mark.asyncio
async def test_close_idle_instance() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    control = _control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1))

    with _patch_create_session():
        spawned = await control.spawn("explore", "hello")
        await control.wait([spawned.subagent_id], timeout_ms=5_000)

    previous = await control.close(spawned.subagent_id, reason="manual")

    assert previous.kind is SubagentStatusKind.COMPLETED
    assert control.get_status(spawned.subagent_id).kind is SubagentStatusKind.NOT_FOUND
    assert control._registry.count == 0
    assert control.list_live() == []


@pytest.mark.asyncio
async def test_close_running_instance_raises() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    control = _control(parent=parent)

    with _patch_create_session():
        spawned = await control.spawn("explore", "slow")

    await asyncio.sleep(0.02)

    with pytest.raises(ExecutionError, match="cannot close running subagent"):
        await control.close(spawned.subagent_id)

    assert control._manager.find(spawned.subagent_id) is not None


@pytest.mark.asyncio
async def test_lru_evicts_idle_instance_when_full() -> None:
    config = SubagentRuntimeConfig(max_subagents=1, enable_lru_eviction=True)
    parent = ControlParentAgent(mock_agent=MockAgent())
    control = _control(parent=parent, config=config)

    with _patch_create_session():
        first = await control.spawn("explore", "first")
        await control.wait([first.subagent_id], timeout_ms=5_000)
        second = await control.spawn("explore", "second")

    assert first.subagent_id != second.subagent_id
    assert control.get_status(first.subagent_id).kind is SubagentStatusKind.NOT_FOUND
    assert control._registry.find_metadata(first.subagent_id) is None
    assert control._registry.find_metadata(second.subagent_id) is not None


@pytest.mark.asyncio
async def test_lru_does_not_evict_running_instance() -> None:
    config = SubagentRuntimeConfig(max_subagents=1, enable_lru_eviction=True)
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    control = _control(parent=parent, config=config)

    with _patch_create_session():
        running = await control.spawn("explore", "slow")

    await asyncio.sleep(0.02)

    with pytest.raises(ValidationError):
        await control.spawn("explore", "blocked")

    assert control._manager.find(running.subagent_id) is not None


@pytest.mark.asyncio
async def test_list_live_matches_spawned_metadata() -> None:
    control = _control()

    with _patch_create_session():
        spawned = await control.spawn("explore", "hello", display_name="Explorer", role="researcher")

    live = control.list_live()
    assert len(live) == 1
    assert live[0].subagent_id == spawned.subagent_id
    assert live[0].display_name == "Explorer"
    assert live[0].role == "researcher"


@pytest.mark.asyncio
async def test_wait_timeout_ms_is_clamped() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    control = _control(parent=parent)

    with _patch_create_session():
        spawned = await control.spawn("explore", "slow")

    with patch("openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN", 50):
        short_wait = await control.wait([spawned.subagent_id], timeout_ms=1)
    assert short_wait.timed_out is True
    assert short_wait.statuses[spawned.subagent_id].kind is SubagentStatusKind.RUNNING

    with patch("openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MAX", 100):
        missing = await control.wait(["missing"], timeout_ms=999_999)
    assert missing.statuses["missing"].kind is SubagentStatusKind.NOT_FOUND
    assert missing.timed_out is False


# ---------------------------------------------------------------------------
# Flow tests: Control multi-step lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_spawn_wait_single() -> None:
    """spawn → wait：秒回只有 id/status，wait 才拿到 output。"""
    parent = ControlParentAgent(mock_agent=MockAgent(output="analysis done"))
    control = _control(parent=parent)

    with _patch_create_session():
        spawned = await control.spawn("explore", "analyze this")

    assert spawned.status.kind is SubagentStatusKind.PENDING_INIT
    assert spawned.subagent_id
    assert spawned.task_id
    assert control._registry.count == 1

    waited = await control.wait([spawned.subagent_id], timeout_ms=5_000)

    assert waited.timed_out is False
    assert waited.statuses[spawned.subagent_id].kind is SubagentStatusKind.COMPLETED
    assert waited.results[spawned.subagent_id] == "analysis done"
    assert parent.mock_agent.invoke_calls == 1


@pytest.mark.asyncio
async def test_flow_spawn_wait_close_release() -> None:
    """spawn → wait → close → release → 再次 spawn 成功（名额复用）。"""
    config = SubagentRuntimeConfig(max_subagents=1)
    parent = ControlParentAgent(mock_agent=MockAgent())
    control = _control(parent=parent, config=config)

    with _patch_create_session():
        first = await control.spawn("explore", "first")
        first_wait = await control.wait([first.subagent_id], timeout_ms=5_000)
        assert first_wait.timed_out is False
        assert first_wait.statuses[first.subagent_id].kind is SubagentStatusKind.COMPLETED

        previous = await control.close(first.subagent_id, reason="manual")
        assert previous.kind is SubagentStatusKind.COMPLETED
        assert control._registry.count == 0
        assert control._manager.list_ids() == []
        assert control.get_status(first.subagent_id).kind is SubagentStatusKind.NOT_FOUND

        second = await control.spawn("explore", "second")
        second_wait = await control.wait([second.subagent_id], timeout_ms=5_000)

    assert second.subagent_id != first.subagent_id
    assert control._registry.count == 1
    assert control._manager.find(first.subagent_id) is None
    assert control._manager.find(second.subagent_id) is not None
    assert second_wait.timed_out is False
    assert second_wait.statuses[second.subagent_id].kind is SubagentStatusKind.COMPLETED
    assert second_wait.results[second.subagent_id] == "done"


@pytest.mark.asyncio
async def test_flow_spawn_failure_then_success() -> None:
    """create 失败 rollback 后，同 session 可再次 spawn 成功。"""
    parent = ControlParentAgent()
    control = _control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1))

    parent.create_error = build_error(
        StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
        error_msg="missing",
    )
    with _patch_create_session():
        with pytest.raises(AgentError):
            await control.spawn("explore", "will fail")
        assert control._registry.count == 0

        parent.create_error = None
        parent.mock_agent = MockAgent(output="recovered")
        succeeded = await control.spawn("explore", "retry")

    waited = await control.wait([succeeded.subagent_id], timeout_ms=5_000)
    assert waited.timed_out is False
    assert waited.results[succeeded.subagent_id] == "recovered"
    assert control._registry.count == 1
