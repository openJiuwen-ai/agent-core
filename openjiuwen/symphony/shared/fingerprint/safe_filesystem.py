# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Race-resistant file opening beneath an explicitly supplied directory."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

_OPEN_MODE = 0o600


def open_regular_file_no_follow(path: Path, *, root: Path | None = None) -> int | None:
    """Open a regular file without following its final component or rooted ancestors."""

    if root is not None:
        if not supports_anchored_open():
            return None
        return _open_regular_file_beneath(root, path)
    return _open_regular_file_fallback(path)


def open_directory_no_follow(path: Path, *, root: Path) -> int | None:
    """Open a directory beneath *root* without following any path component."""

    if not supports_anchored_open():
        return None
    return _open_directory_beneath(root, path)


def supports_anchored_open() -> bool:
    """Whether this platform can securely anchor no-follow opens to a root fd."""

    return hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


@contextmanager
def _opened_directory_descriptor(path: Path | str, *, dir_fd: int | None = None) -> Iterator[int]:
    """Yield one no-follow directory descriptor and always release it locally."""

    descriptor = os.open(path, _directory_flags(), mode=_OPEN_MODE, dir_fd=dir_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _open_regular_file_beneath(root: Path, path: Path) -> int | None:
    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return None
    if not relative.parts:
        return None

    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        file_flags |= os.O_CLOEXEC

    with ExitStack() as descriptors:
        current = descriptors.enter_context(_opened_directory_descriptor(root_absolute))
        for component in relative.parts[:-1]:
            current = descriptors.enter_context(_opened_directory_descriptor(component, dir_fd=current))
        file_descriptor = os.open(relative.parts[-1], file_flags, mode=_OPEN_MODE, dir_fd=current)
        try:
            is_regular = stat.S_ISREG(os.fstat(file_descriptor).st_mode)
        except OSError:
            os.close(file_descriptor)
            raise
        if not is_regular:
            os.close(file_descriptor)
            return None
        return file_descriptor


def _open_directory_beneath(root: Path, path: Path) -> int | None:
    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return None

    with ExitStack() as descriptors:
        current = descriptors.enter_context(_opened_directory_descriptor(root_absolute))
        for component in relative.parts:
            current = descriptors.enter_context(_opened_directory_descriptor(component, dir_fd=current))
        result = os.dup(current)
        try:
            is_directory = stat.S_ISDIR(os.fstat(result).st_mode)
        except OSError:
            os.close(result)
            raise
        if not is_directory:
            os.close(result)
            return None
        return result


def _open_regular_file_fallback(path: Path) -> int | None:
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(path, flags, mode=_OPEN_MODE)
    try:
        file_stat = os.fstat(file_descriptor)
    except OSError:
        os.close(file_descriptor)
        raise
    if stat.S_IFMT(file_stat.st_mode) != stat.S_IFREG or (
        file_stat.st_dev,
        file_stat.st_ino,
    ) != (path_stat.st_dev, path_stat.st_ino):
        os.close(file_descriptor)
        return None
    return file_descriptor


__all__ = ["open_directory_no_follow", "open_regular_file_no_follow", "supports_anchored_open"]
