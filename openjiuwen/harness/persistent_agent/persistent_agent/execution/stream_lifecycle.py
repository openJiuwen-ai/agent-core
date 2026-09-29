# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Own native stream producers without replacing their model/tool loop."""

import asyncio
from contextlib import asynccontextmanager


class NativeStreamTaskLifecycle:
    """One construction binding's live producers, never a global task registry."""

    def __init__(self):
        """Initialize task sets for producers, cancellation, and finalization."""
        self._tasks: set[asyncio.Task] = set()
        self._cancel_requested: set[asyncio.Task] = set()
        self._finalizing: set[asyncio.Task] = set()

    def begin_finalization(self, task):
        """Mark a producer that has entered its own finalization path."""
        if task in self._tasks:
            self._finalizing.add(task)

    def _request_cancel(self, task):
        """Cancel a live producer unless it is already finalizing."""
        if not task.done() and task not in self._cancel_requested and task not in self._finalizing:
            self._cancel_requested.add(task)
            task.cancel()

    @staticmethod
    async def _wait(task):
        """Wait through repeated caller cancellation and surface task errors."""
        # A second consumer/control cancellation must not interrupt the native
        # producer's cancellation checkpoint or Session close/commit.
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    interrupted = True
        if not task.cancelled():
            task.result()
        if interrupted:
            raise asyncio.CancelledError

    @asynccontextmanager
    async def scope(self, task):
        """Track a producer for the duration of its consuming stream scope."""
        self._tasks.add(task)
        try:
            yield
        finally:
            self._request_cancel(task)
            try:
                await self._wait(task)
            finally:
                if task.done():
                    self._tasks.discard(task)
                    self._cancel_requested.discard(task)
                    self._finalizing.discard(task)

    async def cancel_and_wait(self):
        """Request cancellation and drain every producer owned by this binding."""
        tasks = tuple(self._tasks)
        if asyncio.current_task() in tasks:
            raise RuntimeError("stream producer cannot await its own shutdown")
        for task in tasks:
            self._request_cancel(task)
        for task in tasks:
            try:
                await self._wait(task)
            finally:
                if task.done():
                    self._tasks.discard(task)
                    self._cancel_requested.discard(task)
                    self._finalizing.discard(task)
