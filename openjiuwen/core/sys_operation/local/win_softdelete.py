# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load the Windows soft-delete DLL into the current process.

The DLL hooks ntdll delete calls and CreateProcessInternalW. Child
processes are injected by that hook. This module only loads the DLL once
into the agent process and publishes the archive directory in the environment
so children inherit it.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

_ENV_ENABLED = "JIUWENBOX_SOFT_DELETE"
_ENV_ARCHIVE = "JIUWENBOX_SOFT_DELETE_ARCHIVE"
_ENV_DLL = "JIUWENBOX_SOFT_DELETE_DLL"
_LOADED = False
_LOCK = threading.Lock()


def _dll_candidates() -> list[Path]:
    env_path = (os.environ.get(_ENV_DLL) or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    try:
        import jiuwenbox
    except ImportError:
        jiuwenbox = None
    if jiuwenbox is not None and getattr(jiuwenbox, "__file__", None):
        candidates.append(Path(jiuwenbox.__file__).resolve().parent / "native" / "jiuwen_softdelete.dll")
    here = Path(__file__).resolve()
    # local/ -> sys_operation/ -> core/ -> openjiuwen/ -> agent-core/ -> sibling checkout
    if len(here.parents) > 4:
        sibling = (
            here.parents[4].parent
            / "jiuwenclaw"
            / "jiuwenbox"
            / "src"
            / "jiuwenbox"
            / "native"
            / "jiuwen_softdelete.dll"
        )
        candidates.append(sibling)
    return candidates


def _find_dll() -> Path:
    for candidate in _dll_candidates():
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(item) for item in _dll_candidates())
    raise FileNotFoundError(f"jiuwen_softdelete.dll not found; searched: {searched}")


def _load_error_text() -> str:
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    if not temp:
        return ""
    path = Path(temp) / "jiuwen-softdelete-error.txt"
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def install_win_soft_delete(archive_dir: str) -> None:
    """Load the soft-delete DLL. No-op off Windows. Raises if the load fails.

    Args:
        archive_dir: Absolute directory used when the recycle bin cannot
            accept the file. Must be non-empty. Same-volume moves only.
    """
    global _LOADED
    if sys.platform != "win32":
        return
    with _LOCK:
        if _LOADED:
            return
        raw = (archive_dir or "").strip()
        if not raw:
            raise RuntimeError("soft-delete archive directory is empty")
        archive = os.path.abspath(raw)
        dll = _find_dll()
        os.environ[_ENV_ENABLED] = "1"
        os.environ[_ENV_ARCHIVE] = archive
        os.environ[_ENV_DLL] = str(dll)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LoadLibraryW.argtypes = [ctypes.c_wchar_p]
        kernel.LoadLibraryW.restype = ctypes.c_void_p
        handle = kernel.LoadLibraryW(str(dll))
        if not handle:
            error = ctypes.get_last_error()
            detail = _load_error_text()
            os.environ.pop(_ENV_ENABLED, None)
            os.environ.pop(_ENV_ARCHIVE, None)
            os.environ.pop(_ENV_DLL, None)
            message = f"failed to load soft-delete DLL {dll} (winerror={error})"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message)
        _LOADED = True
