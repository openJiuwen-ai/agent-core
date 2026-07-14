# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import heapq
import hashlib
import os
import pathlib
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from filelock import Timeout as FileLockTimeout

from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.sys_operation.local._async_read_write_lock import HybridAsyncReadWriteLock


@dataclass
class _RwLockEntry:
    """A process-local wrapper around one cross-process lock database."""

    lock: HybridAsyncReadWriteLock
    lease_count: int = 0


class ReadWriteLockManager:
    """Manage the process-local lifecycle of cross-process file locks."""

    _lock_dir: Optional[pathlib.Path] = None
    _locks: Dict[pathlib.Path, _RwLockEntry] = {}
    _generations: Dict[pathlib.Path, int] = {}
    _idle_heap: List[Tuple[float, int, pathlib.Path]] = []
    _cleanup_task: Optional[asyncio.Task] = None
    _idle_ttl = 2 * 60
    _cleanup_interval = 20 * 60

    @classmethod
    def get_lock_file(cls, file_path: pathlib.Path) -> pathlib.Path:
        """Return the deterministic cross-process lock database for a file path."""
        normalized_path = os.path.normcase(str(file_path.resolve(strict=False)))
        digest = hashlib.sha256(normalized_path.encode("utf-8", errors="surrogatepass")).hexdigest()
        return cls.ensure_lock_dir() / f"{digest}.db"

    @classmethod
    def ensure_lock_dir(cls) -> pathlib.Path:
        """Create or reuse the machine-local directory for lock databases."""
        if cls._lock_dir is None:
            cls._lock_dir = pathlib.Path(tempfile.gettempdir()) / "openjiuwen-fs-rwlocks"
        cls._lock_dir.mkdir(parents=True, exist_ok=True)
        return cls._lock_dir

    @classmethod
    async def close_locks(cls) -> None:
        """Close all read-write lock resources cached by this process."""
        entries = tuple(cls._locks.values())
        for entry in entries:
            await entry.lock.close()
        cls._locks.clear()

    @classmethod
    async def stop(cls) -> None:
        """Stop local cleanup work and perform one final expired-lock scan."""
        sys_operation_logger.info(
            "Stopping read-write lock cleanup, cached_locks=%s, pending_cleanup=%s",
            len(cls._locks),
            len(cls._idle_heap),
        )
        task = cls._cleanup_task
        cls._cleanup_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await cls.cleanup_expired_locks()
        await cls.close_locks()
        cls._idle_heap.clear()
        cls._generations.clear()
        sys_operation_logger.info("Stopped read-write lock cleanup")

    @classmethod
    def start(cls) -> None:
        """Start the periodic cleanup task for idle lock databases."""
        if cls._cleanup_task is not None and not cls._cleanup_task.done():
            return

        lock_dir = cls.ensure_lock_dir()
        loop = asyncio.get_running_loop()
        restored_count = 0
        for lock_file in lock_dir.glob("*.db"):
            remaining = max(0.0, lock_file.stat().st_mtime + cls._idle_ttl - time.time())
            cls._schedule_idle_lock(lock_file, loop.time() + remaining)
            restored_count += 1
        cls._cleanup_task = loop.create_task(cls._run_cleanup())
        sys_operation_logger.info(
            "Started read-write lock cleanup, lock_dir=%s, restored_locks=%s",
            lock_dir,
            restored_count,
        )

    @classmethod
    async def _run_cleanup(cls) -> None:
        while True:
            await asyncio.sleep(cls._cleanup_interval)
            await cls.cleanup_expired_locks()

    @classmethod
    def _schedule_idle_lock(cls, lock_file: pathlib.Path, deadline: float) -> None:
        generation = cls._generations.get(lock_file, 0)
        heapq.heappush(cls._idle_heap, (deadline, generation, lock_file))
        sys_operation_logger.debug(
            "Scheduled read-write lock cleanup, lock_file=%s, generation=%s, delay_seconds=%.3f",
            lock_file,
            generation,
            max(0.0, deadline - asyncio.get_running_loop().time()),
        )

    @classmethod
    async def cleanup_expired_locks(cls) -> None:
        """Delete expired databases that can be exclusively acquired now."""
        now = asyncio.get_running_loop().time()
        while cls._idle_heap and cls._idle_heap[0][0] <= now:
            _, generation, lock_file = heapq.heappop(cls._idle_heap)
            entry = cls._locks.get(lock_file)
            current_generation = cls._generations.get(lock_file, 0)
            if generation != current_generation:
                sys_operation_logger.debug(
                    "Skipped stale read-write lock cleanup, lock_file=%s, queued_generation=%s, current_generation=%s",
                    lock_file,
                    generation,
                    current_generation,
                )
                continue
            if entry is not None and entry.lease_count:
                sys_operation_logger.debug(
                    "Skipped active read-write lock cleanup, lock_file=%s, lease_count=%s",
                    lock_file,
                    entry.lease_count,
                )
                continue

            if entry is None:
                entry = cls._get_or_create_lock(lock_file)
            try:
                await entry.lock.file_lock.acquire_write(timeout=0, blocking=False)
            except FileLockTimeout:
                current_entry = cls._locks.get(lock_file)
                if generation == cls._generations.get(lock_file, 0) and (
                        current_entry is None or not current_entry.lease_count
                ):
                    cls._schedule_idle_lock(lock_file, now + cls._cleanup_interval)
                    sys_operation_logger.debug(
                        "Deferred read-write lock cleanup because exclusive access is unavailable, lock_file=%s",
                        lock_file,
                    )
                continue
            await entry.lock.file_lock.release()

            current_entry = cls._locks.get(lock_file)
            if (generation != cls._generations.get(lock_file, 0) or current_entry
                    is not None and current_entry.lease_count):
                continue
            try:
                lock_file.unlink()
                sys_operation_logger.info("Deleted expired read-write lock database, lock_file=%s", lock_file)
            except FileNotFoundError:
                sys_operation_logger.debug("Read-write lock database was already deleted, lock_file=%s", lock_file)
            except OSError as exc:
                sys_operation_logger.warning(
                    "Failed to delete expired read-write lock database, lock_file=%s, error=%s",
                    lock_file,
                    exc,
                )

    @classmethod
    def get_lock(cls, file_path: pathlib.Path) -> HybridAsyncReadWriteLock:
        """Return the process-local lock object for a file path."""
        return cls._get_or_create_lock(cls.get_lock_file(file_path)).lock

    @classmethod
    def _get_or_create_lock(cls, lock_file: pathlib.Path) -> _RwLockEntry:
        entry = cls._locks.get(lock_file)
        if entry is None:
            entry = _RwLockEntry(lock=HybridAsyncReadWriteLock(lock_file))
            cls._locks[lock_file] = entry
        return entry

    @classmethod
    def _acquire_lease(cls, file_path: pathlib.Path) -> Tuple[pathlib.Path, _RwLockEntry]:
        cls.start()
        lock_file = cls.get_lock_file(file_path)
        entry = cls._get_or_create_lock(lock_file)
        cls._generations[lock_file] = cls._generations.get(lock_file, 0) + 1
        entry.lease_count += 1
        return lock_file, entry

    @classmethod
    async def _release_lease(cls, lock_file: pathlib.Path, entry: _RwLockEntry) -> None:
        entry.lease_count -= 1
        if entry.lease_count:
            return

        lock_file.touch(exist_ok=True)
        cls._schedule_idle_lock(lock_file, asyncio.get_running_loop().time() + cls._idle_ttl)

    @classmethod
    @asynccontextmanager
    async def lock_guard(
            cls,
            file_path: pathlib.Path,
            mode: Literal["read", "write"],
            timeout: float,
    ):
        """Acquire a file lock while tracking its process-local lease."""
        lock_file, entry = cls._acquire_lease(file_path)
        guard = entry.lock.read(timeout) if mode == "read" else entry.lock.write(timeout)
        try:
            async with guard:
                yield
        finally:
            await cls._release_lease(lock_file, entry)
