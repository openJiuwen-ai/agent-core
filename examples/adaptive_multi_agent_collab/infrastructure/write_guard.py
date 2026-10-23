"""Accidental-write guard for the experiment's Python processes.

This is not a security sandbox: Python audit hooks do not mediate arbitrary
native code or subprocesses. Configure every child process independently.
Audit event layouts follow Python 3.13's audit_events table. ``open`` omits
``dir_fd`` and metadata/link events omit ``follow_symlinks``; narrow wrappers
retain those arguments for the synchronous audit callback without changing
the underlying operation, its arguments or the process working directory.
"""

from __future__ import annotations

import contextvars
import functools
import os
from pathlib import Path
import stat
import sys
from typing import Any


class WriteScopeError(PermissionError):
    """A filesystem mutation cannot be established to remain in scope."""


_CONTEXT: contextvars.ContextVar[tuple[str, dict[str, Any]] | None] = contextvars.ContextVar(
    "experiment_write_arguments", default=None
)
_INSTALLED: WriteGuard | None = None


def descriptor_path(descriptor: int, *, directory: bool = False) -> Path:
    """Resolve a live descriptor, including descriptors renamed after opening.

    Darwin's F_GETPATH returns the kernel's current path. Linux /proc/self/fd
    is used only on Linux; there is no cwd fallback for an invalid descriptor.
    """
    try:
        info = os.fstat(descriptor)
        if directory and not stat.S_ISDIR(info.st_mode):
            raise WriteScopeError("dir_fd is not a directory")
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            result = Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        elif sys.platform.startswith("linux"):
            result = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        else:
            raise WriteScopeError("descriptor paths are unsupported on this platform")
        if not result.is_absolute():
            raise WriteScopeError("descriptor has no absolute filesystem path")
        current = result.lstat() if stat.S_ISLNK(info.st_mode) else result.stat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise WriteScopeError("descriptor path no longer identifies its inode")
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise WriteScopeError("filesystem descriptor could not be verified") from exc


class WriteGuard:
    """Check mutation paths with the operation's actual descriptor semantics."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("write scope must be an existing directory")

    def path(
        self, value: Any, dir_fd: int | None = None, *, follow: bool = True
    ) -> Path:
        if isinstance(value, int):
            return descriptor_path(value)
        text = os.fsdecode(value)
        path = Path(text)
        if not path.is_absolute():
            # CPython uses -1 to represent omitted dir_fd in audit arguments.
            base = Path.cwd() if dir_fd in (None, -1) else descriptor_path(dir_fd, directory=True)
            path = base / path
        # Removing/renaming a symlink modifies its directory entry, not its target.
        # Intermediate symlinks must still resolve, including a trailing '..'.
        if follow or text.endswith(os.sep) or path.name in ("", ".", ".."):
            return path.resolve()
        return path.parent.resolve() / path.name

    def check(self, value: Any, dir_fd: int | None = None, *, follow: bool = True) -> Path:
        path = self.path(value, dir_fd, follow=follow)
        if not path.is_relative_to(self.root):
            raise WriteScopeError("experiment refused an out-of-scope filesystem write")
        return path

    @staticmethod
    def _arguments(event: str) -> dict[str, Any]:
        context = _CONTEXT.get()
        return context[1] if context is not None and context[0] == event else {}

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            value, mode, flags = args
            if not ((mode and any(char in mode for char in "wax+")) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )):
                return
            if isinstance(value, int):
                # Pipes/sockets are process I/O, not filesystem mutations. A
                # regular-file descriptor still requires an in-scope path.
                info = os.fstat(value)
                if stat.S_ISFIFO(info.st_mode) or stat.S_ISSOCK(info.st_mode):
                    return
            self.check(value, self._arguments(event).get("dir_fd"))
        elif event == "os.mkdir":
            self.check(args[0], args[2], follow=False)
        elif event in ("os.remove", "os.rmdir"):
            self.check(args[0], args[1], follow=False)
        elif event == "os.rename":
            self.check(args[0], args[2], follow=False)
            self.check(args[1], args[3], follow=False)
        elif event == "os.link":
            self.check(args[0], args[2], follow=self._arguments(event).get("follow_symlinks", True))
            self.check(args[1], args[3], follow=False)
        elif event == "os.symlink":
            # src is the stored link text, not a mutated path. Future writes
            # through the symlink independently resolve and validate the target.
            self.check(args[1], args[2], follow=False)
        elif event in ("os.chmod", "os.chown", "os.utime"):
            offset = {"os.chmod": 2, "os.chown": 3, "os.utime": 3}[event]
            self.check(args[0], args[offset], follow=self._arguments(event).get("follow_symlinks", True))
        elif event == "os.chflags":
            self.check(args[0], follow=self._arguments(event).get("follow_symlinks", True))
        elif event in ("os.truncate", "os.setxattr", "os.removexattr"):
            self.check(args[0])


def _wrap(name: str, event: str, *, follows: bool | None = None) -> None:
    original = getattr(os, name, None)
    if original is None:
        return

    @functools.wraps(original)
    def with_arguments(*args: Any, **kwargs: Any) -> Any:
        # Validate a supplied dir_fd before the event turns explicit -1 into
        # the same sentinel as omitted dir_fd. Absolute paths ignore dir_fd,
        # consistently with the underlying POSIX operation.
        value = args[0] if args else kwargs.get("path")
        if name == "open" and kwargs.get("dir_fd") is not None and not os.path.isabs(value):
            descriptor_path(kwargs["dir_fd"], directory=True)
        context = kwargs if follows is None else {**kwargs, "follow_symlinks": follows}
        token = _CONTEXT.set((event, context))
        try:
            return original(*args, **kwargs)
        finally:
            _CONTEXT.reset(token)

    setattr(os, name, with_arguments)
    # Preserve Python's capability introspection. In particular shutil chooses
    # its fd-safe rmtree path using membership in os.supports_dir_fd.
    for capability in ("supports_dir_fd", "supports_fd", "supports_follow_symlinks", "supports_effective_ids"):
        supported: set[Any] = getattr(os, capability, set())
        if original in supported:
            supported.add(with_arguments)


def install(root: str | os.PathLike[str], *, scratch: str | os.PathLike[str]) -> WriteGuard:
    """Install once, before SDK imports, with a target-local null sink."""
    global _INSTALLED
    resolved = Path(root).resolve(strict=True)
    if _INSTALLED is not None:
        if _INSTALLED.root != resolved:
            raise WriteScopeError("cannot replace an installed write scope")
        return _INSTALLED
    guard = WriteGuard(resolved)
    scratch_path = guard.check(scratch)
    if not scratch_path.is_dir():
        raise WriteScopeError("configured scratch directory must already exist")
    for name, event in (("open", "open"), ("link", "os.link"), ("chmod", "os.chmod"),
                        ("chown", "os.chown"), ("utime", "os.utime"), ("chflags", "os.chflags")):
        _wrap(name, event)
    for name, event in (("lchmod", "os.chmod"), ("lchown", "os.chown"), ("lchflags", "os.chflags")):
        _wrap(name, event, follows=False)
    sys.addaudithook(guard.audit)
    sys.dont_write_bytecode = True
    null_path = scratch_path / "python_import_null"
    null_path.touch(exist_ok=True)
    os.devnull = str(null_path)
    _INSTALLED = guard
    return guard


def deny_network(event: str, args: tuple[Any, ...]) -> None:
    """Offline verification hook; never enabled implicitly for paid execution."""
    del args
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto"}:
        raise PermissionError("network is disabled for offline execution verification")
