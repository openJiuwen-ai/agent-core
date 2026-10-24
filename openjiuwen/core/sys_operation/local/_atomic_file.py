# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish one complete file without exposing a truncated destination."""

import asyncio
import os
import secrets
import stat
from pathlib import Path


def _replace_file(
    path: Path, content: bytes, permissions: str, expected_parent_identity: tuple[int, int] | None = None
) -> None:
    parent = path.parent
    before = parent.stat()
    if expected_parent_identity is not None and (before.st_dev, before.st_ino) != expected_parent_identity:
        raise OSError("Atomic-write parent no longer belongs to this operation")
    parent_fd = None
    name = f".{path.name}-{secrets.token_hex(12)}.tmp"
    owned = False
    owned_identity = None
    try:
        if os.name != "nt":
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened = os.fstat(parent_fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("File parent changed before atomic write")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(
            name if parent_fd is not None else parent / name,
            flags,
            0o600,
            **({"dir_fd": parent_fd} if parent_fd is not None else {}),
        )
        owned = True
        created = os.fstat(fd)
        owned_identity = (created.st_dev, created.st_ino)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), int(permissions, 8))
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Unsafe atomic-write temporary file")
        current = parent.stat()
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("File parent changed during atomic write")
        if parent_fd is not None:
            os.replace(name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        else:
            os.replace(parent / name, path)
        owned = False
    finally:
        if owned:
            try:
                current = os.stat(
                    name if parent_fd is not None else parent / name,
                    follow_symlinks=False,
                    **({"dir_fd": parent_fd} if parent_fd is not None else {}),
                )
                if (current.st_dev, current.st_ino) == owned_identity:
                    os.unlink(
                        name if parent_fd is not None else parent / name,
                        **({"dir_fd": parent_fd} if parent_fd is not None else {}),
                    )
            except FileNotFoundError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)


async def atomic_replace_file(
    path: Path, content: bytes, permissions: str, expected_parent_identity: tuple[int, int] | None = None
) -> None:
    """Keep the caller's file lock until the worker settles, even on cancellation.

    Cancellation after replace does not roll back a committed file. Draining
    also prevents a cancelled worker from publishing after its lock is released.
    """
    worker = asyncio.create_task(asyncio.to_thread(_replace_file, path, content, permissions, expected_parent_identity))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        # Retrieve a possible worker failure before propagating cancellation.
        worker.exception()
        raise asyncio.CancelledError
    worker.result()
