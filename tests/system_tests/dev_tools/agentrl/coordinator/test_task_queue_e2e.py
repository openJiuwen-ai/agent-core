# -*- coding: utf-8 -*-
"""System tests for TaskQueue: full queue_task -> get_task -> add_rollout -> get_rollouts flow."""

import pytest

from openjiuwen.dev_tools.agentrl.coordinator.task_queue import TaskQueue
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask, RolloutMessage, Rollout


@pytest.fixture
def queue():
    return TaskQueue()


@pytest.fixture
def sample_task():
    return RLTask(
        task_id="task-e2e-1",
        origin_task_id="origin-e2e-1",
        task_sample={"prompt": "calculate 1+1"},
        round_num=0,
    )


@pytest.fixture
def sample_rollout(sample_task):
    return RolloutMessage(
        task_id=sample_task.task_id,
        origin_task_id=sample_task.origin_task_id,
        rollout_id="rollout-e2e-1",
        rollout_info=[
            Rollout(turn_id=0, input_prompt={"message": []}, output_response={})
        ],
        reward_list=[0.8],
        global_reward=0.8,
        turn_count=1,
        round_num=0,
    )


@pytest.mark.asyncio
async def test_task_queue_e2e_full_flow(queue, sample_task, sample_rollout):
    """E2E: queue_task -> get_task -> add_rollout -> get_rollouts."""
    tid = await queue.queue_task(sample_task)
    assert tid == sample_task.task_id

    t = await queue.get_task()
    assert t is not None
    assert t.task_id == sample_task.task_id
    assert not queue.is_finished()

    rid = await queue.add_rollout(sample_rollout)
    assert rid == sample_rollout.rollout_id

    rollouts = await queue.get_rollouts()
    assert sample_rollout.rollout_id in rollouts
    assert rollouts[sample_rollout.rollout_id].global_reward == 0.8
    assert queue.is_finished()


@pytest.mark.asyncio
async def test_task_queue_e2e_multiple_tasks(queue):
    """E2E: enqueue multiple tasks, process all, verify all rollouts collected."""
    tasks = [
        RLTask(task_id=f"t{i}", origin_task_id=f"o{i}", task_sample={}, round_num=0)
        for i in range(3)
    ]
    for t in tasks:
        await queue.queue_task(t)

    while not queue.is_finished():
        t = await queue.get_task()
        if t is None:
            continue
        msg = RolloutMessage(
            task_id=t.task_id,
            origin_task_id=t.origin_task_id,
            rollout_id=t.task_id,
            rollout_info=[],
            reward_list=[],
            turn_count=0,
            round_num=0,
        )
        await queue.add_rollout(msg)

    rollouts = await queue.get_rollouts()
    assert len(rollouts) == 3
    assert set(rollouts.keys()) == {t.task_id for t in tasks}
