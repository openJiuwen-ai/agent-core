# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Track shell execution ownership, including descendants of completed commands."""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import os
import signal
import subprocess
import threading
from contextvars import Token
from dataclasses import dataclass
from typing import Any, Union

from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.sys_operation._process_owner import ProcessOwner

ProcessHandle = Union[subprocess.Popen[Any], asyncio.subprocess.Process]

_shell_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "shell_session_id",
    default=None,
)


def set_shell_session_id(session_id: str | None) -> Token[str | None]:
    """Bind the conversation session id for subsequent shell executions in this context."""
    return _shell_session_id.set(session_id)


def reset_shell_session_id(token: Token[str | None]) -> None:
    _shell_session_id.reset(token)


def get_shell_session_id() -> str | None:
    return _shell_session_id.get()


def resolve_shell_session_id() -> str | None:
    """Resolve session id for shell process tracking."""
    sid = (get_shell_session_id() or "").strip()
    if sid:
        return sid
    try:
        from openjiuwen.core.common.logging.utils import get_session_id
    except ImportError:
        return None

    trace = (get_session_id() or "").strip()
    if trace and trace != "default_trace_id":
        return trace
    return None


@dataclass(eq=False)
class PendingShellSpawn:
    """Cancellation ticket spanning asynchronous process creation."""

    session_id: str | None
    cancelled: bool = False


