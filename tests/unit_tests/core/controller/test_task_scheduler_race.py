"""Regression tests for task scheduler cancellation races."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.controller.base import ControllerConfig
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.modules.task_scheduler import (
    TaskExecutor,
    TaskExecutorDependencies,
    TaskScheduler,
)
from openjiuwen.core.controller.schema import Task, TaskStatus


class _NeverStartedExecutor(TaskExecutor):
    async def execute_ability(self, task_id, session):
        raise AssertionError("cancelled tasks must not execute")
        yield

    async def can_pause(self, task_id, session):
        return True, ""

    async def pause(self, task_id, session):
        return True

    async def can_cancel(self, task_id, session):
        return True, ""

    async def cancel(self, task_id, session):
        return True


class _CancelAfterReadTaskManager(TaskManager):
    """Cancel a task after returning the scheduler's stale snapshot."""

    def __init__(self):
        super().__init__(config={})
        self._cancel_after_read = True

    async def get_task(self, task_filter=None):
        tasks = await super().get_task(task_filter)
        if self._cancel_after_read and tasks:
            self._cancel_after_read = False
            await super().update_task_status(tasks[0].task_id, TaskStatus.CANCELED)
        return tasks


@pytest.mark.asyncio
async def test_execute_task_skips_task_cancelled_after_snapshot():
    task_manager = _CancelAfterReadTaskManager()
    task = Task(
        session_id="session-1",
        task_id="task-1",
        task_type="test",
        status=TaskStatus.SUBMITTED,
    )
    await task_manager.add_task(task)

    scheduler = TaskScheduler(
        config=ControllerConfig(),
        task_manager=task_manager,
        context_engine=MagicMock(),
        ability_manager=MagicMock(),
        event_queue=MagicMock(),
        card=MagicMock(),
    )
    executor = _NeverStartedExecutor(
        TaskExecutorDependencies(
            config=ControllerConfig(),
            ability_manager=MagicMock(),
            context_engine=MagicMock(),
            task_manager=task_manager,
            event_queue=MagicMock(),
        )
    )
    scheduler.task_executor_registry.add_task_executor("test", lambda _: executor)

    await scheduler.execute_task("task-1", MagicMock())

    stored_task = (await task_manager.get_task())[0]
    assert stored_task.status is TaskStatus.CANCELED
