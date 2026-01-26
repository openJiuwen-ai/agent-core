# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Task scheduler module.

This module implements the core logic for scheduling and executing tasks:

- TaskExecutorRegistry: registry of task executors for different task types.
- TaskScheduler: task scheduler responsible for scheduling, executing,
  pausing, and canceling tasks.

Core workflow:
1. Receive tasks to be executed (status ``submitted``).
2. Look up the corresponding ``TaskExecutor`` from the registry by task type.
3. Execute multiple tasks concurrently.
4. Stream outputs produced during task execution.
5. Update task status based on output type (completion / interaction / failed).
"""
import asyncio
from typing import Callable, Dict, Optional

from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules.event_queue import EventQueue
from openjiuwen.core.controller.modules.task_executor import TaskExecutor
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.session import Session
from openjiuwen.core.single_agent.agent import AbilityManager


class TaskExecutorRegistry:
    """Registry for task executors.

    Manages executors for different task types, supporting dynamic
    registration and lookup. Executors are located via the string
    ``task_type`` and a corresponding factory function.
    """
    
    def __init__(self):
        """Initialize an empty task executor registry."""
        self.task_executor_builders: Dict[
            str,
            Callable[[ControllerConfig, AbilityManager, ContextEngine, TaskManager, EventQueue], TaskExecutor]
        ] = {}

    def add_task_executor(
            self,
            task_type: str,
            task_executor_builder: Callable[
                [ControllerConfig, AbilityManager, ContextEngine, TaskManager, EventQueue], TaskExecutor
            ]
    ):
        """Register a task executor factory.

        Args:
            task_type: Logical identifier for the task type.
            task_executor_builder: Factory function that accepts configuration
                and dependencies and returns a ``TaskExecutor`` instance.
        """
        self.task_executor_builders[task_type] = task_executor_builder

    def remove_task_executor(self, task_type: str):
        """Unregister a task executor factory.

        Args:
            task_type: Logical identifier for the task type.
        """
        if task_type in self.task_executor_builders:
            del self.task_executor_builders[task_type]

    def get_task_executor(
            self,
            task_executor_info
    ) -> TaskExecutor:
        """Build a concrete task executor instance.

        Args:
            task_executor_info: All information required to build an executor.

        Returns:
            TaskExecutor: A task executor instance for the given task type.

        Raises:
            KeyError: If the task type has not been registered.
        """
        executor_builder = self.task_executor_builders.get(task_executor_info.task_type, None)
        return executor_builder(
            task_executor_info.config,
            task_executor_info.ability_manager,
            task_executor_info.context_engine,
            task_executor_info.task_manager,
            task_executor_info.event_queue
        )


class TaskScheduler:
    """Task scheduler.

    Responsible for scheduling, executing, pausing, and canceling tasks.
    Supports concurrent execution of multiple tasks and streaming of
    intermediate outputs.

    Workflow:
        1. Periodically scan for tasks to run (status ``submitted``).
        2. Execute multiple tasks concurrently.
        3. Stream outputs produced during task execution.
        4. Update task status based on the type of output.

    Attributes:
        _sessions: Mapping of ``session_id`` to ``Session``.
        _running_tasks: Mapping of ``task_id`` to ``(TaskExecutor, asyncio.Task)``.
        _running: Whether the scheduler is currently running.
        _scheduler_task: Background task for the scheduler loop.
        _lock: Asyncio lock to synchronize access to internal state.
    """
    
    def __init__(
            self,
            config: ControllerConfig,
            task_manager: TaskManager,
            context_engine: ContextEngine,
            ability_manager: AbilityManager,
            event_queue: EventQueue
    ):
        """Initialize the task scheduler.

        Args:
            config: Controller configuration.
            task_manager: Task manager.
            context_engine: Context engine.
            ability_manager: Ability manager.
            event_queue: Event queue used to publish task events.
        """
        self._config = config
        self._task_manager = task_manager
        self._context_engine = context_engine
        self._ability_manager = ability_manager
        self._event_queue = event_queue
        self._task_executor_registry = TaskExecutorRegistry()
        self._sessions: Dict[str, Session] = {}

        # 调度器运行状态
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None

        # 正在执行的任务：task_id -> (TaskExecutor, asyncio.Task)
        self._running_tasks: Dict[str, tuple[TaskExecutor, asyncio.Task]] = {}

        # 用于同步访问的锁
        self._lock = asyncio.Lock()

    @property
    def sessions(self) -> Dict[str, Session]:
        """Return the session mapping."""
        return self._sessions

    @property
    def task_executor_registry(self):
        """Return the task executor registry."""
        return self._task_executor_registry

    async def pause_task(self, task_id: str):
        """Pause a running task.

        Workflow:
            1. Check whether the task is currently running. If not, return.
            2. Call ``executor.can_pause``. If it cannot be paused, return
               the reason; otherwise continue.
            3. Call ``executor.pause`` and then cancel the underlying
               ``asyncio.Task``.
            4. Update the task status to ``paused`` in ``TaskManager``.

        Args:
            task_id: Task identifier.
        """
        ...

    async def cancel_task(self, task_id: str):
        """Cancel a running task.

        Workflow:
            1. Check whether the task is currently running. If not, return.
            2. Call ``executor.can_cancel``. If it cannot be canceled, return
               the reason; otherwise continue.
            3. Call ``executor.cancel`` and then cancel the underlying
               ``asyncio.Task``.
            4. Update the task status to ``cancelled`` in ``TaskManager``.

        Args:
            task_id: Task identifier.
        """
        ...

    async def schedule(self):
        """Main scheduling loop.

        Periodically scans and executes tasks that are ready to run.

        Workflow:
            1. Fetch all tasks with ``status=submitted`` whose ``session_id``
               exists in ``sessions``.
            2. If no tasks are found, return.
            3. Execute all fetched tasks concurrently and stream outputs.
            4. When an output with type ``completion``, ``interaction`` or
               ``failed`` is observed, stop the task (cancel the
               ``asyncio.Task``) and update the corresponding status in
               ``TaskManager``.
        """
        ...

    def start(self):
        """Start the scheduler.

        Launch the background scheduling task and begin scanning for tasks.
        """
        self._running = True
        # 其他启动逻辑
        ...

    def stop(self):
        """Stop the scheduler.

        Stop the background scheduling task and stop executing tasks.
        """
        self._running = False
        # 其他停止逻辑
        ...