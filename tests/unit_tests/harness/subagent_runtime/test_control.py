# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime SubagentControl."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError, ExecutionError, ValidationError, build_error
from openjiuwen.core.session.agent import Session
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
        return MockAgent(
            output=self.mock_agent.output,
            delay_s=self.mock_agent.delay_s,
            stream_error=self.mock_agent.stream_error,
            prepare_error=self.mock_agent.prepare_error,
        )


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
    def _factory(**kwargs) -> ManagerSession:
        return session or ManagerSession()

    return patch(
        "openjiuwen.harness.subagent_runtime.session_manager.create_agent_session",
        side_effect=_factory,
    )


@asynccontextmanager
async def _patched_control(*, parent: ControlParentAgent | None = None, config: SubagentRuntimeConfig | None = None):
    control = _control(parent=parent, config=config)
    with _patch_create_session(), patch(
        "openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN",
        100,
    ):
        try:
            yield control
        finally:
            for sid in list(control._manager.list_ids()):
                await control._manager.remove(sid, reason="test_cleanup")
                control._registry.release(sid)


async def _wait_for_turn(agent: MockAgent, *, extra_s: float = 0.05) -> None:
    await asyncio.sleep(agent.delay_s + extra_s)


@pytest.mark.asyncio
async def test_spawn_returns_pending_init() -> None:
    async with _patched_control() as control:
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
    async with _patched_control(parent=parent) as control:
        first = await control.spawn("explore", "one")
        second = await control.spawn("explore", "two")
        await _wait_for_turn(parent.mock_agent)

        wait_result = await control.wait([first.subagent_id, second.subagent_id], timeout_ms=500)

        assert wait_result.timed_out is False
        assert wait_result.statuses[first.subagent_id].kind is SubagentStatusKind.COMPLETED
        assert wait_result.statuses[second.subagent_id].kind is SubagentStatusKind.COMPLETED
        assert set(wait_result.results) == {first.subagent_id, second.subagent_id}


@pytest.mark.asyncio
async def test_wait_not_found_does_not_short_circuit() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.05))
    async with _patched_control(parent=parent) as control:
        live = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)

        wait_result = await control.wait(["missing-id", live.subagent_id], timeout_ms=500)

        assert wait_result.statuses["missing-id"].kind is SubagentStatusKind.NOT_FOUND
        assert wait_result.statuses[live.subagent_id].kind is SubagentStatusKind.COMPLETED
        assert wait_result.timed_out is False


@pytest.mark.asyncio
async def test_wait_timeout_keeps_subagent_running() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "slow")

        with patch("openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN", 50):
            first_wait = await control.wait([spawned.subagent_id], timeout_ms=50)
        assert first_wait.timed_out is True
        assert first_wait.statuses[spawned.subagent_id].kind is SubagentStatusKind.RUNNING

        await _wait_for_turn(parent.mock_agent)
        second_wait = await control.wait([spawned.subagent_id], timeout_ms=500)
        assert second_wait.timed_out is False
        assert second_wait.statuses[spawned.subagent_id].kind is SubagentStatusKind.COMPLETED


@pytest.mark.asyncio
async def test_wait_deduplicates_ids() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)

        wait_result = await control.wait([spawned.subagent_id, spawned.subagent_id], timeout_ms=500)

        assert set(wait_result.statuses) == {spawned.subagent_id}
        assert wait_result.timed_out is False


@pytest.mark.asyncio
async def test_get_status_and_subscribe_status() -> None:
    async with _patched_control() as control:
        assert control.get_status("missing").kind is SubagentStatusKind.NOT_FOUND

        with pytest.raises(AgentError):
            control.subscribe_status("missing")

        spawned = await control.spawn("explore", "hello")
        receiver = control.subscribe_status(spawned.subagent_id)
        assert receiver.current().kind is SubagentStatusKind.PENDING_INIT


