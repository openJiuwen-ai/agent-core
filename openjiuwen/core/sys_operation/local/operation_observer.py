# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional host checks at the actual local I/O boundary, scoped to a tool task."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

OperationObserver = Callable[[str, Any], Awaitable[bool]]
_observer: ContextVar[OperationObserver | None] = ContextVar("local_operation_observer", default=None)
_observing: ContextVar[bool] = ContextVar("local_operation_observing", default=False)
_owner_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar("local_operation_loop", default=None)


@contextmanager
def bind_operation_observer(observer: OperationObserver | None) -> Iterator[None]:
    """The trusted host supplies tool identity; model arguments cannot bind it."""
    token = _observer.set(observer)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    loop_token = _owner_loop.set(loop)
    try:
        yield
    finally:
        _observer.reset(token)
        _owner_loop.reset(loop_token)


async def before_operation(name: str, arguments: Any) -> None:
    """Finish authorization before I/O. Host reporting traffic is not re-observed."""
    observer = _observer.get()
    if observer is None or _observing.get():
        return
    token = _observing.set(True)
    try:
        if not await observer(name, arguments):
            raise PermissionError(f"Local operation denied: {name}")
    finally:
        _observing.reset(token)


def before_operation_sync(name: str, arguments: Any) -> None:
    """Bridge a synchronous tool worker to its owner's live event loop.

    Hosts should execute blocking tools with ``asyncio.to_thread`` so context
    and cancellation remain bound to the original tool. Never block the event
    loop that must deliver an approval response.
    """
    if _observer.get() is None or _observing.get():
        return
    owner = _owner_loop.get()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError("Run synchronous observed I/O in a worker, or use before_operation")
    if owner is None:
        asyncio.run(before_operation(name, arguments))
    else:
        if owner.is_closed() or not owner.is_running():
            raise PermissionError("The owning tool event loop has stopped")
        asyncio.run_coroutine_threadsafe(before_operation(name, arguments), owner).result()
