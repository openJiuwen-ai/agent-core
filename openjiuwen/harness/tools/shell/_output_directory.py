# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Choose a shell output location within the local filesystem sandbox."""

from __future__ import annotations

import errno
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator
from uuid import uuid4

from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.cwd import get_cwd, get_project_root, get_workspace

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation import SysOperation


def get_output_root(operation: SysOperation | None) -> Path | None:
    """Return a sandbox-contained output root, or the legacy temp-root default."""
    if operation is None or operation.mode != OperationMode.LOCAL:
        return None
    config = operation.work_config
    if config is None or not config.restrict_to_sandbox:
        return None

    preferred_roots = [get_workspace(), get_cwd(), get_project_root()]
    sandbox_roots = config.sandbox_root or preferred_roots
    resolved_roots = [Path(root).expanduser().resolve() for root in sandbox_roots if root is not None]
    for root in [*preferred_roots, *resolved_roots]:
        if root is None:
            continue
        output_root = (Path(root).expanduser() / ".outputs").resolve()
        if any(output_root.is_relative_to(allowed_root) for allowed_root in resolved_roots):
            return output_root
    raise PermissionError("No shell output directory is available within the configured sandbox")


@contextmanager
def _hold_windows_directory(path: Path) -> Iterator[None]:
    """Reject reparse points and block directory mutation until the handle closes."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    # FILE_SHARE_READ denies write handles used to set reparse points and delete/rename access.
    share_read = 0x00000001
    # FILE_LIST_DIRECTORY makes the handle participate in sharing checks.
    list_directory = 0x00000001
    # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT opens the directory itself.
    directory_flags = 0x02000000 | 0x00200000
    handle = create_file(str(path), list_directory, share_read, None, 3, directory_flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = path.lstat()
        if not stat.S_ISDIR(entry.st_mode) or entry.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PermissionError(f"Output directory {path} must be a real directory, not a symlink or junction")
        yield
    finally:
        close_handle(handle)


@contextmanager
def _open_posix_directory(path: Path | str, flags: int, *, dir_fd: int | None = None) -> Iterator[int]:
    """Keep directory acquisition and release paired across every exit path."""
    descriptor = os.open(path, flags, 0o700, dir_fd=dir_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def open_output_directory(directory: Path) -> Iterator[int | None]:
    """Create and anchor each directory component without following links."""
    directory = Path(os.path.abspath(directory))
    with ExitStack() as stack:
        current = Path(directory.anchor)
        directory_fd = None
        if os.name == "nt":
            stack.enter_context(_hold_windows_directory(current))
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = stack.enter_context(_open_posix_directory(current, flags))

        for component in directory.parts[1:]:
            current = current / component
            try:
                if directory_fd is None:
                    os.mkdir(current, 0o700)
                else:
                    os.mkdir(component, 0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            if os.name == "nt":
                stack.enter_context(_hold_windows_directory(current))
            else:
                try:
                    directory_fd = stack.enter_context(_open_posix_directory(component, flags, dir_fd=directory_fd))
                except OSError as error:
                    if error.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise PermissionError(
                            f"Output directory {current} must be a real directory, not a symlink"
                        ) from error
                    raise
        entry = os.fstat(directory_fd) if directory_fd is not None else directory.lstat()
        getuid = getattr(os, "getuid", None)
        if getuid is not None and entry.st_uid != getuid():
            raise PermissionError(f"Output directory {directory} must be owned by this account")
        yield directory_fd


def _replace_windows_output_file(source: Path, destination: Path) -> None:
    """Rename within the guarded parent without opening another directory writer."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    ntdll = ctypes.WinDLL("ntdll")
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG,
    ]
    set_information.restype = wintypes.LONG
    error_code = ntdll.RtlNtStatusToDosError
    error_code.argtypes = [wintypes.LONG]
    error_code.restype = wintypes.ULONG

    class IOStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    name = destination.name

    class RenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace", ctypes.c_ubyte), ("root", wintypes.HANDLE),
            ("length", wintypes.ULONG), ("name", wintypes.WCHAR * (len(name) + 1)),
        ]

    # DELETE access permits rename. OPEN_REPARSE_POINT keeps the source entry itself.
    handle = create_file(str(source), 0x00010000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = RenameInformation(True, None, len(name.encode("utf-16-le")), name)
        io_status = IOStatus()
        # FileRenameInformation (10), with a simple name and NULL root, stays in the same parent.
        status = set_information(handle, ctypes.byref(io_status), ctypes.byref(information),
                                 ctypes.sizeof(information), 10)
        if status < 0:
            raise ctypes.WinError(error_code(status))
    finally:
        close_handle(handle)


def write_output_file(directory: Path, filename: str, content: bytes) -> Path:
    """Publish complete output atomically through an anchored directory."""
    path = directory / filename
    temporary_name = f".{filename}.{uuid4().hex}.tmp"
    with open_output_directory(directory) as directory_fd:
        directory_arguments = {"dir_fd": directory_fd} if directory_fd is not None else {}
        target = filename if directory_fd is not None else path
        temporary = temporary_name if directory_fd is not None else directory / temporary_name
        try:
            entry = os.stat(target, follow_symlinks=False, **directory_arguments)
        except FileNotFoundError:
            entry = None
        if entry is not None and (
            not stat.S_ISREG(entry.st_mode)
            or getattr(entry, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise PermissionError(f"Output file {path} must be a regular file, not a symlink")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600, **directory_arguments)
        try:
            stream = os.fdopen(descriptor, "wb")
            descriptor = None
            with stream:
                stream.write(content)
                file_entry = os.fstat(stream.fileno())
            if directory_fd is None:
                _replace_windows_output_file(temporary, target)
            else:
                os.replace(temporary, target, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            published_entry = path.lstat()
            if path.resolve() != path or (
                published_entry.st_dev, published_entry.st_ino
            ) != (file_entry.st_dev, file_entry.st_ino):
                raise PermissionError("Output path changed during persistence")
            return path
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, **directory_arguments)
            except FileNotFoundError:
                pass
