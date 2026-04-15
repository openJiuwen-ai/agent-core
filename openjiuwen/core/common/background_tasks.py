# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import sys
from typing import TYPE_CHECKING, Any, Coroutine, Optional

import anyio

if TYPE_CHECKING:
    from openjiuwen.core.common.task_manager.task import Task


def _get_loaded_task_group():
    context_module = sys.modules.get("openjiuwen.core.common.task_manager.context")
    if context_module is None:
        return None
    return context_module.get_task_group()


def _get_loaded_create_task():
    manager_module = sys.modules.get("openjiuwen.core.common.task_manager.manager")
    if manager_module is None:
        return None
    return manager_module.create_task


class BackgroundTask:
    """Handle for background tasks created through task_manager when possible."""

    def __init__(self, *, group: str):
        self._group = group
        self._manager_task: Optional["Task"] = None
        self._asyncio_task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()

    @classmethod
    def from_asyncio_task(cls, task: asyncio.Task, *, group: str) -> "BackgroundTask":
        handle = cls(group=group)
        handle._asyncio_task = task
        handle._ready.set()
        return handle

    def set_manager_task(self, task: "Task") -> None:
        self._manager_task = task
        self._ready.set()

    @property
    def group(self) -> str:
        return self._group

    def done(self) -> bool:
        if self._manager_task is not None:
            return self._manager_task.is_terminal
        if self._asyncio_task is not None:
            return self._asyncio_task.done()
        return False

    async def wait(self) -> Any:
        await self._ready.wait()
        if self._manager_task is not None:
            return await self._manager_task.wait()
        return await self._asyncio_task

    async def cancel(self, *, reason: str = "background_task_cancelled", timeout: float = 1.0) -> None:
        await self._ready.wait()
        if self._manager_task is not None:
            await self._manager_task.cancel(reason=reason)
            with anyio.move_on_after(timeout):
                await self._manager_task.wait()
            return

        if self._asyncio_task is None:
            return
        if not self._asyncio_task.done():
            self._asyncio_task.cancel()
        try:
            with anyio.move_on_after(timeout):
                await self._asyncio_task
        except asyncio.CancelledError:
            pass


async def create_background_task(
    coro: Coroutine,
    *,
    name: str,
    group: str,
    fallback_to_asyncio: bool = True,
) -> BackgroundTask:
    """Create a background task via task_manager when a task group is active."""
    if _get_loaded_task_group() is not None:
        create_task = _get_loaded_create_task()
        if create_task is not None:
            task = await create_task(coro, name=name, group=group, catch_exceptions=True)
            handle = BackgroundTask(group=group)
            handle.set_manager_task(task)
            return handle
    if not fallback_to_asyncio:
        raise RuntimeError("task manager root task group is not available")
    return BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)


def start_background_task(
    coro: Coroutine,
    *,
    name: str,
    group: str,
    fallback_to_asyncio: bool = True,
) -> BackgroundTask:
    """Start a background task from synchronous lifecycle methods."""
    tg = _get_loaded_task_group()
    if tg is None:
        if not fallback_to_asyncio:
            raise RuntimeError("task manager root task group is not available")
        return BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)

    create_task = _get_loaded_create_task()
    if create_task is None:
        if not fallback_to_asyncio:
            raise RuntimeError("task manager root task group is active but manager is not loaded")
        return BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)

    handle = BackgroundTask(group=group)

    async def _create() -> None:
        task = await create_task(coro, name=name, group=group, catch_exceptions=True)
        handle.set_manager_task(task)

    tg.start_soon(_create)
    return handle
