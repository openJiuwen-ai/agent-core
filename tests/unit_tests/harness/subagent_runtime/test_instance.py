# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime SubagentInstance."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.ids import new_task_id
from openjiuwen.harness.subagent_runtime.instance import SubagentInstance
from openjiuwen.harness.subagent_runtime.models import (
    SubagentMetadata,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
)
from openjiuwen.harness.subagent_runtime.registry import SubagentRegistry


@dataclass
class MockSession:
    commit_calls: int = 0

    async def commit(self) -> None:
        self.commit_calls += 1


@dataclass
class MockAgent:
    output: str = "done"
    delay_s: float = 0.0
    error: BaseException | None = None
    invoke_calls: int = 0
    active_invokes: int = 0
    max_active_invokes: int = 0
    prepare_calls: int = 0
    cleanup_calls: int = 0

    async def invoke(self, inputs: dict[str, str], *, session: MockSession) -> dict[str, str]:
        self.invoke_calls += 1
        self.active_invokes += 1
        self.max_active_invokes = max(self.max_active_invokes, self.active_invokes)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.error is not None:
                raise self.error
            return {"output": self.output, "conversation_id": inputs["conversation_id"]}
        finally:
            self.active_invokes -= 1

    def prepare_task_resources(self) -> None:
        self.prepare_calls += 1

    def cleanup_task_resources(self) -> None:
        self.cleanup_calls += 1


def _metadata(subagent_id: str, *, parent_session_id: str = "parent") -> SubagentMetadata:
    now = time.time()
    return SubagentMetadata(
        subagent_id=subagent_id,
        subagent_type="explore",
        display_name="Explorer",
        role="researcher",
        parent_session_id=parent_session_id,
        created_at=now,
        last_used_at=time.monotonic(),
    )


def _make_instance(
    *,
    subagent_id: str = "parent_sub_explore",
    agent: MockAgent | None = None,
    session: MockSession | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[SubagentInstance, MockAgent, MockSession]:
    mock_agent = agent or MockAgent()
    mock_session = session or MockSession()
    instance = SubagentInstance(
        subagent_id=subagent_id,
        subagent_type="explore",
        display_name="Explorer",
        role="researcher",
        parent_session_id="parent",
        agent=mock_agent,
        session=mock_session,
        running_semaphore=semaphore or asyncio.Semaphore(5),
    )
    return instance, mock_agent, mock_session


@pytest.mark.asyncio
async def test_instance_starts_pending_init_without_worker() -> None:
    instance, _, _ = _make_instance()

    assert instance.agent_status() == SubagentStatus.pending_init()
    assert instance.revision() == 0
    assert instance._worker_task is None


@pytest.mark.asyncio
async def test_user_input_runs_to_completed() -> None:
    instance, agent, session = _make_instance()
    await instance.start_worker()
    task_id = new_task_id()

    await instance.enqueue(UserInputOp(query="hello", task_id=task_id))
    await asyncio.sleep(0.05)

    assert instance.agent_status() == SubagentStatus.completed("done")
    assert instance.last_output == "done"
    assert instance.last_task_id == task_id
    assert agent.invoke_calls == 1
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_two_user_inputs_run_serially() -> None:
    instance, agent, _ = _make_instance()
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="first", task_id="t1"))
    await instance.enqueue(UserInputOp(query="second", task_id="t2"))
    await asyncio.sleep(0.05)

    assert agent.invoke_calls == 2
    assert instance.agent_status() == SubagentStatus.completed("done")
    assert instance.last_task_id == "t2"


@pytest.mark.asyncio
async def test_invoke_error_sets_errored_and_worker_survives() -> None:
    agent = MockAgent(error=RuntimeError("boom"))
    instance, _, session = _make_instance(agent=agent)
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="fail", task_id="t1"))
    await asyncio.sleep(0.05)

    status = instance.agent_status()
    assert status.kind == SubagentStatusKind.ERRORED
    assert status.message == "boom"
    assert session.commit_calls == 1

    agent.error = None
    await instance.enqueue(UserInputOp(query="retry", task_id="t2"))
    await asyncio.sleep(0.05)
    assert instance.agent_status().kind == SubagentStatusKind.COMPLETED


@pytest.mark.asyncio
async def test_invoke_base_error_preserves_error_code() -> None:
    base_error = build_error(
        StatusCode.DEEPAGENT_SUBAGENT_RUNTIME_ERROR,
        error_msg="worker failed",
    )
    instance, _, _ = _make_instance(agent=MockAgent(error=base_error))
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="fail", task_id="t1"))
    await asyncio.sleep(0.05)

    status = instance.agent_status()
    assert status.kind == SubagentStatusKind.ERRORED
    assert status.error_code == StatusCode.DEEPAGENT_SUBAGENT_RUNTIME_ERROR.name


