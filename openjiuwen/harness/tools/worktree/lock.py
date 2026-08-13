# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Repo-bucketed concurrency lock for worktree lifecycle critical sections.

Serializes worktree create/remove against the same ``repo_root`` so they do
not race on shared ``.git`` state. Two layers, acquired in-process first then
cross-process, released in reverse (no deadlock):

1. **In-process** ``asyncio.Lock`` (module-level ``{repo_root: Lock}`` dict).
   Module-level because ``with_worktrees_dir`` / ``with_base_dir`` build fresh
   manager instances via ``model_copy``; an instance-level lock would not be
   shared across those views.

2. **Cross-process** ``filelock.AsyncFileLock`` on
   ``<repo_root>/.git/openjiuwen-worktree.lock``.

Timeout raises rather than degrading to lock-free. Exclusive (not read/write):
every worktree critical section mutates shared state. Read-only fast paths
stay outside the lock by caller convention.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from filelock import AsyncFileLock, Timeout as FileLockTimeout

from openjiuwen.core.common.logging import team_logger


class WorktreeLockTimeout(TimeoutError):
    """Repo lock not acquired within the timeout.

    Raised rather than degrading to lock-free, which would reintroduce the
    race this lock prevents.
    """


# Module-level bucket keyed by repo_root. Shared across all manager instances
# and asyncio tasks in this process; dict mutation guarded by _BUCKET_GUARD.
_INPROCESS_LOCKS: dict[str, asyncio.Lock] = {}
_BUCKET_GUARD = asyncio.Lock()


def _lock_file_path(repo_root: str) -> str:
    """Cross-process lock file path under ``<repo_root>/.git/``, or beside ``repo_root`` if no ``.git/``."""
    git_dir = os.path.join(repo_root, ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, "openjiuwen-worktree.lock")
    return os.path.join(repo_root, ".openjiuwen-worktree.lock")


async def _get_inprocess_lock(repo_root: str) -> asyncio.Lock:
    """Return the shared in-process ``asyncio.Lock`` for ``repo_root``."""
    async with _BUCKET_GUARD:
        lock = _INPROCESS_LOCKS.get(repo_root)
        if lock is None:
            lock = asyncio.Lock()
            _INPROCESS_LOCKS[repo_root] = lock
        return lock


@asynccontextmanager
async def repo_lock(
    repo_root: str,
    *,
    timeout: float = 120.0,
    enabled: bool = True,
) -> AsyncIterator[None]:
    """Serialize the worktree critical section for ``repo_root``.

    Two-layer exclusive lock: in-process first, then cross-process; released
    in reverse so no deadlock is possible.

    Args:
        repo_root: Canonical git repository root. Different roots never contend.
        timeout: Seconds to wait for both layers before raising.
        enabled: When False, the lock is a no-op (remote backends that manage
            their own isolation opt out).

    Raises:
        WorktreeLockTimeout: Either layer not acquired in time.
    """
    if not enabled:
        yield
        return

    inproc = await _get_inprocess_lock(repo_root)
    lock_file = _lock_file_path(repo_root)

    parent = os.path.dirname(lock_file)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass  # filelock will surface a clearer error if creation truly fails

    file_lock = AsyncFileLock(lock_file, timeout=timeout)

    # Layer 1: in-process. asyncio.timeout (not wait_for) avoids the cancel
    # race where Lock.acquire is cancelled mid-flight, leaking the bucket.
    # inproc_acquired + locked() guard release() to held-only.
    inproc_acquired = False
    try:
        async with asyncio.timeout(timeout):
            await inproc.acquire()
        inproc_acquired = True
    except TimeoutError as exc:
        raise WorktreeLockTimeout(
            f"Timed out waiting for in-process worktree lock on {repo_root} "
            f"after {timeout}s"
        ) from exc

    try:
        # Layer 2: cross-process (runs blocking syscall in an executor).
        try:
            await file_lock.acquire()
        except FileLockTimeout as exc:
            raise WorktreeLockTimeout(
                f"Timed out waiting for cross-process worktree lock "
                f"({lock_file}) after {timeout}s"
            ) from exc
        except PermissionError as exc:
            # Lock file cannot be created/written. Raise rather than degrade
            # to lock-free (would reintroduce the cross-process race).
            # Set repo_lock=False where there is no cross-process contention.
            raise WorktreeLockTimeout(
                f"Cannot acquire cross-process worktree lock ({lock_file}): "
                f"permission denied. Set repo_lock=False if this repo_root "
                f"has no cross-process contention."
            ) from exc

        try:
            yield
        finally:
            try:
                await file_lock.release()  # AsyncFileLock.release is a coroutine
            except Exception:  # noqa: BLE001 - release best-effort
                team_logger.debug("Failed to release cross-process worktree lock %s", lock_file)
    finally:
        if inproc_acquired and inproc.locked():
            inproc.release()


def clear_lock_buckets() -> None:
    """Clear in-process lock buckets. Test-only; never call while operations are in flight."""
    _INPROCESS_LOCKS.clear()


__all__ = [
    "WorktreeLockTimeout",
    "repo_lock",
    "clear_lock_buckets",
]
