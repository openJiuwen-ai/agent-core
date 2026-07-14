# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import pathlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable, Literal

from filelock import AsyncReadWriteLock, Timeout as FileLockTimeout


class HybridAsyncReadWriteLock:
    """Combine process-local task coordination with a cross-process file lock."""

    _poll_interval = 0.05

    def __init__(self, lock_file: pathlib.Path):
        self._lock_file = lock_file
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openjiuwen-rwlock")
        self._file = AsyncReadWriteLock(lock_file, is_singleton=True, executor=self._executor)
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0
        self._closed = False

    @property
    def file_lock(self) -> AsyncReadWriteLock:
        return self._file

    def _timeout(self) -> FileLockTimeout:
        return FileLockTimeout(str(self._lock_file))

    async def _wait_for(self, predicate: Callable[[], bool], deadline: float) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise self._timeout()
        try:
            await asyncio.wait_for(self._condition.wait_for(predicate), timeout=remaining)
        except asyncio.TimeoutError:
            raise self._timeout() from None

    async def _acquire_file(self, mode: Literal["read", "write"], deadline: float) -> None:
        """Poll without a long-running executor call that could outlive cancellation."""
        acquire = self._file.acquire_read if mode == "read" else self._file.acquire_write
        while True:
            task = asyncio.create_task(acquire(timeout=0, blocking=False))
            try:
                await asyncio.shield(task)
                return
            except asyncio.CancelledError:
                try:
                    await task
                except FileLockTimeout:
                    pass
                else:
                    await self._file.release()
                raise
            except FileLockTimeout:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise self._timeout() from None
                await asyncio.sleep(min(self._poll_interval, remaining))

    async def _release_file(self) -> None:
        task = asyncio.create_task(self._file.release())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    @asynccontextmanager
    async def read(self, timeout: float):
        deadline = asyncio.get_running_loop().time() + timeout
        async with self._condition:
            await self._wait_for(lambda: not self._writer and self._writers_waiting == 0, deadline)
            if self._readers == 0:
                await self._acquire_file("read", deadline)
            self._readers += 1

        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    await self._release_file()
                self._condition.notify_all()

    @asynccontextmanager
    async def write(self, timeout: float):
        deadline = asyncio.get_running_loop().time() + timeout
        async with self._condition:
            self._writers_waiting += 1
            try:
                await self._wait_for(lambda: not self._writer and self._readers == 0, deadline)
            finally:
                self._writers_waiting -= 1
            await self._acquire_file("write", deadline)
            self._writer = True

        try:
            yield
        finally:
            async with self._condition:
                await self._release_file()
                self._writer = False
                self._condition.notify_all()

    async def close(self) -> None:
        async with self._condition:
            if self._readers or self._writer:
                raise RuntimeError("Cannot close an active file lock")
            if self._closed:
                return
            self._closed = True
        try:
            await self._file.close()
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)
