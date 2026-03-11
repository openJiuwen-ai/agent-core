# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Union, AsyncGenerator
from uuid import uuid4

import anyio

from openjiuwen.core.common.logging import LogEventType, runner_logger as logger

from openjiuwen.core.common.task_manager.context import _current_task_id, get_task_group, set_task_group, \
    reset_task_group
from openjiuwen.core.common.task_manager.exceptions import DuplicateTaskError, TaskNotFoundError
from openjiuwen.core.common.task_manager.registry import TaskRegistry
from openjiuwen.core.common.task_manager.task import Task
from openjiuwen.core.common.task_manager.types import TaskStatus
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import TaskManagerEvents


class TaskManager:
    """Coroutine Task Manager
    Unified manager for all coroutine tasks with structured concurrency.
    Uses WeakValueDictionary for automatic cleanup when tasks are no longer referenced.
    """

    _instance: Optional["TaskManager"] = None

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self.registry = TaskRegistry()
        self._lock = asyncio.Lock()
        self._initialized = True

        self._callback_framework = Runner.callback_framework

        logger.info("CoroutineTaskManager initialized with WeakValueDictionary auto-cleanup",
                    event_type=LogEventType.CORO_MANAGER_INIT,
                    metadata={"auto_cleanup": "WeakValueDictionary"})

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing purposes)"""
        cls._instance = None

    async def create_task(
            self,
            coro: Coroutine,
            *,
            task_id: Optional[str] = None,
            name: Optional[str] = None,
            group: Optional[str] = None,
            timeout: Optional[float] = None,
            metadata: Optional[Dict] = None,
            catch_exceptions: bool = False,
    ) -> Task:
        """Create and register a new coroutine task.

        Examples:
            Create a basic task:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     task = await manager.create_task(some_async_function())
            ...     print(f"Created task: {task.task_id}")

            Create a task with name and group:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     task = await manager.create_task(
            ...         process_data(),
            ...         name="data_processor",
            ...         group="processors"
            ...     )

            Create a task with timeout and metadata:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     task = await manager.create_task(
            ...         fetch_data(),
            ...         name="data_fetcher",
            ...         timeout=30.0,
            ...         metadata={"priority": "high", "retry": 3}
            ...     )

        Args:
            coro: The coroutine to execute as a task
            task_id: Optional custom task ID (auto-generated if not provided)
            name: Optional friendly name for the task
            group: Optional group name for task organization
            timeout: Optional timeout in seconds
            metadata: Optional dict of metadata to attach to the task
            catch_exceptions: Whether to catch exceptions in the task

        Returns:
            The created Task object
        """
        task_id = task_id or str(uuid4())
        tg = get_task_group()

        task = Task(
            task_id=task_id,
            name=name,
            group=group,
            timeout=timeout,
            metadata=metadata or {},
        )

        async with self._lock:
            if self.registry.contains(task_id):
                raise DuplicateTaskError(f"Task {task_id} already exists")

            task.parent_task_id = _current_task_id.get()
            self.registry.add(task)

        # Create callback trigger for events
        async def callback_trigger(task: Task, status: str):
            event_map = {
                "running": TaskManagerEvents.TASK_RUNNING,
                "completed": TaskManagerEvents.TASK_COMPLETED,
                "cancelled": TaskManagerEvents.TASK_CANCELLED,
                "timeout": TaskManagerEvents.TASK_TIMEOUT,
                "failed": TaskManagerEvents.TASK_FAILED,
            }
            event_type = event_map.get(status)
            if event_type:
                await self._trigger_event(event_type, task)

        # Use task.execute() to run the coroutine
        tg.start_soon(task.execute, coro, callback_trigger, catch_exceptions)

        await self._trigger_event(TaskManagerEvents.TASK_CREATED, task)
        logger.debug("Created task", event_type=LogEventType.CORO_MANAGER_TASK_STATUS_CHANGED,
                     metadata={"task_id": task_id, "name": name, "parent": task.parent_task_id,
                               "previous_status": "pending", "current_status": "running"})
        return task

    @asynccontextmanager
    async def task_group(self) -> AsyncGenerator[anyio.abc.TaskGroup, None]:
        """Create and manage a task group context.

        Usage:
            async with manager.task_group() as tg:
                await manager.create_task(coro())

        Yields:
            anyio.abc.TaskGroup: The created task group
        """
        tg = anyio.create_task_group()
        token = set_task_group(tg)
        try:
            async with tg:
                yield tg
        finally:
            reset_task_group(token)

    async def _cascade_cancel(self, parent_id: str, reason: str = "parent_cancelled") -> None:
        children = self.registry.get_by_parent(parent_id)
        for child in children:
            if not child.is_terminal and child.get_cancel_scope():
                child.cancelled_by = parent_id
                # Use abort() for synchronous cancel in cascade (no further cascade)
                child.abort(reason=reason)

                cancel_chain = self._build_cancel_chain(child.task_id)
                logger.debug("Cascade cancelled child task", event_type=LogEventType.CORO_MANAGER_TASK_STATUS_CHANGED,
                             metadata={"task_id": child.task_id, "display_name": child.display_name,
                                       "chain": cancel_chain,
                                       "previous_status": "running", "current_status": "cancelled"})

                await self._cascade_cancel(child.task_id, reason=reason)

    async def cascade_cancel(self, task_id: str, reason: str = "parent_cancelled") -> None:
        """Cancel a task and all its children recursively (cascade).

        Examples:
            Cancel a parent task and all its children:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Assuming parent_task is an existing task with children
            ...     await manager.cascade_cancel(parent_task.task_id, reason="user_requested")

            This will cancel the specified task and recursively cancel all its
            child tasks, building a cancellation chain for debugging.

        Args:
            task_id: The ID of the task to cancel
            reason: The reason for cancellation (default: "parent_cancelled")
        """
        await self._cascade_cancel(task_id, reason=reason)

    async def cancel_group(self, group: str) -> int:
        """Cancel all tasks in a specific group.

        Examples:
            Cancel all tasks in a group:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create tasks with group
            ...     await manager.create_task(coro(), group="my_group")
            ...     await manager.create_task(coro(), group="my_group")
            ...     # Cancel all tasks in the group
            ...     count = await manager.cancel_group("my_group")
            ...     print(f"Cancelled {count} tasks")

        Args:
            group: The group name to cancel

        Returns:
            Number of tasks that were cancelled
        """
        tasks = self.registry.get_by_group(group)
        count = 0
        for task in tasks:
            if await task.cancel(cascade=False):
                count += 1
        logger.info("Cancelled tasks in group", event_type=LogEventType.CORO_MANAGER_TASK_CANCELLED,
                    metadata={"group": group, "count": count})
        return count

    async def cancel_all(self) -> int:
        """Cancel all running tasks managed by this manager.

        Examples:
            Cancel all running tasks:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create several tasks
            ...     await manager.create_task(task1())
            ...     await manager.create_task(task2())
            ...     # Cancel all tasks
            ...     count = await manager.cancel_all()
            ...     print(f"Cancelled {count} tasks")

        Returns:
            Number of tasks that were cancelled
        """
        tasks = self.registry.get_running()
        count = 0
        for task in tasks:
            if await task.cancel(cascade=False):
                count += 1
        logger.info("Cancelled running tasks", event_type=LogEventType.CORO_MANAGER_TASK_CANCELLED,
                    metadata={"count": count})
        return count

    def _build_cancel_chain(self, task_id: str) -> str:
        chain = []
        current_id = task_id
        while current_id:
            task = self.registry.get(current_id)
            if not task:
                break
            chain.append(f"{task.display_name}({current_id[:8]})")
            current_id = task.cancelled_by
        chain.reverse()
        return " -> ".join(chain) if chain else task_id[:8]

    def get_task_tree(self, task_id: str) -> str:
        """Get a tree representation of a task and its descendants.

        Examples:
            Print task tree for debugging:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create a parent task with child tasks
            ...     parent = await manager.create_task(parent_task(), name="parent")
            ...     await manager.create_task(child_task(), name="child1")
            ...     await manager.create_task(child_task(), name="child2")
            ...     # Get tree representation
            ...     tree = manager.get_task_tree(parent.task_id)
            ...     print(tree)

        Args:
            task_id: The ID of the root task

        Returns:
            A string representation of the task tree
        """
        lines: List[str] = []
        self._build_tree_recursive(task_id, lines, 0)
        return "\n".join(lines)

    def _build_tree_recursive(self, task_id: str, lines: List[str], indent: int) -> None:
        task = self.registry.get(task_id)
        if not task:
            return

        prefix = "  " * indent + ("+- " if indent else "")
        status_info = f"[{task.status.value}]"
        if task.cancelled_by:
            cancelled_by_task = self.registry.get(task.cancelled_by)
            cancelled_by_name = cancelled_by_task.display_name if cancelled_by_task else task.cancelled_by[:8]
            status_info += f" (cancelled by: {cancelled_by_name}, reason: {task.cancel_reason})"

        lines.append(f"{prefix}{task.display_name} {status_info}")

        children = self.registry.get_by_parent(task_id)
        for child in children:
            self._build_tree_recursive(child.task_id, lines, indent + 1)

    async def wait_group(self, group: str, timeout: Optional[float] = None) -> List[Any]:
        """Wait for all tasks in a group to complete.

        Examples:
            Wait for all tasks in a group:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create tasks with group
            ...     await manager.create_task(async_add(1, 2), group="math")
            ...     await manager.create_task(async_add(3, 4), group="math")
            ...     # Wait for all tasks in the group to complete
            ...     results = await manager.wait_group("math")
            ...     print(f"Results: {results}")  # [3, 7]

        Args:
            group: The group name to wait for
            timeout: Optional timeout in seconds

        Returns:
            List of results from all tasks in the group
        """
        async with self._lock:
            task_ids = self.registry.get_group_task_ids(group)

        tasks = [self.registry.get(tid) for tid in task_ids if self.registry.contains(tid)]

        results = []
        async with anyio.create_task_group() as tg:
            async def wait_one(task: Task) -> None:
                try:
                    result = await task.wait()
                    results.append(result)
                except Exception:
                    results.append(None)

            for task in tasks:
                tg.start_soon(wait_one, task)

        return results

    async def wait_all(self, timeout: Optional[float] = None) -> List[Any]:
        """Wait for all tasks managed by this manager to complete.

        Examples:
            Wait for all tasks:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create several tasks
            ...     await manager.create_task(async_work(1))
            ...     await manager.create_task(async_work(2))
            ...     # Wait for all tasks to complete
            ...     results = await manager.wait_all()
            ...     print(f"All results: {results}")

        Args:
            timeout: Optional timeout in seconds for all tasks

        Returns:
            List of results from all tasks
        """
        async with self._lock:
            task_ids = list(self.registry.keys())

        results = []
        for task_id in task_ids:
            task = self.registry.get(task_id)
            if not task:
                results.append(None)
                continue
            try:
                if timeout:
                    with anyio.fail_after(timeout):
                        result = await task.wait()
                else:
                    result = await task.wait()
                results.append(result)
            except Exception:
                results.append(None)

        return results

    async def _trigger_event(self, event_type: str, task: Task) -> None:
        """Trigger an event through the callback framework."""
        if self._callback_framework:
            await self._callback_framework.trigger(
                event_type,
                task=task,
                task_id=task.task_id,
                name=task.name,
                group=task.group,
                status=task.status.value if task.status else None,
            )

    async def on(self, event_type: str, callback: Callable) -> None:
        """Register a callback for an event.

        Examples:
            Register callbacks for task events:

            >>> async def on_completed(task):
            ...     print(f"Task {task.name} completed!")
            ...
            >>> async def main():
            ...     manager = get_task_manager()
            ...     await manager.on("task_completed", on_completed)
            ...     await manager.create_task(some_task(), name="my_task")

            Using convenience methods:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     await manager.on_completed(lambda t: print(f"Completed: {t.name}"))
            ...     await manager.on_failed(lambda t: print(f"Failed: {t.name}"))

        Args:
            event_type: Event name string (use TaskManagerEvents constants)
            callback: Callback function to register
        """
        if self._callback_framework:
            self._callback_framework.register_sync(event_type, callback)

    async def on_created(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_CREATED, callback)

    async def on_running(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_RUNNING, callback)

    async def on_completed(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_COMPLETED, callback)

    async def on_failed(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_FAILED, callback)

    async def on_cancelled(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_CANCELLED, callback)

    async def on_timeout(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        await self.on(TaskManagerEvents.TASK_TIMEOUT, callback)

    async def off(self, event_type: str, callback: Callable) -> None:
        """Unregister a callback from an event.

        Examples:
            Unregister a callback:

            >>> async def on_completed(task):
            ...     print(f"Task completed!")
            ...
            >>> async def main():
            ...     manager = get_task_manager()
            ...     await manager.on("task_completed", on_completed)
            ...     # Later, unregister the callback
            ...     await manager.off("task_completed", on_completed)

        Args:
            event_type: Event name string (use TaskManagerEvents constants)
            callback: Callback function to unregister
        """
        if self._callback_framework:
            await self._callback_framework.unregister(event_type, callback)

    def _remove_task_unsafe(self, task_id: str) -> None:
        self.registry.remove_unsafe(task_id)

    async def remove_task(self, task_id: str) -> bool:
        """Remove a task from the registry.

        Examples:
            Remove a specific task:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     task = await manager.create_task(some_task())
            ...     # Remove the task from registry
            ...     removed = await manager.remove_task(task.task_id)
            ...     print(f"Task removed: {removed}")

        Args:
            task_id: The ID of the task to remove

        Returns:
            True if the task was found and removed, False otherwise
        """
        async with self._lock:
            if not self.registry.contains(task_id):
                return False
            self.registry.remove_unsafe(task_id)
            return True

    async def remove_completed(self) -> int:
        """Remove all completed tasks from the registry.

        Examples:
            Clean up completed tasks:

            >>> async def main():
            ...     manager = get_task_manager()
            ...     # Create and run some tasks
            ...     await manager.create_task(some_task())
            ...     await manager.wait_all()
            ...     # Remove all completed tasks
            ...     count = await manager.remove_completed()
            ...     print(f"Removed {count} completed tasks")

        Returns:
            Number of completed tasks that were removed
        """
        async with self._lock:
            completed = [tid for tid, t in self.registry.items() if t.is_terminal]
            for task_id in completed:
                self.registry.remove_unsafe(task_id)
            return len(completed)


# Module-level convenience functions

def get_task_manager() -> TaskManager:
    """Get the global TaskManager singleton instance.

    Examples:
        Get the singleton instance:

        >>> async def main():
        ...     manager = get_task_manager()
        ...     task = await manager.create_task(async_work())

        Note: This returns the same instance throughout the application,
        making it easy to share task management across modules.

    Returns:
        The global TaskManager singleton instance
    """
    return TaskManager()


async def create_task(
        coro: Coroutine,
        *,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        group: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[Dict] = None,
        catch_exceptions: bool = False,
) -> Task:
    """Create a task using the global TaskManager (convenience function).

    Examples:
        Create a task using the convenience function:

        >>> async def main():
        ...     task = await create_task(
        ...         fetch_data(),
        ...         name="data_fetcher",
        ...         group="io_tasks",
        ...         timeout=30.0
        ...     )

    Args:
        coro: The coroutine to execute as a task
        task_id: Optional custom task ID (auto-generated if not provided)
        name: Optional friendly name for the task
        group: Optional group name for task organization
        timeout: Optional timeout in seconds
        metadata: Optional dict of metadata to attach to the task
        catch_exceptions: Whether to catch exceptions in the task

    Returns:
        The created Task object
    """
    return await get_task_manager().create_task(
        coro,
        task_id=task_id,
        name=name,
        group=group,
        timeout=timeout,
        metadata=metadata,
        catch_exceptions=catch_exceptions,
    )


async def cancel_group(group: str) -> int:
    """Cancel all tasks in a group (convenience function).

    Examples:
        Cancel all tasks in a group:

        >>> async def main():
        ...     await create_task(task1(), group="workers")
        ...     await create_task(task2(), group="workers")
        ...     count = await cancel_group("workers")
        ...     print(f"Cancelled {count} tasks")

    Args:
        group: The group name to cancel

    Returns:
        Number of tasks that were cancelled
    """
    return await get_task_manager().cancel_group(group)


async def cancel_all() -> int:
    """Cancel all running tasks (convenience function).

    Examples:
        Cancel all running tasks:

        >>> async def main():
        ...     await create_task(task1())
        ...     await create_task(task2())
        ...     count = await cancel_all()
        ...     print(f"Cancelled {count} tasks")

    Returns:
        Number of tasks that were cancelled
    """
    return await get_task_manager().cancel_all()


def print_task_tree(task_id: Optional[str] = None) -> None:
    """Print the task tree for debugging (convenience function).

    Examples:
        Print tree for a specific task:

        >>> async def main():
        ...     task = await create_task(parent_task())
        ...     await create_task(child_task(), parent_id=task.task_id)
        ...     print_task_tree(task.task_id)

        Print all task trees:

        >>> async def main():
        ...     # Creates several tasks
        ...     print_task_tree()  # Prints all root tasks and their children

    Args:
        task_id: Optional task ID to print tree for. If None, prints all root tasks.
    """
    manager = get_task_manager()
    if task_id:
        task_tree = manager.get_task_tree(task_id)
        logger.debug("Task tree", event_type=LogEventType.CORO_MANAGER_DEBUG_TASK_TREE,
                     metadata={"task_id": task_id, "tree": task_tree})
    else:
        all_tasks = manager.registry.get_all()
        root_tasks = [t for t in all_tasks if not t.parent_task_id]
        if not root_tasks:
            logger.debug("No tasks found", event_type=LogEventType.CORO_MANAGER_DEBUG_TASK_TREE)
            return
        for task in root_tasks:
            task_tree = manager.get_task_tree(task.task_id)
            logger.debug("Task tree", event_type=LogEventType.CORO_MANAGER_DEBUG_TASK_TREE,
                         metadata={"task_id": task.task_id, "tree": task_tree})
