# -*- coding: utf-8 -*-
"""Unit tests for ParallelRuntimeExecutor (start/stop/is_running, setters, worker loop)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor import ParallelRuntimeExecutor
from openjiuwen.dev_tools.agentrl.coordinator.task_queue import TaskQueue
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask, RolloutMessage


@pytest.fixture
def rl_task_queue():
    return TaskQueue()


@pytest.fixture
def rl_executor(rl_task_queue):
    return ParallelRuntimeExecutor(data_store=rl_task_queue, num_workers=1)


@pytest.mark.asyncio
async def test_start_then_stop_is_running_flip(rl_executor):
    assert rl_executor.is_running() is False
    await rl_executor.start()
    assert rl_executor.is_running() is True
    await rl_executor.stop()
    assert rl_executor.is_running() is False


@pytest.mark.asyncio
async def test_setters_affect_execution(rl_task_queue):
    """Set task_runner; enqueue one task; start executor; task_runner is called and result is added to store."""
    rl_task = RLTask(task_id="tid1", origin_task_id="oid1", task_sample={}, round_num=0)
    rollout_msg = RolloutMessage(
        task_id=rl_task.task_id,
        origin_task_id=rl_task.origin_task_id,
        rollout_id="rid1",
        rollout_info=[],
        reward_list=[],
        turn_count=0,
        round_num=0,
    )
    mock_task_runner = AsyncMock(return_value=rollout_msg)
    rl_executor_with_runner = ParallelRuntimeExecutor(
        data_store=rl_task_queue, num_workers=1, task_runner=mock_task_runner
    )
    await rl_task_queue.queue_task(rl_task)
    await rl_executor_with_runner.start()
    await asyncio.sleep(0.3)
    await rl_executor_with_runner.stop()
    collected_rollouts = await rl_task_queue.get_rollouts()
    assert len(collected_rollouts) >= 1
    assert mock_task_runner.called
    assert any(
        r.rollout_id == "rid1" or r.task_id == "tid1" for r in collected_rollouts.values()
    )