@pytest.mark.asyncio
async def test_close_idle_instance() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1)) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)

        previous = await control.close(spawned.subagent_id, reason="manual")

        assert previous.kind is SubagentStatusKind.COMPLETED
        assert control.get_status(spawned.subagent_id).kind is SubagentStatusKind.NOT_FOUND
        assert control._registry.count == 0
        assert control.list_live() == []


@pytest.mark.asyncio
async def test_close_running_instance_raises() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "slow")
        await asyncio.sleep(0.02)

        with pytest.raises(ExecutionError, match="cannot close running subagent"):
            await control.close(spawned.subagent_id)

        assert control._manager.find(spawned.subagent_id) is not None


@pytest.mark.asyncio
async def test_lru_evicts_idle_instance_when_full() -> None:
    config = SubagentRuntimeConfig(max_subagents=1, enable_lru_eviction=True)
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent, config=config) as control:
        first = await control.spawn("explore", "first")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([first.subagent_id], timeout_ms=500)
        second = await control.spawn("explore", "second")

        assert first.subagent_id != second.subagent_id
        assert control.get_status(first.subagent_id).kind is SubagentStatusKind.NOT_FOUND
        assert control._registry.find_metadata(first.subagent_id) is None
        assert control._registry.find_metadata(second.subagent_id) is not None


@pytest.mark.asyncio
async def test_lru_does_not_evict_running_instance() -> None:
    config = SubagentRuntimeConfig(max_subagents=1, enable_lru_eviction=True)
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    async with _patched_control(parent=parent, config=config) as control:
        running = await control.spawn("explore", "slow")
        await asyncio.sleep(0.02)

        with pytest.raises(ValidationError):
            await control.spawn("explore", "blocked")

        assert control._manager.find(running.subagent_id) is not None


@pytest.mark.asyncio
async def test_list_live_matches_spawned_metadata() -> None:
    async with _patched_control() as control:
        spawned = await control.spawn("explore", "hello", display_name="Explorer", role="researcher")

        live = control.list_live()
        assert len(live) == 1
        assert live[0].subagent_id == spawned.subagent_id
        assert live[0].display_name == "Explorer"
        assert live[0].role == "researcher"


@pytest.mark.asyncio
async def test_wait_timeout_ms_is_clamped() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    async with _patched_control(parent=parent) as control:
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
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "analyze this")

        assert spawned.status.kind is SubagentStatusKind.PENDING_INIT
        assert spawned.subagent_id
        assert spawned.task_id
        assert control._registry.count == 1

        await _wait_for_turn(parent.mock_agent)
        waited = await control.wait([spawned.subagent_id], timeout_ms=500)

        assert waited.timed_out is False
        assert waited.statuses[spawned.subagent_id].kind is SubagentStatusKind.COMPLETED
        assert waited.results[spawned.subagent_id] == "analysis done"
        instance = control._manager.get(spawned.subagent_id)
        assert instance._agent.stream_calls == 1


@pytest.mark.asyncio
async def test_flow_spawn_wait_close_release() -> None:
    """spawn → wait → close → release → 再次 spawn 成功（名额复用）。"""
    config = SubagentRuntimeConfig(max_subagents=1)
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent, config=config) as control:
        first = await control.spawn("explore", "first")
        await _wait_for_turn(parent.mock_agent)
        first_wait = await control.wait([first.subagent_id], timeout_ms=500)
        assert first_wait.timed_out is False
        assert first_wait.statuses[first.subagent_id].kind is SubagentStatusKind.COMPLETED

        previous = await control.close(first.subagent_id, reason="manual")
        assert previous.kind is SubagentStatusKind.COMPLETED
        assert control._registry.count == 0
        assert control._manager.list_ids() == []
        assert control.get_status(first.subagent_id).kind is SubagentStatusKind.NOT_FOUND

        second = await control.spawn("explore", "second")
        await _wait_for_turn(parent.mock_agent)
        second_wait = await control.wait([second.subagent_id], timeout_ms=500)

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
    parent.create_error = build_error(
        StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
        error_msg="missing",
    )
    control = _control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1))
    with _patch_create_session():
        with pytest.raises(AgentError):
            await control.spawn("explore", "will fail")
    assert control._registry.count == 0

    parent.create_error = None
    parent.mock_agent = MockAgent(output="recovered")
    async with _patched_control(parent=parent, config=SubagentRuntimeConfig(max_subagents=1)) as control:
        succeeded = await control.spawn("explore", "retry")
        await _wait_for_turn(parent.mock_agent)
        waited = await control.wait([succeeded.subagent_id], timeout_ms=500)

        assert waited.timed_out is False
        assert waited.results[succeeded.subagent_id] == "recovered"
        assert control._registry.count == 1


