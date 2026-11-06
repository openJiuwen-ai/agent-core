# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional host launch adapter, scoped to the actual tool execution task."""

from __future__ import annotations

from asyncio.subprocess import Process
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ProcessFactory = Callable[..., Awaitable[Process]]
LaunchAdapter = Callable[..., Awaitable[Process]]
_adapter: ContextVar[LaunchAdapter | None] = ContextVar("local_process_launch_adapter", default=None)


@contextmanager
def bind_process_launcher(adapter: LaunchAdapter) -> Iterator[None]:
    """Bind in tool.invoke/stream, where tool identity is explicitly available."""
    token = _adapter.set(adapter)
    try:
        yield
    finally:
        _adapter.reset(token)


async def create_process(factory: ProcessFactory, *args: Any, **kwargs: Any) -> Process:
    """Preserve the normal subprocess path unless a host adapter is bound."""
    adapter = _adapter.get()
    if adapter is None:
        return await factory(*args, **kwargs)
    return await adapter(factory, *args, **kwargs)
