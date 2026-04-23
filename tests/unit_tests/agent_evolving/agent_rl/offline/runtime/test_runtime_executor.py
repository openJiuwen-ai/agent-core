# -*- coding: utf-8 -*-
"""Unit tests for RuntimeExecutor (offline runtime executor behavior)."""

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor import RuntimeExecutor
from openjiuwen.agent_evolving.agent_rl.schemas import RLTask


@pytest.fixture
def sample_task():
    return RLTask(task_id="t1", origin_task_id="o1", task_sample={}, round_num=0)


@pytest.mark.asyncio
async def test_execute_async_neither_agent_factory_returns_empty_rollout(sample_task):
    executor = RuntimeExecutor()
    result = await executor.execute_async(sample_task)
    assert result.rollout_info == []
    assert result.reward_list == []
    assert result.turn_count == 0
    assert result.task_id == sample_task.task_id
    assert result.origin_task_id == sample_task.origin_task_id


@pytest.mark.asyncio
async def test_execute_async_agent_factory_exception_returns_empty_rollout(sample_task):
    def agent_factory(_: RLTask):
        raise ValueError("fail")

    executor = RuntimeExecutor(agent_factory=agent_factory)
    result = await executor.execute_async(sample_task)
    assert result is not None
    assert result.rollout_info == []
    assert result.reward_list == []