@pytest.mark.asyncio
async def test_capacity_and_describe_live() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    config = SubagentRuntimeConfig(max_subagents=3)
    async with _patched_control(parent=parent, config=config) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)

        assert control.capacity() == {"used": 1, "max": 3}
        rows = control.describe_live()
        assert len(rows) == 1
        assert rows[0]["subagent_id"] == spawned.subagent_id
        assert rows[0]["status"] == "closed"
        assert rows[0]["closed_reason"] == "completed"
        assert rows[0]["revision"] >= 1
        assert rows[0]["task_description"] == "hello"
        assert "result" not in rows[0]


@pytest.mark.asyncio
async def test_cancel_all_closes_running_subagents() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.2))
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "running")
        await asyncio.sleep(0.05)
        assert control.get_status(spawned.subagent_id).kind is SubagentStatusKind.RUNNING

        closed = await control.cancel_all(reason="parent_ended")

        assert spawned.subagent_id in closed
        assert control._registry.count == 0
        assert control._manager.list_ids() == []


@pytest.mark.asyncio
async def test_duplicate_sticky_spawn_rejected() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        first = await control.spawn("browser_agent", "first")
        await _wait_for_turn(parent.mock_agent)

        with pytest.raises(Exception, match="subagent already live"):
            await control.spawn("browser_agent", "second")

        assert control._manager.find(first.subagent_id) is not None


@pytest.mark.asyncio
async def test_cancel_all_releases_slots_when_one_remove_fails() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.05))
    async with _patched_control(
        parent=parent,
        config=SubagentRuntimeConfig(max_subagents=2),
    ) as control:
        first = await control.spawn("explore", "one")
        second = await control.spawn("explore", "two")
        await asyncio.sleep(0.02)

        original_remove = control._manager.remove
        calls = {"count": 0}

        async def remove_maybe_fail(sid: str, reason: str = "manual"):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("remove failed")
            return await original_remove(sid, reason=reason)

        with patch.object(control._manager, "remove", side_effect=remove_maybe_fail):
            closed = await control.cancel_all(reason="parent_ended")

        assert first.subagent_id in closed
        assert second.subagent_id in closed
        assert control._registry.count == 0
        # Registry slots are always released; a failed remove may leave a ghost instance.
        assert len(control._manager.list_ids()) <= 1


@pytest.mark.asyncio
async def test_describe_one_returns_external_payload() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)

        payload = control.describe_one(spawned.subagent_id)
        assert payload is not None
        assert payload["subagent_id"] == spawned.subagent_id
        assert payload["status"] == "closed"
        assert payload["closed_reason"] == "completed"
        assert payload["task_description"] == "hello"
        assert payload["parent_session_id"] == "parent"


@pytest.mark.asyncio
async def test_describe_one_missing_returns_none() -> None:
    control = _control()
    assert control.describe_one("missing") is None


@pytest.mark.asyncio
async def test_worker_terminal_emits_without_wait() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.05))
    session = Session(session_id="parent")
    session.write_stream = AsyncMock()
    control = SubagentControl(parent, "parent", parent_session=session)

    with _patch_create_session():
        spawned = await control.spawn("explore", "background")
        await _wait_for_turn(parent.mock_agent)
        try:
            assert session.write_stream.await_count >= 1
            payloads = [
                call.args[0].payload["subagent_updated"]
                for call in session.write_stream.await_args_list
            ]
            closed_payloads = [item for item in payloads if item.get("status") == "closed"]
            assert closed_payloads
            assert closed_payloads[-1]["subagent_id"] == spawned.subagent_id
        finally:
            for sid in list(control._manager.list_ids()):
                await control._manager.remove(sid, reason="test_cleanup")
                control._registry.release(sid)


