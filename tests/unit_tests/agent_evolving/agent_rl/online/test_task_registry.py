from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_evolving.agent_rl.online.task_registry import (
    FinishReason,
    RewardMode,
    TaskConflictError,
    TaskRecord,
    TaskRegistry,
    TaskSpec,
    TaskStatus,
    TurnClosedError,
)
from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis


@pytest.mark.parametrize("task_id", ["task:invalid", "t" * 65])
def test_task_spec_rejects_unsafe_redis_key_components(task_id: str) -> None:
    with pytest.raises(ValueError, match="rl_task_id"):
        TaskSpec(task_id, "session-1", "model-1", "base", RewardMode.TERMINAL)


def test_task_record_preserves_public_constructor_compatibility() -> None:
    record = TaskRecord(
        "task-1",
        "session-1",
        "model-1",
        "base",
        RewardMode.TERMINAL,
        TaskStatus.ACTIVE,
        "2026-01-01T00:00:00+00:00",
    )

    assert record.policy_model == "base"


@pytest.mark.asyncio
async def test_start_is_atomic_and_idempotent_per_active_session() -> None:
    redis = InMemoryRedis()
    first = TaskRegistry(redis=redis)
    second = TaskRegistry(redis=redis)
    specs = [
        TaskSpec(
            rl_task_id=f"task-{index}",
            agent_session_id="session-1",
            model_id="model-1",
            policy_lora_name="model-1:v3",
            reward_mode=RewardMode.TERMINAL,
        )
        for index in range(8)
    ]

    results = await asyncio.gather(*[(first if index % 2 else second).start(spec) for index, spec in enumerate(specs)])

    assert sum(result.created for result in results) == 1
    assert {result.task.rl_task_id for result in results} == {results[0].task.rl_task_id}
    assert results[0].task.status is TaskStatus.ACTIVE
    assert await first.get_active("session-1") == results[0].task


@pytest.mark.asyncio
async def test_terminal_transitions_are_idempotent_and_release_session() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "base", RewardMode.TERMINAL),
    )

    finalized = await registry.finalize("task-1", FinishReason.USER_STOPPED)

    assert finalized.status is TaskStatus.FINALIZED
    assert finalized.finish_reason is FinishReason.USER_STOPPED
    assert finalized.finished_at is not None
    assert await registry.finalize("task-1", FinishReason.SERVICE_STOPPED) == finalized
    assert await registry.abort("task-1", FinishReason.CAPTURE_FAILED) == finalized
    assert await registry.get_active("session-1") is None

    replacement = await registry.start(
        TaskSpec("task-2", "session-1", "model-1", "base", RewardMode.DELAYED_FEEDBACK),
    )
    aborted = await registry.abort("task-2", FinishReason.TIMEOUT)
    assert replacement.created is True
    assert aborted.status is TaskStatus.ABORTED
    assert await registry.abort("task-2", FinishReason.SERVICE_RESTARTED) == aborted
    assert await registry.finalize("task-2", FinishReason.USER_STOPPED) == aborted


@pytest.mark.asyncio
async def test_terminal_transition_rejects_unknown_task() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())

    with pytest.raises(TaskConflictError, match="unknown RL Task"):
        await registry.finalize("missing", FinishReason.USER_STOPPED)


@pytest.mark.asyncio
async def test_turn_publish_claim_prevents_new_capture() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "base", RewardMode.DELAYED_FEEDBACK),
    )
    await registry._begin_capture("task-1", "capture-1", "turn-1", "request-1")
    await registry._discard_capture("task-1", "capture-1", "turn-1")

    assert await registry._claim_turn_publish("task-1", "turn-1") == "claimed"
    with pytest.raises(TurnClosedError, match="closing"):
        await registry._begin_capture("task-1", "capture-2", "turn-1", "request-2")


@pytest.mark.asyncio
async def test_recover_active_aborts_only_indexed_active_tasks() -> None:
    redis = InMemoryRedis()
    previous = TaskRegistry(redis=redis)
    await previous.start(TaskSpec("task-1", "session-1", "model-1", "base", RewardMode.TERMINAL))
    await previous.start(TaskSpec("task-2", "session-2", "model-1", "base", RewardMode.TERMINAL))
    await previous.finalize("task-2", FinishReason.USER_STOPPED)

    recovered = await TaskRegistry(redis=redis).recover_active()

    assert [task.rl_task_id for task in recovered] == ["task-1"]
    assert recovered[0].status is TaskStatus.ABORTED
    assert recovered[0].finish_reason is FinishReason.SERVICE_RESTARTED
