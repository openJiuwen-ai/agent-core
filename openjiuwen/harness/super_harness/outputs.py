# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Output channel for SuperHarness.

The supervisor pushes OutputSchema chunks into ``HarnessInternalState.output_queue``
and pushes ``_END`` when the harness stops. ``_OutputIterator`` wraps the queue
as an AsyncIterator so external consumers never see the raw queue or any
generator.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


# Sentinel marking end-of-stream. Object identity is the comparison key,
# so a plain object() instance is enough — no enum / no string.
_END: Any = object()


class _OutputIterator:
    """Wrap an asyncio.Queue as an AsyncIterator until a sentinel arrives.

    Single-consumer contract: if multiple coroutines iterate concurrently
    they will steal items from each other. Wrap externally if broadcast is
    needed.
    """

    __slots__ = ("_q",)

    def __init__(self, queue: asyncio.Queue) -> None:
        """Initialize the iterator over the given queue."""
        self._q = queue

    def __aiter__(self) -> "AsyncIterator[Any]":
        """Return self; required by the async-iterator protocol."""
        return self

    async def __anext__(self) -> Any:
        """Return the next chunk; raise StopAsyncIteration on sentinel."""
        item = await self._q.get()
        if item is _END:
            raise StopAsyncIteration
        return item