@pytest.mark.asyncio
async def test_close_writes_closed_record_and_describe_one_returns_closed() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello", display_name="Explorer", role="researcher")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)

        await control.close(spawned.subagent_id, reason="manual")

        assert spawned.subagent_id in control._closed_records
        payload = control.describe_one(spawned.subagent_id)
        assert payload is not None
        assert payload["status"] == "closed"
        assert payload["closed_reason"] == "manual"
        assert payload["display_name"] == "Explorer"


@pytest.mark.asyncio
async def test_send_input_on_completed_instance() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(output="first", delay_s=0.05))
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "first")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)

        parent.mock_agent.output = "second"
        instance = control._manager.get(spawned.subagent_id)
        instance._agent.output = "second"
        task_id = await control.send_input(spawned.subagent_id, "continue")

        assert task_id
        await _wait_for_turn(parent.mock_agent)
        waited = await control.wait([spawned.subagent_id], timeout_ms=500)
        assert waited.results[spawned.subagent_id] == "second"
        instance = control._manager.get(spawned.subagent_id)
        assert instance._agent.stream_calls == 2


@pytest.mark.asyncio
async def test_send_input_on_closed_instance_raises() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)
        await control.close(spawned.subagent_id)

        with pytest.raises(ExecutionError, match="subagent_resume first"):
            await control.send_input(spawned.subagent_id, "too late")


@pytest.mark.asyncio
async def test_send_input_interrupt_redirects_running_turn() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(output="wrong", delay_s=0.2))
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "slow")
        await asyncio.sleep(0.02)

        parent.mock_agent.output = "redirected"
        parent.mock_agent.delay_s = 0.05
        instance = control._manager.get(spawned.subagent_id)
        instance._agent.output = "redirected"
        instance._agent.delay_s = 0.05
        await control.send_input(spawned.subagent_id, "change direction", interrupt=True)
        waited = await control.wait([spawned.subagent_id], timeout_ms=500)

        assert waited.timed_out is False
        assert waited.results[spawned.subagent_id] == "redirected"


@pytest.mark.asyncio
async def test_resume_restores_closed_instance() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)
        await control.close(spawned.subagent_id)

        with patch(
            "openjiuwen.harness.subagent_runtime.control.CheckpointerFactory.get_checkpointer",
        ) as get_checkpointer:
            checkpointer = AsyncMock()
            checkpointer.session_exists = AsyncMock(return_value=True)
            get_checkpointer.return_value = checkpointer

            status = await control.resume(spawned.subagent_id)

        assert status.kind is SubagentStatusKind.PENDING_INIT
        assert control._manager.find(spawned.subagent_id) is not None
        assert spawned.subagent_id not in control._closed_records
        assert control._registry.find_metadata(spawned.subagent_id) is not None


@pytest.mark.asyncio
async def test_resume_missing_closed_record_raises_not_found() -> None:
    async with _patched_control() as control:
        with pytest.raises(AgentError):
            await control.resume("missing-id")


@pytest.mark.asyncio
async def test_resume_no_checkpointer_history_raises_not_found() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent())
    async with _patched_control(parent=parent) as control:
        spawned = await control.spawn("explore", "hello")
        await _wait_for_turn(parent.mock_agent)
        await control.wait([spawned.subagent_id], timeout_ms=500)
        await control.close(spawned.subagent_id)

        with patch(
            "openjiuwen.harness.subagent_runtime.control.CheckpointerFactory.get_checkpointer",
        ) as get_checkpointer:
            checkpointer = AsyncMock()
            checkpointer.session_exists = AsyncMock(return_value=False)
            get_checkpointer.return_value = checkpointer

            with pytest.raises(AgentError):
                await control.resume(spawned.subagent_id)
