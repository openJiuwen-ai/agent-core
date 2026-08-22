# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cross-platform directory-link primitives (design-v5, block C).

Creates symlinks first (full ``islink`` / ``rmtree``-guard semantics), falls
back to a Windows junction (``mklink /J``) when the runtime lacks the
privilege, and never copies. ``remove_dir_link`` deletes only the link, never
the target — a junction must go through ``os.rmdir`` (RemoveDirectory on a
reparse point unlinks it), never ``shutil.rmtree`` which would descend and
delete the target contents.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Windows directory junction / symlink reparse-point attribute.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def create_dir_link(target: Path, link: Path) -> None:
    """Create ``link`` pointing at ``target``; never copies.

    Tries ``os.symlink`` first. On Windows without Developer Mode / elevation
    (winerror 1314 ``ERROR_PRIVILEGE_NOT_HELD`` or 5 ``ACCESS_DENIED``) it
    falls back to ``mklink /J``. Any other failure (including POSIX
    EACCES/EPERM) is re-raised so the binder can retreat into the team tree.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) in (1314, 5):
            _create_windows_junction(target, link)
            return
        raise


def is_dir_link(path: Path) -> bool:
    """Return True when ``path`` is a symlink or a Windows reparse point."""
    if os.path.islink(path):
        return True
    if os.name != "nt":
        return False
    try:
        path_stat = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(path_stat, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def remove_dir_link(path: Path) -> bool:
    """Remove only the link itself; return False when ``path`` is not a link.

    A symlink is unlinked; a Windows junction is removed with ``os.rmdir``
    (never ``shutil.rmtree`` — that would descend into the target and delete
    shared assets).
    """
    if os.path.islink(path):
        os.unlink(path)
        return True
    if os.name == "nt" and is_dir_link(path):
        os.rmdir(path)
        return True
    return False


def _create_windows_junction(target: Path, link: Path) -> None:
    """Create a Windows junction via ``mklink /J`` (no privilege required)."""
    cmd_path = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "cmd.exe",
    )
    result = subprocess.run(
        [cmd_path, "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise OSError(
            f"Failed to create junction {link} -> {target}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


__all__ = ["create_dir_link", "is_dir_link", "remove_dir_link"]
