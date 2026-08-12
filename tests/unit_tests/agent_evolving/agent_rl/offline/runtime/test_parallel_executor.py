# -*- coding: utf-8 -*-
"""Unit tests for ParallelRuntimeExecutor (start/stop/is_running, setters, worker loop)."""

import asyncio

import pytest

import openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor as parallel_executor_module
from openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor import ParallelRuntimeExecutor
from openjiuwen.agent_evolving.agent_rl.offline.coordinator.task_queue import TaskQueue
from openjiuwen.agent_evolving.agent_rl.schemas import RLTask
from openjiuwen.extensions.observability.config import ObservabilityConfig


@pytest.fixture
def rl_task_queue():
    return TaskQueue()


@pytest.fixture
def rl_executor(rl_task_queue):
    return ParallelRuntimeExecutor(data_store=rl_task_queue, num_workers=1)


@pytest.fixture(autouse=True)
def observability_lifecycle(monkeypatch):
    state = {"initialized": False, "init_calls": [], "shutdown_calls": 0}

    def init(config, *, additional_span_processors=()):
        state["initialized"] = True
        state["init_calls"].append((config, additional_span_processors))

    def shutdown():
        state["initialized"] = False
        state["shutdown_calls"] += 1

    monkeypatch.setattr(parallel_executor_module, "is_initialized", lambda: state["initialized"])
    monkeypatch.setattr(parallel_executor_module, "get_config", lambda: None)
    monkeypatch.setattr(parallel_executor_module, "init_observability", init)
    monkeypatch.setattr(parallel_executor_module, "shutdown_observability", shutdown)
    return state


@pytest.mark.asyncio
async def test_start_then_stop_is_running_flip(rl_executor):
    assert rl_executor.is_running() is False
    await rl_executor.start()
    assert rl_executor.is_running() is True
    await rl_executor.stop()
    assert rl_executor.is_running() is False


@pytest.mark.asyncio
async def test_workers_share_one_processor(rl_task_queue, monkeypatch, observability_lifecycle) -> None:
    processors = []

    class RecordingRuntimeExecutor:
        def __init__(self, *, trajectory_span_processor, **kwargs):
            del kwargs
            processors.append(trajectory_span_processor)

    monkeypatch.setattr(parallel_executor_module, "RuntimeExecutor", RecordingRuntimeExecutor)
    executor = ParallelRuntimeExecutor(data_store=rl_task_queue, num_workers=2)

    await executor.start()
    await executor.stop()

    assert len(processors) == 2
    registered_processor = observability_lifecycle["init_calls"][0][1][0]
    assert processors[0] is processors[1] is registered_processor


@pytest.mark.asyncio
async def test_start_failure_does_not_enter_running_state(rl_executor, monkeypatch) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("observability failed")

    monkeypatch.setattr(parallel_executor_module, "init_observability", fail)

    with pytest.raises(RuntimeError, match="observability failed"):
        await rl_executor.start()

    assert not rl_executor.is_running()


@pytest.mark.asyncio
async def test_stop_closes_only_executor_owned_runtime(rl_task_queue, observability_lifecycle) -> None:
    executor = ParallelRuntimeExecutor(data_store=rl_task_queue, num_workers=1)
    await executor.start()
    await executor.stop()

    assert observability_lifecycle["shutdown_calls"] == 1

    observability_lifecycle["initialized"] = True
    external_executor = ParallelRuntimeExecutor(
        data_store=rl_task_queue,
        num_workers=1,
        observability_config=ObservabilityConfig(service_name="ignored-when-external"),
    )
    await external_executor.start()
    await external_executor.stop()

    assert observability_lifecycle["shutdown_calls"] == 1


@pytest.mark.asyncio
async def test_restart_reuses_processor_without_leaking_ownership(rl_executor, observability_lifecycle) -> None:
    await rl_executor.start()
    processor = observability_lifecycle["init_calls"][-1][1][0]
    await rl_executor.stop()
    await rl_executor.start()
    restarted_processor = observability_lifecycle["init_calls"][-1][1][0]
    await rl_executor.stop()

    assert restarted_processor is processor
    assert observability_lifecycle["shutdown_calls"] == 2


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