@pytest.mark.asyncio
async def test_interrupt_active_turn() -> None:
    instance, _, session = _make_instance(agent=MockAgent(delay_s=0.2))
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)

    interrupted = await instance.interrupt()
    await asyncio.sleep(0.05)

    assert interrupted is True
    assert instance.agent_status().kind == SubagentStatusKind.INTERRUPTED
    assert instance._worker_task is not None
    assert not instance._worker_task.done()
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_interrupt_on_completed_is_noop() -> None:
    instance, _, _ = _make_instance()
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="done", task_id="t1"))
    await asyncio.sleep(0.05)

    revision = instance.revision()
    interrupted = await instance.interrupt()

    assert interrupted is False
    assert instance.revision() == revision
    assert instance.agent_status().kind == SubagentStatusKind.COMPLETED


@pytest.mark.asyncio
async def test_interrupt_when_idle_is_noop() -> None:
    instance, _, _ = _make_instance()
    await instance.start_worker()

    assert await instance.interrupt() is False
    assert instance.agent_status().kind == SubagentStatusKind.PENDING_INIT


@pytest.mark.asyncio
async def test_interrupt_then_new_input_completes() -> None:
    agent = MockAgent(delay_s=0.2)
    instance, _, _ = _make_instance(agent=agent)
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)
    await instance.interrupt()
    await asyncio.sleep(0.25)

    agent.delay_s = 0.0
    await instance.enqueue(UserInputOp(query="again", task_id="t2"))
    await asyncio.sleep(0.05)

    assert instance.agent_status() == SubagentStatus.completed("done")
    assert instance.last_task_id == "t2"


@pytest.mark.asyncio
async def test_shutdown_closes_instance_and_stops_worker() -> None:
    instance, _, _ = _make_instance()
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="done", task_id="t1"))
    await asyncio.sleep(0.05)

    await instance.shutdown("manual")

    assert instance.is_closed()
    assert instance.agent_status() == SubagentStatus.closed("manual")
    assert instance._worker_task is not None
    assert instance._worker_task.done()


@pytest.mark.asyncio
async def test_shutdown_during_active_turn() -> None:
    instance, _, _ = _make_instance(agent=MockAgent(delay_s=0.5))
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)

    await instance.shutdown("manual")
    await asyncio.sleep(0.05)

    assert instance.is_closed()
    assert instance.agent_status().kind == SubagentStatusKind.CLOSED


@pytest.mark.asyncio
async def test_shutdown_is_idempotent() -> None:
    instance, _, _ = _make_instance()
    await instance.start_worker()

    await instance.shutdown("manual")
    revision = instance.revision()
    await instance.shutdown("manual")

    assert instance.revision() == revision
    assert instance.agent_status().close_reason == "manual"


@pytest.mark.asyncio
async def test_shared_semaphore_serializes_invokes_across_instances() -> None:
    semaphore = asyncio.Semaphore(1)
    agent_a = MockAgent(delay_s=0.1)
    agent_b = MockAgent(delay_s=0.1)
    instance_a, _, _ = _make_instance(agent=agent_a, semaphore=semaphore)
    instance_b, _, _ = _make_instance(
        subagent_id="parent_sub_explore_b",
        agent=agent_b,
        semaphore=semaphore,
    )
    await instance_a.start_worker()
    await instance_b.start_worker()
    await instance_a.enqueue(UserInputOp(query="a", task_id="a1"))
    await instance_b.enqueue(UserInputOp(query="b", task_id="b1"))
    await asyncio.sleep(0.25)

    assert agent_a.max_active_invokes == 1
    assert agent_b.max_active_invokes == 1


@pytest.mark.asyncio
async def test_running_only_after_semaphore_acquired() -> None:
    semaphore = asyncio.Semaphore(1)
    blocker = MockAgent(delay_s=0.2)
    waiter = MockAgent(delay_s=0.05)
    holder, _, _ = _make_instance(agent=blocker, semaphore=semaphore)
    waiting, _, _ = _make_instance(agent=waiter, semaphore=semaphore)
    waiting, _, _ = _make_instance(
        subagent_id="parent_sub_wait",
        agent=waiter,
        semaphore=semaphore,
    )
    await holder.start_worker()
    await waiting.start_worker()
    await holder.enqueue(UserInputOp(query="hold", task_id="h1"))
    await asyncio.sleep(0.02)
    await waiting.enqueue(UserInputOp(query="wait", task_id="w1"))
    await asyncio.sleep(0.02)

    assert holder.agent_status().kind == SubagentStatusKind.RUNNING
    assert waiting.agent_status().kind != SubagentStatusKind.RUNNING


@pytest.mark.asyncio
async def test_finalize_runs_cleanup_and_commit_on_cancel() -> None:
    agent = MockAgent(delay_s=0.2)
    instance, _, session = _make_instance(agent=agent)
    await instance.start_worker()
    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)
    await instance.interrupt()
    await asyncio.sleep(0.05)

    assert agent.cleanup_calls == 1
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_is_evictable() -> None:
    instance, _, _ = _make_instance(agent=MockAgent(delay_s=0.2))
    await instance.start_worker()

    assert instance.is_evictable() is True

    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)
    assert instance.is_evictable() is False

    await asyncio.sleep(0.3)
    assert instance.is_evictable() is True


