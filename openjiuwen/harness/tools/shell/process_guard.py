# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Hard stop for process-termination commands.

Broad kills (by name, image, or pipeline) are always refused. A precise PID
kill is allowed only when every PID was spawned by the shell layer and is
not the current process, its parent, or the sidecar.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_BROAD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:pkill|killall)\b"), "pkill/killall"),
    (re.compile(r"(?i)\btaskkill\b[^\n]*(?:/|//)(?:im|fi)\b"), "taskkill /IM or /FI"),
    (re.compile(r"(?i)\b(?:Stop-Process|spps)\b[^\n]*-(?:Name|ProcessName)\b"), "Stop-Process -Name"),
    (
        re.compile(r"(?i)\bGet-Process\b[\s\S]{0,800}\|\s*(?:Stop-Process|spps)\b"),
        "Get-Process | Stop-Process",
    ),
    (re.compile(r"(?i)\.Kill\s*\("), "Process.Kill()"),
    (
        re.compile(r"(?i)\bwmic\b[^\n]*\bprocess\b[^\n]*\b(?:delete|terminate)\b"),
        "wmic process delete",
    ),
    (re.compile(r"(?i)\bInvoke-CimMethod\b[^\n]*\bTerminate\b"), "Invoke-CimMethod Terminate"),
    (
        re.compile(r"(?i)(?:^|[\s;&|()])kill\b[^\n]*-(?:Name|ProcessName)\b"),
        "kill -Name",
    ),
    (
        re.compile(r"(?i)\b(?:Stop-Process|spps)\b(?![^\n;&|]*-Id\b)"),
        "Stop-Process without -Id",
    ),
    (re.compile(r"(?i)(?:^|[\s;&|()])kill\s+-9\s+(?:-1|1)\b"), "kill -9 -1/1"),
    (re.compile(r"(?i)(?:^|[\s;&|()])kill\s+-1\b"), "kill -1"),
)

_STOP_PROCESS = re.compile(r"(?i)\b(?:Stop-Process|spps)\b")
_TASKKILL = re.compile(r"(?i)\btaskkill\b")
_KILL = re.compile(r"(?i)(?:^|[\s;&|()])kill\b")
_STOP_IDS = re.compile(r"(?i)-Id\b\s+([0-9]+(?:\s*,\s*[0-9]+)*)")
_TASKKILL_PIDS = re.compile(r"(?i)/{1,2}pid\s+(\d+)")
_KILL_PIDS = re.compile(r"(?i)(?:^|[\s;&|()])kill\s+(?:-9\s+|-SIGKILL\s+)?(\d+(?:\s+\d+)*)")

_SIDECAR_PID_ENV_KEYS = (
    "OFFICE_CLAW_SIDECAR_PID",
    "JIUWEN_SIDECAR_PID",
    "RELAYCLAW_SIDECAR_PID",
)


@dataclass(frozen=True, slots=True)
class SecurityCheck:
    """Result of a process-termination check."""

    blocked: bool
    reason: str | None = None


def protected_pids() -> set[int]:
    """Current process, parent, and sidecar PIDs. These are never killable."""
    pids = {os.getpid(), os.getppid(), 0}
    for key in _SIDECAR_PID_ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if raw.isdigit():
            pids.add(int(raw))
    return pids


def registered_child_pids() -> set[int]:
    """PIDs spawned by the shell layer that are still running."""
    from openjiuwen.core.sys_operation.shell_process_registry import live_spawned_pids

    return live_spawned_pids()


def check_process_termination(
        command: str,
        *,
        allowed_pids: set[int] | None = None,
        protected: set[int] | None = None,
) -> SecurityCheck:
    """Refuse broad kills and PID kills outside the tool-registered child set."""
    text = command or ""
    for pattern, label in _BROAD_PATTERNS:
        if pattern.search(text):
            return SecurityCheck(
                blocked=True,
                reason=f"refusing broad process termination: {label}",
            )

    has_terminator, pids = _precise_pids(text)
    if not has_terminator:
        return SecurityCheck(blocked=False)
    if not pids:
        return SecurityCheck(
            blocked=True,
            reason="refusing process termination without an explicit PID",
        )

    allowed = registered_child_pids() if allowed_pids is None else allowed_pids
    guarded = protected_pids() if protected is None else protected
    for pid in pids:
        if pid in guarded or pid <= 0:
            return SecurityCheck(
                blocked=True,
                reason=(
                    f"refusing to terminate protected pid {pid} "
                    "(current process, parent, or sidecar)"
                ),
            )
        if pid not in allowed:
            return SecurityCheck(
                blocked=True,
                reason=(
                    f"refusing to terminate pid {pid}; "
                    "only tool-registered child processes may be stopped"
                ),
            )
    return SecurityCheck(blocked=False)


def _precise_pids(command: str) -> tuple[bool, list[int]]:
    has = False
    pids: list[int] = []
    if _STOP_PROCESS.search(command):
        has = True
        for group in _STOP_IDS.findall(command):
            pids.extend(int(part) for part in group.split(",") if part.strip())
    if _TASKKILL.search(command):
        has = True
        pids.extend(int(part) for part in _TASKKILL_PIDS.findall(command))
    if _KILL.search(command) and not _TASKKILL.search(command):
        has = True
        for group in _KILL_PIDS.findall(command):
            pids.extend(int(part) for part in group.split() if part.strip())
    return has, pids
