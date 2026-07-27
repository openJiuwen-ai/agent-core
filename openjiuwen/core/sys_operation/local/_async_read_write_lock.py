# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import pathlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable, Literal

from filelock import AsyncReadWriteLock, ReadWriteLock, Timeout as FileLockTimeout


class _ManagedReadWriteLock(ReadWriteLock):
    """Expose lifecycle management for filelock's singleton registry."""

    def _configure_and_begin(
            self,
            mode: Literal["read", "write"],
            timeout: float,
            *,
            blocking: bool,
            start_time: float,
    ) -> None:
        try:
            super()._configure_and_begin(mode, timeout, blocking=blocking, start_time=start_time)
        except sqlite3.OperationalError as exc:
            if mode != "read" or "no such table: sqlite_schema" not in str(exc).lower():
                raise
            self._con.execute("SELECT name FROM sqlite_master LIMIT 1;").close()

    @classmethod
    def evict_singleton(cls, lock_file: str, expected: ReadWriteLock) -> None:
        normalized_path = pathlib.Path(lock_file).resolve()
        with cls._instances_lock:
            if cls._instances.get(normalized_path) is expected:
                cls._instances.pop(normalized_path, None)


class _ManagedAsyncReadWriteLock(AsyncReadWriteLock):
    """Add an explicit singleton-eviction operation to the async adapter."""

    def evict_singleton(self) -> None:
        _ManagedReadWriteLock.evict_singleton(self.lock_file, self._lock)


class HybridAsyncReadWriteLock:
    """Combine process-local task coordination with a cross-process file lock."""

    _poll_interval = 0.05

    def __init__(self, lock_file: pathlib.Path, *, is_singleton: bool = True):
        self._lock_file = lock_file
        self._is_singleton = is_singleton
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openjiuwen-rwlock")
        self._file = _ManagedAsyncReadWriteLock(lock_file, is_singleton=is_singleton, executor=self._executor)
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

    def evict_singleton(self) -> None:
        """Detach this lock from filelock's process-wide singleton registry."""
        if not self._is_singleton:
            return

        self._file.evict_singleton()

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
