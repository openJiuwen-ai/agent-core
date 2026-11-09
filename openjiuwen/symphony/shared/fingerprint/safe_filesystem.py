# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Open regular files beneath a root without following links where supported."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

_OPEN_MODE = 0o600


def open_regular_file_no_follow(path: Path, *, root: Path | None = None) -> int | None:
    """Use anchored opens on supported systems and a checked path on Windows."""

    if root is not None:
        if supports_anchored_open():
            return _open_regular_file_beneath(root, path)
        if not _is_beneath(root, path):
            return None
    return _open_regular_file(path)


def supports_anchored_open() -> bool:
    return hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def open_directory_no_follow(path: Path, *, root: Path) -> int | None:
    if not supports_anchored_open():
        return None
    with ExitStack() as descriptors:
        current = descriptors.enter_context(_opened_directory_descriptor(Path(os.path.abspath(root))))
        try:
            relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
        except ValueError:
            return None
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


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


@contextmanager
def _opened_directory_descriptor(path: Path | str, *, dir_fd: int | None = None) -> Iterator[int]:
    descriptor = os.open(path, _directory_flags(), mode=_OPEN_MODE, dir_fd=dir_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _open_regular_file_beneath(root: Path, path: Path) -> int | None:
    root_absolute = Path(os.path.abspath(root))
    try:
        relative = Path(os.path.abspath(path)).relative_to(root_absolute)
    except ValueError:
        return None
    if not relative.parts:
        return None
    with ExitStack() as descriptors:
        current = descriptors.enter_context(_opened_directory_descriptor(root_absolute))
        for component in relative.parts[:-1]:
            current = descriptors.enter_context(_opened_directory_descriptor(component, dir_fd=current))
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        file_descriptor = os.open(relative.parts[-1], flags, mode=_OPEN_MODE, dir_fd=current)
        try:
            is_regular = stat.S_ISREG(os.fstat(file_descriptor).st_mode)
        except OSError:
            os.close(file_descriptor)
            raise
        if not is_regular:
            os.close(file_descriptor)
            return None
        return file_descriptor


def _is_beneath(root: Path, path: Path) -> bool:
    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return False
    return bool(relative.parts)


def _open_regular_file(path: Path) -> int | None:
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
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
