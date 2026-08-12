# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
ParallelRuntimeExecutor
-----------------------

Parallel rollout execution engine that manages multiple async worker loops,
pulling tasks from a TaskQueue and writing results back.
"""

import asyncio
import multiprocessing as mp
import traceback
from typing import Callable, List, Optional

from openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor import RuntimeExecutor
from openjiuwen.agent_evolving.agent_rl.schemas import RLTask
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.agent_rl.offline.coordinator.task_queue import TaskQueue
from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.setup import (
    get_config,
    init_observability,
    is_initialized,
    shutdown_observability,
)


class ParallelRuntimeExecutor:
    """Parallel rollout engine pulling tasks from a TaskQueue.

    Each worker creates its own RuntimeExecutor and processes tasks
    concurrently until stopped.
    """

    def __init__(
        self,
        data_store: TaskQueue,
        num_workers: int,
        *,
        agent_factory: Optional[Callable] = None,
        task_data_fn: Optional[Callable] = None,
        reward_fn: Optional[Callable] = None,
        observability_config: ObservabilityConfig | None = None,
    ) -> None:
        """Initialize the parallel executor with a task queue and worker count."""
        self.data_store = data_store
        self.num_workers = num_workers or mp.cpu_count()

        self._agent_factory = agent_factory
        self._task_data_fn = task_data_fn
        self._reward_fn = reward_fn
        self._observability_config = observability_config or ObservabilityConfig()

        self._is_running = False
        self._runtime_tasks: List[asyncio.Task] = []
        self._trajectory_span_processor: TrajectorySpanProcessor | None = None
        self._owns_observability_runtime = False

    def _prepare_observability(self) -> None:
        """Register the shared trajectory processor and record runtime ownership."""

        if self._trajectory_span_processor is None:
            self._trajectory_span_processor = TrajectorySpanProcessor()
        self._owns_observability_runtime = not is_initialized()
        config = get_config() or self._observability_config
        try:
            init_observability(
                config,
                additional_span_processors=(self._trajectory_span_processor,),
            )
        except BaseException:
            self._owns_observability_runtime = False
            raise

    async def start(self) -> None:
        """Launch all worker loops."""
        if self._is_running:
            logger.warning("ParallelRuntimeExecutor is already running")
            return
        self._prepare_observability()
        self._is_running = True
        try:
            logger.info("Starting ParallelRuntimeExecutor with %d workers", self.num_workers)
            for i in range(self.num_workers):
                task = asyncio.create_task(self._worker_loop(worker_id=i))
                self._runtime_tasks.append(task)
        except BaseException:
            self._is_running = False
            for task in self._runtime_tasks:
                task.cancel()
            if self._runtime_tasks:
                await asyncio.gather(*self._runtime_tasks, return_exceptions=True)
                self._runtime_tasks.clear()
            if self._owns_observability_runtime:
                shutdown_observability()
                self._owns_observability_runtime = False
            raise

    async def stop(self) -> None:
        """Stop all workers and clean up."""
        self._is_running = False
        try:
            if self._runtime_tasks:
                await asyncio.gather(*self._runtime_tasks, return_exceptions=True)
                self._runtime_tasks.clear()
        finally:
            if self._owns_observability_runtime:
                shutdown_observability()
                self._owns_observability_runtime = False
            logger.info("ParallelRuntimeExecutor stopped")

    def is_running(self) -> bool:
        """Return whether the executor is currently running."""
        return self._is_running

    def set_agent_factory(self, factory: Callable) -> None:
        """Set the agent factory for creating agents per task."""
        self._agent_factory = factory

    def set_task_data_fn(self, fn: Callable) -> None:
        """Set the function to convert task samples to agent inputs."""
        self._task_data_fn = fn

    def set_reward_fn(self, fn: Callable) -> None:
        """Set the reward function to compute rewards from rollout messages."""
        self._reward_fn = fn

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop: pull → execute → push results."""
        logger.debug("Worker %d started", worker_id)
        processor = self._trajectory_span_processor
        if processor is None:
            raise RuntimeError("trajectory span processor is not initialized")
        executor = RuntimeExecutor(
            trajectory_span_processor=processor,
            agent_factory=self._agent_factory,
            task_data_fn=self._task_data_fn,
            reward_fn=self._reward_fn,
        )

        while self._is_running:
            task: Optional[RLTask] = None
            try:
                task = await self.data_store.get_task()
                if task is None:
                    await asyncio.sleep(0.1)
                    continue

                logger.debug("Worker %d START task %s", worker_id, task.task_id)
                rollout_message = await executor.execute_async(task)
                rollout_message.rollout_id = task.task_id
                await self.data_store.add_rollout(rollout_message)
                logger.debug(
                    "Worker %d DONE task %s, reward=%s",
                    worker_id, task.task_id, rollout_message.global_reward,
                )
            except Exception as e:
                traceback.print_exc()
                logger.error(
                    "Worker %d error: %s, deleting task directly.", worker_id, str(e)
                )
                if task is not None:
                    await self.data_store.delete_task(task)
                await asyncio.sleep(1)
