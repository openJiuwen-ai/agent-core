# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import pathlib
import sqlite3
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable, Literal

try:
    from filelock import AsyncReadWriteLock, ReadWriteLock, Timeout as FileLockTimeout
except ImportError:
    from filelock import FileLock, Timeout as FileLockTimeout

    AsyncReadWriteLock = None  # type: ignore[assignment]
    ReadWriteLock = None  # type: ignore[assignment]


if AsyncReadWriteLock is not None and ReadWriteLock is not None:
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
            # ``ReadWriteLock.__init_subclass__`` hands every subclass its own
            # ``_instances`` registry, while the async adapter always constructs
            # the base class. Consulting ``cls._instances`` would therefore look
            # into this subclass's empty registry and never detach anything; the
            # registry that holds *expected* is the one owned by its own class.
            owner = type(expected)
            normalized_path = pathlib.Path(lock_file).resolve()
            with owner._instances_lock:
                if owner._instances.get(normalized_path) is expected:
                    owner._instances.pop(normalized_path, None)


    class _ManagedAsyncReadWriteLock(AsyncReadWriteLock):
        """Add an explicit singleton-eviction operation to the async adapter."""

        def evict_singleton(self) -> None:
            _ManagedReadWriteLock.evict_singleton(self.lock_file, self._lock)
else:
    class _ManagedAsyncReadWriteLock:  # type: ignore[no-redef]
        """Compatibility adapter for filelock versions without ReadWriteLock."""

        def __init__(
            self,
            lock_file: pathlib.Path,
            *,
            is_singleton: bool = True,
            executor: ThreadPoolExecutor | None = None,
        ) -> None:
            del is_singleton
            self.lock_file = str(lock_file)
            self._lock = FileLock(self.lock_file)
            self._executor = executor

        async def acquire_read(self, *, timeout: float = -1, blocking: bool = True) -> None:
            await self._acquire(timeout=timeout, blocking=blocking)

        async def acquire_write(self, *, timeout: float = -1, blocking: bool = True) -> None:
            await self._acquire(timeout=timeout, blocking=blocking)

        async def release(self) -> None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._lock.release)

        async def close(self) -> None:
            return None

        @staticmethod
        def evict_singleton() -> None:
            return None

        async def _acquire(self, *, timeout: float, blocking: bool) -> None:
            loop = asyncio.get_running_loop()
            call = partial(self._lock.acquire, timeout=timeout, blocking=blocking)
            await loop.run_in_executor(self._executor, call)


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
        """Poll with a short non-blocking attempt instead of one long blocking call.

        The attempt has to stay in the calling task: ``filelock`` records read and
        write ownership per asyncio task and rejects a release issued by a task that
        never acquired, so moving the call into a helper task would leave the file
        lock unreleasable. A non-blocking attempt keeps every executor call short
        enough that cancellation cannot leave one running behind.
        """
        acquire = self._file.acquire_read if mode == "read" else self._file.acquire_write
        while True:
            try:
                await acquire(timeout=0, blocking=False)
                return
            except FileLockTimeout:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise self._timeout() from None
                await asyncio.sleep(min(self._poll_interval, remaining))

    async def _release_file(self) -> None:
        # Released from the acquiring task for the ownership reason documented on
        # ``_acquire_file``.
        await self._file.release()

    @asynccontextmanager
    async def read(self, timeout: float):
        deadline = asyncio.get_running_loop().time() + timeout
        async with self._condition:
            await self._wait_for(lambda: not self._writer and self._writers_waiting == 0, deadline)
            # Every reader takes its own file-lock level. Sharing one level between
            # in-process readers would hand the release to whichever reader leaves
            # last, and filelock refuses a release issued by any task other than the
            # one that acquired.
            await self._acquire_file("read", deadline)
            self._readers += 1

        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                try:
                    await self._release_file()
                finally:
                    # Wake waiters even when the release failed, so one broken
                    # release cannot strand them for the whole lock timeout.
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
                # Readers queued behind this writer wait for the queue position to
                # clear; a writer that times out or fails to acquire must still wake
                # them, or they sleep out their whole timeout behind a gone writer.
                self._condition.notify_all()
            await self._acquire_file("write", deadline)
            self._writer = True

        try:
            yield
        finally:
            async with self._condition:
                try:
                    await self._release_file()
                finally:
                    # The writer flag must never outlive the lock: leaving it set
                    # blocks every later writer on the same instance for the whole
                    # lock timeout.
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
