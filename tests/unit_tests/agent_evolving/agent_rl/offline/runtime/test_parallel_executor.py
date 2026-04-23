# -*- coding: utf-8 -*-
"""Unit tests for ParallelRuntimeExecutor (start/stop/is_running, setters, worker loop)."""

import asyncio

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor import ParallelRuntimeExecutor
from openjiuwen.agent_evolving.agent_rl.offline.coordinator.task_queue import TaskQueue
from openjiuwen.agent_evolving.agent_rl.schemas import RLTask


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
    rl_task = RLTask(task_id="tid1", origin_task_id="oid1", task_sample={}, round_num=0)
    rl_executor_with_runner = ParallelRuntimeExecutor(data_store=rl_task_queue, num_workers=1)
    await rl_task_queue.queue_task(rl_task)
    await rl_executor_with_runner.start()
    await asyncio.sleep(0.3)
    await rl_executor_with_runner.stop()
    collected_rollouts = await rl_task_queue.get_rollouts()
    assert len(collected_rollouts) >= 1
    assert any(r.task_id == "tid1" for r in collected_rollouts.values())
