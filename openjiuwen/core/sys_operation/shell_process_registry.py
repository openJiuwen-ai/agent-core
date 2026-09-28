# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Track in-flight shell subprocesses so callers can kill them on user interrupt."""

from __future__ import annotations

import asyncio
import contextvars
import os
import signal
import subprocess
import threading
from contextvars import Token
from typing import Any, Union

from openjiuwen.core.common.logging import sys_operation_logger

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


class ShellProcessRegistry:
    """Session-scoped registry of in-flight shell processes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[str, set[ProcessHandle]] = {}
        self._cancelled_sessions: set[str] = set()
        self._spawned: set[ProcessHandle] = set()

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
            procs = list(self._processes.pop(sid, set()))
        killed = 0
        for proc in procs:
            if terminate_shell_process(proc):
                killed += 1
        return killed

    def kill_session_tree(self, session_id: str) -> int:
        """Kill tracked shell processes for *session_id* and child session keys."""
        sid = (session_id or "").strip()
        if not sid:
            return 0
        prefix = f"{sid}_"
        with self._lock:
            matching_keys = [
                key for key in list(self._processes)
                if key == sid or key.startswith(prefix)
            ]
            for key in matching_keys:
                self._cancelled_sessions.add(key)
        killed = 0
        for key in matching_keys:
            with self._lock:
                procs = list(self._processes.pop(key, set()))
            for proc in procs:
                if terminate_shell_process(proc):
                    killed += 1
        return killed

    def note_spawned_process(self, proc: ProcessHandle) -> None:
        """Remember a process the shell layer started, until it exits."""
        if getattr(proc, "pid", None) is None:
            return
        with self._lock:
            self._spawned.add(proc)

    def forget_spawned_process(self, proc: ProcessHandle) -> None:
        with self._lock:
            self._spawned.discard(proc)

    def live_spawned_pids(self) -> set[int]:
        """PIDs of shell-spawned processes that are still running."""
        with self._lock:
            dead: list[ProcessHandle] = []
            pids: set[int] = set()
            for proc in self._spawned:
                pid = _pid_if_alive(proc)
                if pid is None:
                    dead.append(proc)
                else:
                    pids.add(pid)
            for proc in dead:
                self._spawned.discard(proc)
            return pids

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


def note_spawned_process(proc: ProcessHandle) -> None:
    """Record a shell-spawned process so later PID kills can recognize it."""
    SHELL_PROCESS_REGISTRY.note_spawned_process(proc)


def forget_spawned_process(proc: ProcessHandle) -> None:
    SHELL_PROCESS_REGISTRY.forget_spawned_process(proc)


def live_spawned_pids() -> set[int]:
    return SHELL_PROCESS_REGISTRY.live_spawned_pids()


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


def _pid_if_alive(proc: ProcessHandle) -> int | None:
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return None
    poll = getattr(proc, "poll", None)
    if callable(poll):
        try:
            if poll() is not None:
                return None
        except OSError:
            return None
    elif getattr(proc, "returncode", None) is not None:
        return None
    return pid


def terminate_shell_process(proc: ProcessHandle) -> bool:
    if isinstance(proc, asyncio.subprocess.Process):
        if proc.returncode is not None:
            return False
        pid = proc.pid
        if pid is None:
            return False
        try:
            if os.name != "nt":
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
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        return False
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
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
