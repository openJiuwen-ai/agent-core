# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows junction creation with extended-length path support.

``mklink /J`` (the cmd builtin) cannot create a junction whose link path
exceeds the classic directory limit of 248 chars (MAX_PATH minus room for
an 8.3 child name), and cmd.exe does not understand ``\\\\?\\`` prefixes,
so long link paths are unreachable through it. This module first tries
``mklink`` (preserving the historical behavior and its error messages),
then falls back to writing the reparse point directly through
``CreateDirectoryW`` + ``DeviceIoControl(FSCTL_SET_REPARSE_POINT)`` with
``\\\\?\\`` extended-length paths, which lifts the limit to ~32767 chars
and needs neither elevation nor Developer Mode.

Callers get the same contract as before: success returns None, failure
raises ``OSError``.
"""

import ctypes
import os
import struct
import subprocess

from openjiuwen.core.common.logging import team_logger

_FSCTL_SET_REPARSE_POINT = 0x000900A4
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _create_junction_via_mklink(target_path: str, link_path: str) -> None:
    """Create a directory junction using ``mklink /J`` (248-char link limit)."""
    cmd_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    result = subprocess.run(
        [cmd_path, "/c", "mklink", "/J", link_path, target_path],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        error_output = result.stderr.strip() or result.stdout.strip()
        raise OSError(f"Failed to create junction {link_path} -> {target_path}: {error_output}")


def _create_junction_via_reparse(target_path: str, link_path: str) -> None:
    """Create a directory junction via the reparse-point API (long paths).

    Both the link directory and the handle are opened through ``\\\\?\\``
    extended-length paths, bypassing the 248-char directory limit. The
    reparse buffer stores the target as an NT-native ``\\??\\`` path with
    the plain path as print name, matching what ``mklink /J`` produces.
    """
    target_abs = os.path.abspath(target_path)
    link_abs = os.path.abspath(link_path)
    ext_link = "\\\\?\\" + link_abs
    if not ctypes.windll.kernel32.CreateDirectoryW(ext_link, None):
        err = ctypes.GetLastError()
        raise OSError(err, f"CreateDirectoryW failed for junction link {link_abs}")
    handle = ctypes.windll.kernel32.CreateFileW(
        ext_link,
        _GENERIC_WRITE,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (None, 0, _INVALID_HANDLE_VALUE):
        err = ctypes.GetLastError()
        raise OSError(err, f"CreateFileW failed for junction link {link_abs}")
    try:
        sub_bytes = ("\\??\\" + target_abs).encode("utf-16-le")
        print_bytes = target_abs.encode("utf-16-le")
        print_offset = len(sub_bytes) + 2
        data_length = 8 + len(sub_bytes) + 2 + len(print_bytes) + 2
        buffer = struct.pack(
            "<IHHHHHH",
            _IO_REPARSE_TAG_MOUNT_POINT,
            data_length,
            0,
            0,
            len(sub_bytes),
            print_offset,
            len(print_bytes),
        ) + sub_bytes + b"\x00\x00" + print_bytes + b"\x00\x00"
        bytes_returned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            handle,
            _FSCTL_SET_REPARSE_POINT,
            buffer,
            len(buffer),
            None,
            0,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err = ctypes.GetLastError()
            raise OSError(err, f"FSCTL_SET_REPARSE_POINT failed for junction link {link_abs}")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def create_windows_junction(target_path: str, link_path: str) -> None:
    """Create a directory junction, tolerating link paths beyond 248 chars.

    Tries ``mklink /J`` first (unchanged legacy behavior); when that
    fails, retries through the reparse-point API with extended-length
    paths before giving up. Raises ``OSError`` only when both fail.
    """
    try:
        _create_junction_via_mklink(target_path, link_path)
        return
    except OSError as mklink_error:
        try:
            _create_junction_via_reparse(target_path, link_path)
        except OSError as reparse_error:
            raise OSError(
                f"Failed to create junction {link_path} -> {target_path}: "
                f"mklink: {mklink_error}; reparse fallback: {reparse_error}"
            ) from reparse_error
        team_logger.warning(
            "mklink failed (%s); created junction at %s via reparse-point API",
            mklink_error,
            link_path,
        )
