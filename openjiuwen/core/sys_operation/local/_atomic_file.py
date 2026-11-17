# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish a complete file atomically for concurrent readers.

This prevents partial visibility during normal operation, not data loss after
power failure or kernel crash: file and parent directory are not fsynced.
"""

import asyncio
import errno
import os
import secrets
import stat
import sys
from pathlib import Path

from openjiuwen.core.common.logging import sys_operation_logger

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_GROUP_SECURITY_INFORMATION = 0x00000002
_DACL_SECURITY_INFORMATION = 0x00000004
_FILE_SECURITY_INFORMATION = _OWNER_SECURITY_INFORMATION | _GROUP_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SE_DACL_PROTECTED = 0x1000
_SE_DACL_AUTO_INHERITED = 0x0400
_SDDL_REVISION_1 = 1
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_WINDOWS_MISSING_TARGET_ERRORS = frozenset({_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND})


def _winerror(error: BaseException) -> int | None:
    """Return the Win32 code carried by an OS error, if any."""
    code = getattr(error, "winerror", None)
    if isinstance(code, int):
        return code
    code = getattr(error, "errno", None)
    return code if isinstance(code, int) else None


def _windows_error(code: int, message: str) -> OSError:
    """Build an OSError that keeps the Win32 code available to callers."""
    error = OSError(code, message)
    try:
        error.winerror = code
    except (AttributeError, TypeError):  # pragma: no cover - POSIX OSError without winerror
        pass
    return error


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


def _copy_windows_security(source: Path, destination: Path, information: int = _FILE_SECURITY_INFORMATION) -> None:
    """Apply owner/group/DACL and its inheritance policy before publication.

    ``information`` narrows the copied parts. The DACL is what gates access, so a
    DACL-only copy keeps the target access policy when owner/group cannot be set.
    """
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
    requested = information
    error = get(
        str(source),
        _SE_FILE_OBJECT,
        requested,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        None,  # SACL auditing information is outside the copied access policy.
        ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        get_control = api.GetSecurityDescriptorControl
        get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        get_control.restype = wintypes.BOOL
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        information |= (
            _PROTECTED_DACL_SECURITY_INFORMATION
            if control.value & _SE_DACL_PROTECTED
            else _UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        if control.value & _SE_DACL_AUTO_INHERITED:
            error = put(str(destination), _SE_FILE_OBJECT, information, owner, group, dacl, None)
            if error:
                raise ctypes.WinError(error)
        else:
            # The named getter can annotate legacy ACEs as inherited. Read the
            # raw descriptor so copying it does not change their future behavior.
            get_raw = api.GetFileSecurityW
            get_raw.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            get_raw.restype = wintypes.BOOL
            needed = wintypes.DWORD()
            get_raw(str(source), requested, None, 0, ctypes.byref(needed))
            if not needed.value:
                raise ctypes.WinError(ctypes.get_last_error())
            raw = ctypes.create_string_buffer(needed.value)
            if not get_raw(str(source), requested, raw, needed.value, ctypes.byref(needed)):
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


def _normalized_security_sddl(sddl: str) -> str:
    """Drop the DACL auto-inherited bookkeeping flag from an SDDL string.

    ReplaceFileW may set SE_DACL_AUTO_INHERITED while merging, and a freshly
    created temporary file may not carry it. The flag records whether inheritance
    has been applied, not which accesses are granted, so the owner, group and ACE
    list are what decide whether two descriptors are equivalent.
    """
    head, separator, dacl = sddl.partition("D:")
    if not separator:
        return sddl
    flags, ace_start, aces = dacl.partition("(")
    return f"{head}D:{flags.replace('AI', '')}{ace_start}{aces}"


def _windows_security_sddl(path: Path, information: int = _FILE_SECURITY_INFORMATION) -> str:
    """Read owner/group/DACL as SDDL with the inheritance bookmark normalized."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, *([ctypes.POINTER(ctypes.c_void_p)] * 5)]
    get.restype = wintypes.DWORD
    owner, group, dacl, descriptor = (ctypes.c_void_p() for _ in range(4))
    error = get(
        str(path),
        _SE_FILE_OBJECT,
        information,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if error:
        raise _windows_error(error, f"Cannot read the security descriptor of {path} (WinError {error})")
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    try:
        convert = api.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        ]
        convert.restype = wintypes.BOOL
        text, size = wintypes.LPWSTR(), wintypes.DWORD()
        if not convert(descriptor, _SDDL_REVISION_1, information, ctypes.byref(text), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return _normalized_security_sddl(text.value)
        finally:
            free(text)
    finally:
        free(descriptor)


def _windows_security_matches(source: Path, destination: Path, information: int = _FILE_SECURITY_INFORMATION) -> bool:
    """Return True only when both files carry the same owner, group and DACL."""
    try:
        return _windows_security_sddl(source, information) == _windows_security_sddl(destination, information)
    except OSError as exc:
        sys_operation_logger.debug("Cannot compare the security descriptors of %s and %s: %s", source, destination, exc)
        return False


def _copy_windows_security_or_dacl(source: Path, destination: Path) -> None:
    """Copy file security, degrading to the DACL when owner/group is denied.

    Copying OWNER/GROUP needs WRITE_OWNER access on the temporary file, which a
    parent directory ACL that grants only Modify does not provide. The DACL still
    gates access and a file owner holds implicit WRITE_DAC, so the reduced copy
    preserves the access policy; the degradation is logged and then verified so a
    partially applied descriptor cannot be published.
    """
    try:
        _copy_windows_security(source, destination)
        return
    except OSError as exc:
        sys_operation_logger.warning(
            "Cannot copy owner/group from %s to %s (WinError %s): %s",
            source,
            destination,
            _winerror(exc),
            exc,
        )
    _copy_windows_security(source, destination, _DACL_SECURITY_INFORMATION)
    if not _windows_security_matches(source, destination, _DACL_SECURITY_INFORMATION):
        raise _windows_error(
            _ERROR_ACCESS_DENIED,
            f"DACL copy from {source} to {destination} reported success but did not apply; "
            "the replacement must not be published",
        )


def _replace_file_windows(target: Path, temporary: Path) -> int:
    """Publish ``temporary`` over ``target`` with ReplaceFileW; 0 on success or WinError.

    ReplaceFileW merges the replaced file's owner/group/DACL into the replacement
    natively, so publication needs neither WRITE_OWNER nor WRITE_DAC on the
    temporary file and keeps a protected DACL intact. The REPLACEFILE_IGNORE_*
    flags are deliberately never passed: they let the call succeed without
    preserving ACLs, which would silently widen access.
    """
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace.restype = wintypes.BOOL
    if replace(str(target), str(temporary), None, 0, None, None):
        return 0
    return ctypes.get_last_error() or _ERROR_ACCESS_DENIED


def _replace_windows_target(target: Path, temporary: Path) -> None:
    """Publish an existing file without widening its owner/group/DACL boundary.

    ReplaceFileW is the only path that preserves the target security descriptor
    without WRITE_OWNER/WRITE_DAC on the temporary file. Every fallback still has
    to apply or prove the target access policy before anything is published.
    """
    error = _replace_file_windows(target, temporary)
    if not error:
        return
    sys_operation_logger.warning(
        "ReplaceFileW could not publish %s (WinError %s); trying an explicit security copy",
        target,
        error,
    )
    if error in _WINDOWS_MISSING_TARGET_ERRORS:
        # The target vanished after validation: publishing becomes a plain create,
        # so no existing security descriptor can be relaxed.
        os.replace(temporary, target)
        return
    copy_error = None
    try:
        _copy_windows_security_or_dacl(target, temporary)
    except OSError as exc:
        copy_error = exc
        sys_operation_logger.warning(
            "Cannot copy the security descriptor of %s to %s (WinError %s): %s",
            target,
            temporary,
            _winerror(exc),
            exc,
        )
    else:
        os.replace(temporary, target)
        return
    if not os.path.lexists(target):
        os.replace(temporary, target)
        return
    if _windows_security_matches(target, temporary):
        sys_operation_logger.warning(
            "Publishing %s without a security copy: the replacement already carries the target owner/group/DACL",
            target,
        )
        os.replace(temporary, target)
        return
    raise _windows_error(
        _winerror(copy_error) or error,
        f"Cannot atomically publish {target}: ReplaceFileW failed (WinError {error}) and the "
        f"target owner/group/DACL could not be applied to the replacement "
        f"(WinError {_winerror(copy_error)}); the existing file was left unchanged",
    )


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
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, mode=0o600)
            opened = os.fstat(parent_fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("File parent changed before atomic write")
        # Permission to replace a directory entry must not bypass file write access.
        try:
            target_fd = os.open(
                path.name if parent_fd is not None else path,
                os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                mode=0o600,
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
            if (
                hasattr(os, "fchown")
                and existing is not None
                and (created.st_uid, created.st_gid) != (existing.st_uid, existing.st_gid)
            ):
                os.fchown(stream.fileno(), existing.st_uid, existing.st_gid)
            if existing is not None:
                _restore_posix_access_acl(stream.fileno(), existing_acl)
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), stat.S_IMODE(existing.st_mode) if existing else int(permissions, 8))
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Unsafe atomic-write temporary file")
        current = parent.stat()
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("File parent changed during atomic write")
        if os.name == "nt" and existing is not None and not os.path.islink(path):
            _replace_windows_target(path, parent / name)
        else:
            if os.name == "nt" and existing is not None:
                # A reparse point keeps the os.replace rename semantics; only harden
                # the security copy, because ReplaceFileW follows the reparse target.
                _copy_windows_security_or_dacl(path, parent / name)
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
    settled = asyncio.gather(worker, return_exceptions=True)
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(settled)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        # Retrieve a possible worker failure before propagating cancellation.
        worker.exception()
        raise asyncio.CancelledError
    worker.result()
