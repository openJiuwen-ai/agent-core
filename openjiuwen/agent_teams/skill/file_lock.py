# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cross-process file locking for team Skill visibility metadata.

A team's visibility declarations are written by the gateway (authorization
RPC), by the startup migration of legacy Skill views, and by team assembly
seeding — potentially from different processes on the same machine.
``portalocker`` gives the same advisory-lock semantics on POSIX (``fcntl``) and
Windows (``msvcrt``), so the guard below is the single locking primitive used
by every writer of ``skills-visibility.json``.

The lock is always taken on a *sidecar* file (``.<name>.lock``) and never on the
protected file itself. That matters on Windows, where ``os.replace`` fails when
the destination path is still open by any handle: locking the payload file
directly would deadlock the atomic-write pattern the metadata writers rely on.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import portalocker

# Long enough to outlast a slow NFS/SMB round-trip, short enough that a crashed
# holder cannot wedge an agent turn indefinitely.
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0


class FileLockTimeout(TimeoutError):
    """Raised when a cross-process file lock cannot be acquired in time."""


def lock_path_for(target: str | Path) -> Path:
    """Return the sidecar lock file path guarding ``target``.

    Args:
        target: Path of the file whose access must be serialized.

    Returns:
        Path of the sidecar lock file, a hidden sibling of ``target``.
    """
    resolved = Path(target).expanduser()
    return resolved.parent / f".{resolved.name}.lock"


@contextmanager
def cross_process_file_lock(
    target: str | Path,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Hold an exclusive cross-process lock while accessing ``target``.

    The parent directory is created if missing so callers can lock a path whose
    workspace has not been materialized yet.

    Args:
        target: Path of the file being protected. The lock itself is taken on a
            sidecar file next to it, never on ``target``.
        timeout: Seconds to wait for the lock before giving up.

    Yields:
        The sidecar lock file path, for callers that want to log it.

    Raises:
        FileLockTimeout: The lock was still held elsewhere when ``timeout``
            expired.
    """
    sidecar = lock_path_for(target)
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    # Acquire outside the ``try`` that wraps the body so a LockException raised
    # by user code inside the block is not misreported as an acquisition timeout.
    handle = portalocker.Lock(str(sidecar), mode="a", timeout=timeout)
    try:
        handle.acquire()
    except portalocker.exceptions.LockException as exc:
        raise FileLockTimeout(f"failed to acquire file lock within {timeout}s: {sidecar}") from exc

    try:
        yield sidecar
    finally:
        handle.release()


__all__ = [
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "FileLockTimeout",
    "cross_process_file_lock",
    "lock_path_for",
]
