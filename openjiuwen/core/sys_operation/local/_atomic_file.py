# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish one complete file without exposing a truncated destination."""

import asyncio
import errno
import os
import secrets
import stat
import sys
from pathlib import Path


def _read_posix_access_acl(fd: int) -> bytes | None:
    if sys.platform != "linux":
        return None
    try:
        return os.getxattr(fd, "system.posix_acl_access")
    except OSError as exc:
        if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise
        return None


def _restore_posix_access_acl(fd: int, acl: bytes | None) -> None:
    if sys.platform != "linux":
        return
    if acl is not None:
        os.setxattr(fd, "system.posix_acl_access", acl)
    else:
        # The temporary inode must not gain a default ACL absent from the target.
        try:
            os.removexattr(fd, "system.posix_acl_access")
        except OSError as exc:
            if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
                raise


def _copy_windows_security(source: Path, destination: Path) -> None:
    """Preserve owner/group/DACL and its inheritance policy before publication."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, *([ctypes.POINTER(ctypes.c_void_p)] * 5)]
    get.restype = wintypes.DWORD
    put = api.SetNamedSecurityInfoW
    put.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, *([ctypes.c_void_p] * 4)]
    put.restype = wintypes.DWORD
    owner, group, dacl, descriptor = (ctypes.c_void_p() for _ in range(4))
    information = 7  # OWNER | GROUP | DACL
    error = get(str(source), 1, information, ctypes.byref(owner), ctypes.byref(group),
                ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        get_control = api.GetSecurityDescriptorControl
        get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        get_control.restype = wintypes.BOOL
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        information |= 0x80000000 if control.value & 0x1000 else 0x20000000
        if control.value & 0x0400:  # SE_DACL_AUTO_INHERITED
            error = put(str(destination), 1, information, owner, group, dacl, None)
            if error:
                raise ctypes.WinError(error)
        else:
            # The named getter can annotate legacy ACEs as inherited. Read the
            # raw descriptor so copying it does not change their future behavior.
            get_raw = api.GetFileSecurityW
            get_raw.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            get_raw.restype = wintypes.BOOL
            needed = wintypes.DWORD()
            get_raw(str(source), 7, None, 0, ctypes.byref(needed))
            if not needed.value:
                raise ctypes.WinError(ctypes.get_last_error())
            raw = ctypes.create_string_buffer(needed.value)
            if not get_raw(str(source), 7, raw, needed.value, ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            put_legacy = api.SetFileSecurityW
            put_legacy.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
            put_legacy.restype = wintypes.BOOL
            if not put_legacy(str(destination), information, raw):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(descriptor)


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
    existing = None
    existing_acl = None
    try:
        if os.name != "nt":
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened = os.fstat(parent_fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("File parent changed before atomic write")
        # Permission to replace a directory entry must not bypass file write access.
        try:
            target_fd = os.open(
                path.name if parent_fd is not None else path,
                os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                **({"dir_fd": parent_fd} if parent_fd is not None else {}),
            )
        except FileNotFoundError:
            pass
        else:
            try:
                existing = os.fstat(target_fd)
                if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                    raise OSError("Unsafe atomic-write target")
                existing_acl = _read_posix_access_acl(target_fd)
            finally:
                os.close(target_fd)
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
            if (hasattr(os, "fchown") and existing is not None
                    and (created.st_uid, created.st_gid) != (existing.st_uid, existing.st_gid)):
                os.fchown(stream.fileno(), existing.st_uid, existing.st_gid)
            if existing is not None:
                _restore_posix_access_acl(stream.fileno(), existing_acl)
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), int(permissions, 8))
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Unsafe atomic-write temporary file")
        if os.name == "nt" and existing is not None:
            _copy_windows_security(path, parent / name)
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
