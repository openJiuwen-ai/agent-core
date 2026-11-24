# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OS ownership for a shell execution, independent of its command's lifetime."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol


class ProcessOwner(Protocol):
    """Own processes until explicitly stopped or the execution group is empty."""

    def spawn_options(self) -> dict[str, Any]:
        ...

    def attach(self, pid: int) -> None:
        ...

    def start(self) -> None:
        ...

    def is_empty(self) -> bool:
        ...

    def close(self) -> None:
        ...


def _posix_members(group: int) -> set[int]:
    """Return live group members; inability to inspect must not mean empty."""
    members = set()
    if sys.platform.startswith("linux"):
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    # comm may contain non-UTF-8 bytes and embedded parentheses.
                    fields = (Path(entry.path) / "stat").read_bytes().rpartition(b")")[2].split()
                    if len(fields) < 3:
                        raise ValueError("missing process state or group")
                    pid, pgid = int(entry.name), int(fields[2])
                except (FileNotFoundError, ProcessLookupError):
                    continue  # A process exited between listing and reading its status.
                except ValueError as exc:
                    # Unknown membership must not turn a partial scan into "empty".
                    raise OSError(f"Cannot inspect process {entry.name}: invalid procfs stat") from exc
                if pgid == group and fields[0] not in {b"Z", b"X"}:
                    members.add(pid)
        return members

    # macOS has no procfs. Request only fixed, numeric columns, never command text.
    try:
        output = subprocess.check_output(
            ["/bin/ps", "-axo", "pid=,pgid=,stat="], text=True, encoding="ascii", timeout=2,
        )
        if not output.strip():
            raise ValueError("ps returned no process rows")
        for line in output.splitlines():
            if not line.strip():
                continue
            pid, pgid, status = line.split()
            pid, pgid = int(pid), int(pgid)
            if pgid == group and not status.startswith(("Z", "X")):
                members.add(pid)
    except ValueError as exc:
        raise OSError("Cannot inspect process groups: invalid ps output") from exc
    return members


def wrap_posix_command(arguments: list[str]) -> list[str]:
    """Detach the command's controlling terminal without leaving its owned group."""
    # A fresh interpreter avoids running Python preexec_fn in a threaded host.
    launcher = str(Path(__file__).with_name("_posix_command.py").resolve())
    return [sys.executable, "-I", "-S", launcher, *arguments]


class PosixProcessOwner:
    """Keep a group leader unreaped so its PGID cannot be reused by another task.

    The small guardian has no inherited command pipes. EOF on its private pipe
    also cleans the group when the SDK process exits, including abrupt exits.
    This owns ordinary shell descendants, not programs that deliberately leave
    their process group (those require a sandbox or cgroup containment).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._empty_since: float | None = None
        self._guardian = subprocess.Popen(
            ["/bin/sh", "-c", (
                "trap 'trap \"\" TERM; kill -TERM -$$ 2>/dev/null; "
                "sleep 0.2; kill -KILL -$$ 2>/dev/null' 0; IFS= read -r _"
            )],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            # Commands spawned by the SDK can only join a group in its session.
            process_group=0,
        )

    def spawn_options(self) -> dict[str, Any]:
        return {"process_group": self._guardian.pid}

    @staticmethod
    def attach(pid: int) -> None:
        # Membership was set atomically by Popen before exec, even for fast commands.
        return None

    @staticmethod
    def start() -> None:
        return None

    def is_empty(self) -> bool:
        with self._lock:
            if self._closed:
                return True
            try:
                members = _posix_members(self._guardian.pid)
            except (OSError, subprocess.SubprocessError, ValueError):
                self._empty_since = None
                raise
            if members - {self._guardian.pid}:
                self._empty_since = None
                return False
            # procfs/ps enumeration is not an atomic snapshot. Require a quiet
            # interval before normal reaping so a fork/exit handoff is retained.
            now = time.monotonic()
            if self._empty_since is None:
                self._empty_since = now
            return now - self._empty_since >= 1

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            # Do not poll/wait for the guardian before signalling: its unreaped
            # PID is what makes signalling this process group safe against reuse.
            try:
                os.killpg(self._guardian.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The group is already gone; its guardian still needs reaping.
                self._guardian.wait(timeout=3)
            else:
                self._guardian.wait(timeout=3)
            if self._guardian.stdin is not None:
                self._guardian.stdin.close()
            self._closed = True


def create_process_owner() -> ProcessOwner | None:
    if os.name == "nt":
        from openjiuwen.core.sys_operation._windows_process_job import WindowsProcessJob

        return WindowsProcessJob()
    if os.getenv("JW_START_NEW_SESSION", "true").strip().lower() in {"0", "false", "no", "off"}:
        # Preserve the explicit opt-out; never signal the host's shared group.
        return None
    return PosixProcessOwner()
