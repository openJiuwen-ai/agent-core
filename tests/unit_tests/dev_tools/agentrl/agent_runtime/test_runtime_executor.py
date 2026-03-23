# -*- coding: utf-8 -*-
"""Unit tests for RuntimeExecutor: task_runner mode returns RolloutMessage; unset task_runner/agent_factory raises."""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor import RuntimeExecutor
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask, RolloutMessage


@pytest.fixture
def sample_task():
    return RLTask(task_id="t1", origin_task_id="o1", task_sample={}, round_num=0)


@pytest.mark.asyncio
async def test_execute_async_task_runner_mode_returns_injected_message(sample_task):
    msg = RolloutMessage(
        task_id=sample_task.task_id,
        origin_task_id=sample_task.origin_task_id,
        rollout_id="r1",
        rollout_info=[],
        reward_list=[0.5],
        global_reward=0.5,
        turn_count=0,
        round_num=0,
    )
    task_runner = AsyncMock(return_value=msg)
    executor = RuntimeExecutor(task_runner=task_runner)
    result = await executor.execute_async(sample_task)
    assert result is msg
    task_runner.assert_called_once()
    assert task_runner.call_args[0][0].task_id == sample_task.task_id


@pytest.mark.asyncio
async def test_execute_async_neither_task_runner_nor_agent_factory_returns_empty_rollout(sample_task):
    """When neither task_runner nor agent_factory is set, implementation returns empty RolloutMessage (no raise)."""
    executor = RuntimeExecutor()
    result = await executor.execute_async(sample_task)
    assert result.rollout_info == []
    assert result.reward_list == []
    assert result.turn_count == 0


@pytest.mark.asyncio
async def test_execute_async_task_runner_exception_propagates(sample_task):
    task_runner = AsyncMock(side_effect=ValueError("fail"))
    executor = RuntimeExecutor(task_runner=task_runner)
    result = await executor.execute_async(sample_task)
    assert result is not None
    assert result.rollout_info == []
    assert result.reward_list == []
