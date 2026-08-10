# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conservative regular-file opening beneath an explicitly supplied directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_OPEN_MODE = 0o600


def open_regular_file_no_follow(path: Path, *, root: Path | None = None) -> int | None:
    """Open a regular file by path after validating its root and final component."""

    if root is not None and not _is_beneath(root, path):
        return None
    return _open_regular_file(path)


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


__all__ = ["open_regular_file_no_follow"]
