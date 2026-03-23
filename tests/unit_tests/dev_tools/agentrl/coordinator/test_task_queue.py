# -*- coding: utf-8 -*-
"""Unit tests for TaskQueue: enqueue, get, add_rollout, get_rollouts, is_finished, clear, delete_task."""

import asyncio
import pytest

from openjiuwen.dev_tools.agentrl.coordinator.task_queue import TaskQueue
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask, RolloutMessage, Rollout


@pytest.fixture
def queue():
    return TaskQueue()


@pytest.fixture
def sample_task():
    return RLTask(
        task_id="task-1",
        origin_task_id="origin-1",
        task_sample={"prompt": "hello"},
        round_num=0,
    )


@pytest.fixture
def sample_rollout_message(sample_task):
    return RolloutMessage(
        task_id=sample_task.task_id,
        origin_task_id=sample_task.origin_task_id,
        rollout_id="rollout-1",
        rollout_info=[Rollout(turn_id=0, input_prompt={"message": []}, output_response={})],
        reward_list=[0.5],
        global_reward=0.5,
        turn_count=1,
        round_num=0,
    )


@pytest.mark.asyncio
async def test_queue_task_get_task_add_rollout_get_rollouts_full_flow(
    queue, sample_task, sample_rollout_message
):
    """Normal order: queue_task -> get_task -> add_rollout -> get_rollouts."""
    tid = await queue.queue_task(sample_task)
    assert tid == sample_task.task_id

    t = await queue.get_task()
    assert t is not None
    assert t.task_id == sample_task.task_id
    assert not queue.is_finished()

    rid = await queue.add_rollout(sample_rollout_message)
    assert rid == sample_rollout_message.rollout_id

    rollouts = await queue.get_rollouts()
    assert rollouts == {sample_rollout_message.rollout_id: sample_rollout_message}
    assert queue.is_finished()

    rollouts_again = await queue.get_rollouts()
    assert rollouts_again == {}


@pytest.mark.asyncio
async def test_get_task_empty_returns_none(queue):
    """Queue empty: get_task returns None, no error."""
    assert await queue.get_task() is None
    assert queue.is_finished()


@pytest.mark.asyncio
async def test_is_finished_false_when_task_in_processing(queue, sample_task):
    """is_finished() is False when task has been get_task() but not yet add_rollout."""
    await queue.queue_task(sample_task)
    assert queue.is_finished() is False
    t = await queue.get_task()
    assert t is not None
    assert queue.is_finished() is False
    await queue.add_rollout(
        RolloutMessage(
            task_id=sample_task.task_id,
            origin_task_id=sample_task.origin_task_id,
            rollout_id="r1",
            rollout_info=[],
            reward_list=[],
            turn_count=0,
            round_num=0,
        )
    )
    assert queue.is_finished() is True


@pytest.mark.asyncio
async def test_clear_resets_all_state(queue, sample_task, sample_rollout_message):
    """clear() empties queue, in_processing, rollouts; get_rollouts empty, get_task None, is_finished True."""
    await queue.queue_task(sample_task)
    t = await queue.get_task()
    await queue.add_rollout(sample_rollout_message)

    queue.clear()

    assert await queue.get_rollouts() == {}
    assert await queue.get_task() is None
    assert queue.is_finished()


@pytest.mark.asyncio
async def test_delete_task_removes_from_in_processing(queue, sample_task):
    """After get_task, delete_task removes task from in_processing."""
    await queue.queue_task(sample_task)
    t = await queue.get_task()
    assert t is not None
    await queue.delete_task(t)
    assert queue.is_finished()


@pytest.mark.asyncio
async def test_concurrent_queue_get_add_rollouts_no_duplicate_or_loss(queue):
    """Concurrent queue_task, get_task, add_rollout, get_rollouts: all rollouts present, no duplicate."""
    tasks = [
        RLTask(task_id=f"t-{i}", origin_task_id=f"o-{i}", task_sample={}, round_num=0)
        for i in range(10)
    ]
    for t in tasks:
        await queue.queue_task(t)

    async def worker():
        while True:
            t = await queue.get_task()
            if t is None:
                await asyncio.sleep(0.01)
                if queue.is_finished():
                    break
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

    workers = [asyncio.create_task(worker()) for _ in range(3)]
    await asyncio.gather(*workers)

    rollouts = await queue.get_rollouts()
    assert len(rollouts) == 10
    assert set(rollouts.keys()) == {t.task_id for t in tasks}