class ShellProcessRegistry:
    """Session-scoped ownership; command completion does not stop preview servers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[str, set[ProcessHandle]] = {}
        self._cancelled_sessions: set[str] = set()
        self._owners: dict[ProcessHandle, ProcessOwner] = {}
        self._pending: set[PendingShellSpawn] = set()
        self._reaper: threading.Thread | None = None

    def reserve(self, session_id: str | None) -> PendingShellSpawn:
        ticket = PendingShellSpawn(session_id)
        with self._lock:
            self._pending.add(ticket)
        return ticket

    def finish_spawn(self, ticket: PendingShellSpawn) -> None:
        with self._lock:
            self._pending.discard(ticket)

    def adopt(self, ticket: PendingShellSpawn, proc: ProcessHandle, owner: ProcessOwner | None) -> None:
        with self._lock:
            if ticket.cancelled:
                raise asyncio.CancelledError("Session stopped while its shell command was starting")
            if owner is not None:
                self._owners[proc] = owner
            if ticket.session_id:
                self._processes.setdefault(ticket.session_id, set()).add(proc)
            # Windows commands are still suspended. Make starting and stopping
            # a session mutually exclusive, including the spawn/registration gap.
            if owner is not None:
                owner.start()
                if self._reaper is None:
                    self._reaper = threading.Thread(target=self._reap_loop, name="jiuwen-shell-reaper", daemon=True)
                    self._reaper.start()
            self._pending.discard(ticket)

    def _forget_locked(self, proc: ProcessHandle) -> None:
        self._owners.pop(proc, None)
        for sid, processes in list(self._processes.items()):
            processes.discard(proc)
            if not processes:
                self._processes.pop(sid, None)

    def reap_completed(self) -> None:
        """Release empty ownership handles without killing live background services."""
        with self._lock:
            entries = list(self._owners.items())
        for proc, owner in entries:
            try:
                if not owner.is_empty():
                    continue
                owner.close()
            except (OSError, subprocess.SubprocessError) as exc:
                sys_operation_logger.warning("Could not inspect shell execution %s: %s", proc.pid, exc)
                continue
            with self._lock:
                if self._owners.get(proc) is owner:
                    self._forget_locked(proc)

    def _reap_loop(self) -> None:
        while True:
            threading.Event().wait(1)
            self.reap_completed()
            with self._lock:
                if not self._owners:
                    self._reaper = None
                    return

    def terminate(self, proc: ProcessHandle) -> bool:
        with self._lock:
            owner = self._owners.get(proc)
        if owner is None:
            killed = _terminate_unowned_process(proc)
        else:
            try:
                killed = not owner.is_empty()
            except (OSError, subprocess.SubprocessError) as exc:
                # Inspection is advisory. An explicit stop must still release
                # the OS ownership handle even if process enumeration failed.
                sys_operation_logger.warning("Could not inspect shell execution %s before stop: %s", proc.pid, exc)
                killed = True
            try:
                owner.close()
            except (OSError, subprocess.SubprocessError) as exc:
                # Keep ownership available for a later cleanup attempt.
                sys_operation_logger.warning("Could not terminate shell execution %s: %s", proc.pid, exc)
                return False
        with self._lock:
            self._forget_locked(proc)
        return killed

    def close(self) -> None:
        """Release all managed executions when their SDK process shuts down."""
        with self._lock:
            for ticket in self._pending:
                ticket.cancelled = True
            processes = set(self._owners)
            for bucket in self._processes.values():
                processes.update(bucket)
        for proc in processes:
            self.terminate(proc)

    def register(self, session_id: str, proc: ProcessHandle) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._processes.setdefault(sid, set()).add(proc)

    def unregister(self, session_id: str, proc: ProcessHandle) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            if proc in self._owners:
                # Reap only once the entire owned group, not just its parent,
                # has exited. Background previews remain usable between tools.
                return
            bucket = self._processes.get(sid)
            if bucket is None:
                return
            bucket.discard(proc)
            if not bucket:
                self._processes.pop(sid, None)

    def kill_session(self, session_id: str) -> int:
        sid = (session_id or "").strip()
        if not sid:
            return 0
        with self._lock:
            self._cancelled_sessions.add(sid)
            for ticket in self._pending:
                if ticket.session_id == sid:
                    ticket.cancelled = True
            procs = list(self._processes.get(sid, set()))
        killed = 0
        for proc in procs:
            if self.terminate(proc):
                killed += 1
        return killed

    def kill_session_tree(self, session_id: str) -> int:
        """Kill tracked shell processes for *session_id* and child session keys."""
        sid = (session_id or "").strip()
        if not sid:
            return 0
        prefix = f"{sid}_"
        with self._lock:
            self._cancelled_sessions.add(sid)
            for ticket in self._pending:
                if ticket.session_id and (ticket.session_id == sid or ticket.session_id.startswith(prefix)):
                    ticket.cancelled = True
            matching_keys = [
                key for key in list(self._processes)
                if key == sid or key.startswith(prefix)
            ]
            for key in matching_keys:
                self._cancelled_sessions.add(key)
            procs = set().union(*(self._processes[key] for key in matching_keys))
        killed = 0
        for proc in procs:
            if self.terminate(proc):
                killed += 1
        return killed

    def consume_cancelled(self, session_id: str) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._lock:
            if sid not in self._cancelled_sessions:
                return False
            self._cancelled_sessions.discard(sid)
            return True


SHELL_PROCESS_REGISTRY = ShellProcessRegistry()
atexit.register(SHELL_PROCESS_REGISTRY.close)


def reserve_shell_spawn() -> PendingShellSpawn:
    return SHELL_PROCESS_REGISTRY.reserve(resolve_shell_session_id())


def adopt_shell_process(ticket: PendingShellSpawn, proc: ProcessHandle, owner: ProcessOwner | None) -> None:
    SHELL_PROCESS_REGISTRY.adopt(ticket, proc, owner)


def finish_shell_spawn(ticket: PendingShellSpawn) -> None:
    SHELL_PROCESS_REGISTRY.finish_spawn(ticket)


def register_shell_process(session_id: str, proc: ProcessHandle) -> None:
    SHELL_PROCESS_REGISTRY.register(session_id, proc)


def unregister_shell_process(session_id: str, proc: ProcessHandle) -> None:
    SHELL_PROCESS_REGISTRY.unregister(session_id, proc)


def kill_shell_processes_for_session(session_id: str) -> int:
    """Kill all tracked shell processes for *session_id*."""
    return SHELL_PROCESS_REGISTRY.kill_session(session_id)


def kill_shell_processes_for_session_tree(session_id: str) -> int:
    """Kill shell processes for *session_id* and sub-agent session keys."""
    return SHELL_PROCESS_REGISTRY.kill_session_tree(session_id)


def consume_shell_session_cancelled(session_id: str) -> bool:
    return SHELL_PROCESS_REGISTRY.consume_cancelled(session_id)


def terminate_shell_process(proc: ProcessHandle) -> bool:
    return SHELL_PROCESS_REGISTRY.terminate(proc)


def _terminate_unowned_process(proc: ProcessHandle) -> bool:
    if isinstance(proc, asyncio.subprocess.Process):
        if proc.returncode is not None:
            return False
        pid = proc.pid
        if pid is None:
            return False
        try:
            if os.name != "nt" and os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            try:
                proc.kill()
            except ProcessLookupError:
                return False
        return True

    if proc.poll() is not None:
        return False
    try:
        if os.name != "nt" and os.getpgid(proc.pid) == proc.pid:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        return False
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt" and os.getpgid(proc.pid) == proc.pid:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except OSError as e:
            sys_operation_logger.warning(f"Failed to kill shell process {proc.pid}: {e}")
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired as e:
            sys_operation_logger.warning(
                f"Timeout expired waiting for shell process {proc.pid} termination: {e}"
            )
    return True