@pytest.mark.asyncio
async def test_subscribe_status_wait_for_final() -> None:
    instance, _, _ = _make_instance()
    await instance.start_worker()
    receiver = instance.subscribe_status()
    wait_task = asyncio.create_task(receiver.wait_for_final())

    await asyncio.sleep(0)
    await instance.enqueue(UserInputOp(query="done", task_id="t1"))
    status = await asyncio.wait_for(wait_task, timeout=1.0)

    assert status.kind == SubagentStatusKind.COMPLETED


# ---------------------------------------------------------------------------
# Flow tests: Registry + Instance multi-step lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_reserve_run_shutdown_release() -> None:
    """reserve → create → commit → run → wait → shutdown → release."""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    subagent_id = "parent_sub_explore"
    reservation = registry.reserve_slot()

    instance, agent, _ = _make_instance(subagent_id=subagent_id)
    await instance.start_worker()
    task_id = new_task_id()
    reservation.commit(_metadata(subagent_id))

    assert registry.count == 1
    assert registry.find_metadata(subagent_id) is not None

    receiver = instance.subscribe_status()
    wait_task = asyncio.create_task(receiver.wait_for_final())
    await instance.enqueue(UserInputOp(query="hello", task_id=task_id))
    status = await asyncio.wait_for(wait_task, timeout=1.0)

    assert status.kind == SubagentStatusKind.COMPLETED
    assert agent.invoke_calls == 1
    assert instance.last_task_id == task_id

    await instance.shutdown("manual")
    registry.release(subagent_id)

    assert instance.is_closed()
    assert registry.count == 0
    assert registry.find_metadata(subagent_id) is None


@pytest.mark.asyncio
async def test_flow_commit_shutdown_release_reuses_quota() -> None:
    """commit 后 shutdown + release，名额可再次 reserve。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))
    subagent_id = "parent_sub_explore"
    reservation = registry.reserve_slot()

    instance, _, _ = _make_instance(subagent_id=subagent_id)
    await instance.start_worker()
    reservation.commit(_metadata(subagent_id))
    assert registry.count == 1

    await instance.shutdown("manual")
    registry.release(subagent_id)
    assert registry.count == 0

    retry = registry.reserve_slot()
    retry.commit(_metadata("parent_sub_explore_2"))
    assert registry.count == 1
    assert registry.find_metadata("parent_sub_explore_2") is not None


@pytest.mark.asyncio
async def test_flow_two_instances_registry_and_semaphore() -> None:
    """同一 Registry 下两实例：占位 → 跑任务 → 共享 Semaphore → 释放。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    semaphore = asyncio.Semaphore(1)

    r1 = registry.reserve_slot()
    inst1, agent1, _ = _make_instance(subagent_id="sid-1", semaphore=semaphore)
    await inst1.start_worker()
    r1.commit(_metadata("sid-1"))

    r2 = registry.reserve_slot()
    inst2, agent2, _ = _make_instance(
        subagent_id="sid-2",
        agent=MockAgent(delay_s=0.1),
        semaphore=semaphore,
    )
    await inst2.start_worker()
    r2.commit(_metadata("sid-2"))

    assert registry.count == 2

    await inst1.enqueue(UserInputOp(query="a", task_id="t1"))
    await inst2.enqueue(UserInputOp(query="b", task_id="t2"))
    await asyncio.sleep(0.25)

    assert agent1.max_active_invokes == 1
    assert agent2.max_active_invokes == 1

    await inst1.shutdown("manual")
    await inst2.shutdown("manual")
    registry.release("sid-1")
    registry.release("sid-2")

    assert registry.count == 0


@pytest.mark.asyncio
async def test_flow_interrupt_then_complete_with_registry() -> None:
    """reserve → run → interrupt → 再跑一轮 → shutdown → release。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))
    subagent_id = "parent_sub_explore"
    reservation = registry.reserve_slot()

    agent = MockAgent(delay_s=0.2)
    instance, _, _ = _make_instance(subagent_id=subagent_id, agent=agent)
    await instance.start_worker()
    reservation.commit(_metadata(subagent_id))

    await instance.enqueue(UserInputOp(query="slow", task_id="t1"))
    await asyncio.sleep(0.02)
    await instance.interrupt()
    await asyncio.sleep(0.25)

    agent.delay_s = 0.0
    await instance.enqueue(UserInputOp(query="retry", task_id="t2"))
    await asyncio.sleep(0.05)

    assert instance.agent_status().kind == SubagentStatusKind.COMPLETED
    assert instance.last_task_id == "t2"

    await instance.shutdown("manual")
    registry.release(subagent_id)
    assert registry.count == 0
