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
from typing import Literal

from filelock import Timeout as FileLockTimeout

from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.sys_operation.local._async_read_write_lock import HybridAsyncReadWriteLock


@dataclass
class _RwLockEntry:
    """A process-local wrapper around one cross-process lock database."""

    lock: HybridAsyncReadWriteLock
    lease_count: int = 0


class ReadWriteLockManager:
    """Manage process-local locks and cross-process database cleanup."""

    _lock_dir: pathlib.Path | None = None
    _locks: dict[pathlib.Path, _RwLockEntry] = {}
    _state_lock: asyncio.Lock | None = None
    _cleanup_task: asyncio.Task | None = None
    _idle_heap: list[tuple[float, pathlib.Path]] = []
    _idle_deadlines: dict[pathlib.Path, float] = {}
    _idle_ttl = 2 * 60
    _cleanup_interval = 60
    # Databases left behind by an earlier process are only reclaimed once they
    # have been untouched for far longer than any live process leaves its own
    # lock idle. The mtime is a *candidate filter*, never proof of death:
    # SQLite refreshes it on write, so a process holding a database for reads
    # alone looks idle while still using it. Deleting such a database would put
    # it and the surviving process on different inodes and silently end the
    # mutual exclusion this module exists to provide, so the threshold is a
    # full day rather than ``_idle_ttl``. Exclusive-lock probing in
    # ``_try_delete_database`` is what actually decides.
    _orphan_ttl = 24 * 60 * 60
    # Candidates are collected a batch at a time, so a directory that
    # accumulated many databases drains over several ticks instead of stalling
    # a single one on thousands of SQLite probes.
    _orphan_sweep_batch = 200

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
    def _get_state_lock(cls) -> asyncio.Lock:
        if cls._state_lock is None:
            cls._state_lock = asyncio.Lock()
        return cls._state_lock

    @classmethod
    async def close_locks(cls) -> None:
        """Detach and close every business lock cached by this process."""
        async with cls._get_state_lock():
            entries = tuple(cls._locks.values())
            cls._locks.clear()
            for entry in entries:
                entry.lock.evict_singleton()
                await entry.lock.close()

    @classmethod
    async def stop(cls) -> None:
        """Stop cleanup work, close local resources, and process final due entries."""
        sys_operation_logger.info(
            "Stopping read-write lock cleanup, cached_locks=%s, pending_cleanup=%s",
            len(cls._locks),
            len(cls._idle_deadlines),
        )
        task = cls._cleanup_task
        cls._cleanup_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await cls.close_locks()
        await cls.cleanup_expired_locks()
        cls._idle_heap.clear()
        cls._idle_deadlines.clear()
        cls._state_lock = None
        sys_operation_logger.info("Stopped read-write lock cleanup")

    @classmethod
    def start(cls) -> None:
        """Start the periodic cleanup task."""
        if cls._cleanup_task is not None and not cls._cleanup_task.done():
            return

        lock_dir = cls.ensure_lock_dir()
        cls._cleanup_task = asyncio.get_running_loop().create_task(cls._run_cleanup())
        sys_operation_logger.info("Started read-write lock cleanup, lock_dir=%s", lock_dir)

    @classmethod
    async def _run_cleanup(cls) -> None:
        while True:
            await asyncio.sleep(cls._cleanup_interval)
            await cls._sweep_orphan_databases()
            await cls.cleanup_expired_locks()

    @classmethod
    def _collect_orphan_candidates(cls, known: frozenset[pathlib.Path]) -> tuple[list[pathlib.Path], int]:
        """Scan the lock directory for databases left behind by dead processes.

        Runs off the event loop: a directory listing plus one ``stat`` per
        entry is blocking syscall work, and the directories worth sweeping are
        exactly the ones with enough entries to make that measurable. The scan
        stops as soon as a full batch is collected, so a backlog costs a
        bounded number of syscalls per tick rather than one per file.

        Args:
            known: Lock files this process already tracks. Their lifecycle
                belongs to :meth:`cleanup_expired_locks`, so they are skipped.

        Returns:
            Up to ``_orphan_sweep_batch`` candidate paths, and the number of
            directory entries examined before the scan stopped.
        """
        candidates: list[pathlib.Path] = []
        scanned = 0
        cutoff = time.time() - cls._orphan_ttl
        try:
            with os.scandir(cls.ensure_lock_dir()) as entries:
                for entry in entries:
                    if len(candidates) >= cls._orphan_sweep_batch:
                        break
                    scanned += 1
                    if not entry.name.endswith(".db"):
                        continue
                    lock_file = pathlib.Path(entry.path)
                    if lock_file in known:
                        continue
                    try:
                        if entry.stat().st_mtime > cutoff:
                            continue
                    except OSError:
                        continue
                    candidates.append(lock_file)
        except OSError as exc:
            sys_operation_logger.warning(
                "Failed to scan read-write lock directory for orphans, error=%s", exc
            )
        return candidates, scanned

    @classmethod
    async def _sweep_orphan_databases(cls) -> None:
        """Queue lock databases left behind by earlier processes for cleanup.

        ``_idle_heap`` only tracks locks this process leased, so a database
        whose owning process was hard-killed would otherwise stay on disk
        forever.

        Age only selects candidates (see ``_orphan_ttl``); whether a database
        is genuinely unused is decided by :meth:`_try_delete_database`, which
        removes only what it can lock exclusively -- one another live process
        is holding is skipped rather than pulled out from under it.
        """
        # Snapshot before the thread hop: these dicts belong to the event loop.
        known = frozenset(cls._idle_deadlines) | frozenset(cls._locks)
        candidates, scanned = await asyncio.to_thread(cls._collect_orphan_candidates, known)
        if not candidates:
            return

        # Already past ``_orphan_ttl``, so they are due on this same tick.
        due_now = asyncio.get_running_loop().time()
        for lock_file in candidates:
            cls._schedule_idle_lock(lock_file, due_now)

        sys_operation_logger.info(
            "Queued orphaned read-write lock databases for cleanup, count=%s, scanned_entries=%s",
            len(candidates),
            scanned,
        )

    @classmethod
    def _schedule_idle_lock(cls, lock_file: pathlib.Path, deadline: float) -> None:
        if lock_file in cls._idle_deadlines:
            return
        cls._idle_deadlines[lock_file] = deadline
        heapq.heappush(cls._idle_heap, (deadline, lock_file))

    @classmethod
    def _pop_due_locks(cls, now: float) -> list[pathlib.Path]:
        due_locks = []
        while cls._idle_heap and cls._idle_heap[0][0] <= now:
            deadline, lock_file = heapq.heappop(cls._idle_heap)
            if cls._idle_deadlines.get(lock_file) != deadline:
                continue
            cls._idle_deadlines.pop(lock_file, None)
            due_locks.append(lock_file)
        return due_locks

    @classmethod
    async def _try_delete_database(cls, lock_file: pathlib.Path) -> bool:
        probe = HybridAsyncReadWriteLock(lock_file, is_singleton=False)
        acquired = False
        try:
            try:
                await probe.file_lock.acquire_write(timeout=0, blocking=False)
                acquired = True
            except FileLockTimeout:
                sys_operation_logger.debug(
                    "Deferred read-write lock database cleanup because exclusive access is unavailable, lock_file=%s",
                    lock_file,
                )
        finally:
            try:
                if acquired:
                    await probe.file_lock.release()
            finally:
                await probe.close()

        if not acquired:
            return False

        # Windows cannot unlink an SQLite database while the probe still has it open.
        async with cls._get_state_lock():
            if lock_file in cls._locks:
                return False
            try:
                lock_file.unlink()
                cls._idle_deadlines.pop(lock_file, None)
                sys_operation_logger.info("Deleted expired read-write lock database, lock_file=%s", lock_file)
                return True
            except FileNotFoundError:
                sys_operation_logger.debug(
                    "Read-write lock database was already deleted, lock_file=%s",
                    lock_file,
                )
                return False
            except OSError as exc:
                sys_operation_logger.warning(
                    "Failed to delete expired read-write lock database, lock_file=%s, error=%s",
                    lock_file,
                    exc,
                )
                return False

    @classmethod
    async def cleanup_expired_locks(cls) -> None:
        """Process due heap entries and delete databases that are exclusively available."""
        loop = asyncio.get_running_loop()
        due_locks = cls._pop_due_locks(loop.time())
        deleted_count = 0
        for lock_file in due_locks:
            try:
                remaining = lock_file.stat().st_mtime + cls._idle_ttl - time.time()
            except FileNotFoundError:
                continue
            if remaining > 0:
                cls._schedule_idle_lock(lock_file, loop.time() + remaining)
                continue

            if await cls._try_delete_database(lock_file):
                deleted_count += 1
            elif lock_file.exists() and lock_file not in cls._idle_deadlines:
                cls._schedule_idle_lock(lock_file, loop.time())
        sys_operation_logger.debug(
            "Completed read-write lock database cleanup, due_count=%s, deleted_count=%s",
            len(due_locks),
            deleted_count,
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
    async def _acquire_lease(cls, file_path: pathlib.Path) -> tuple[pathlib.Path, _RwLockEntry]:
        lock_file = cls.get_lock_file(file_path)
        async with cls._get_state_lock():
            entry = cls._get_or_create_lock(lock_file)
            entry.lease_count += 1
            return lock_file, entry

    @classmethod
    async def _release_lease(cls, lock_file: pathlib.Path, entry: _RwLockEntry) -> None:
        async with cls._get_state_lock():
            entry.lease_count -= 1
            if entry.lease_count:
                return

            if cls._locks.get(lock_file) is entry:
                cls._locks.pop(lock_file, None)
            entry.lock.evict_singleton()
            try:
                await entry.lock.close()
            finally:
                lock_file.touch(exist_ok=True)
                cls._schedule_idle_lock(
                    lock_file,
                    asyncio.get_running_loop().time() + cls._idle_ttl,
                )

    @classmethod
    @asynccontextmanager
    async def lock_guard(
            cls,
            file_path: pathlib.Path,
            mode: Literal["read", "write"],
            timeout: float,
    ):
        """Acquire the requested business file lock with a process-local lease."""
        cls.start()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        lock_file, entry = await cls._acquire_lease(file_path)
        remaining = max(0.0, deadline - loop.time())
        guard = entry.lock.read(remaining) if mode == "read" else entry.lock.write(remaining)
        try:
            async with guard:
                yield
        finally:
            await cls._release_lease(lock_file, entry)
