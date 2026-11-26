# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Builtin mass-kill rules: name-based process kills are denied, PID kills allowed.

Background: 2026-11-23 CI incident — a team leader agent executed
``taskkill //F //IM python.exe`` (plus a PowerShell CommandLine kill) to clean
up its own test server; the command killed every python process on the shared
host (all shards' AS/gateway). Name-based mass kills escape any session/team
scope; PID-based kills stay allowed.
"""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
)
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy

INCIDENT_POWERSHELL_KILL = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -like '*server.py*' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)


def _cfg() -> dict:
    return inline_package_command_rules({
        "enabled": True,
        "tools": {"bash": "allow", "powershell": "allow"},
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "rules": [],
    })


def _level(command: str, tool: str = "bash"):
    return evaluate_tiered_policy(_cfg(), tool, {"command": command})[0]


@pytest.mark.parametrize("command", [
    "taskkill /F /IM python.exe",
    "taskkill //F //IM python.exe //FI \"WINDOWTITLE eq *\"",
    "taskkill /f /im python.exe",
    "C:\\Windows\\System32\\taskkill.exe /F /IM python.exe",
    "wmic process where name='python.exe' delete",
    "wmic process where \"name='python.exe'\" call terminate",
    "wmic path win32_process where name='python.exe' delete",
    "killall python",
    "pkill -9 -f server.py",
    "/usr/bin/pkill python",
    "kill -9 $(pgrep -f server.py)",
    "ps aux | grep server.py | grep -v grep | awk '{print $2}' | xargs kill -9",
    "Stop-Process -Name python -Force",
    "Get-Process python | Stop-Process",
    "Get-Process python | ForEach-Object { $_.Kill() }",
    "ps python | kill",
    INCIDENT_POWERSHELL_KILL,
])
def test_name_based_mass_kill_is_denied(command: str) -> None:
    assert _level(command) == PermissionLevel.DENY


def test_incident_powershell_kill_denied_on_powershell_tool() -> None:
    assert _level(INCIDENT_POWERSHELL_KILL, tool="powershell") == PermissionLevel.DENY


@pytest.mark.parametrize("command", [
    "taskkill /F /PID 4242 /T /F",
    "taskkill //F //PID 4242 //T //F",
    "C:\\Windows\\System32\\taskkill.exe /F /PID 4242",
    "Stop-Process -Id 4242 -Force",
    "kill -9 4242",
    "kill %1",
    "tasklist | findstr python",
    "git push origin main",
    "echo hello world",
    "grep -rn killall openjiuwen/",
    "pytest -k pkill",
    "git commit -m \"deny killall\"",
    "echo \"use xargs kill here\"",
    "Get-Process -Id 4242 | Stop-Process",
])


def test_pid_precise_and_unrelated_commands_stay_allowed(command: str) -> None:
    assert _level(command) == PermissionLevel.ALLOW


def test_pid_pipeline_method_kill_not_denied_by_mass_kill_rules() -> None:
    """PID-precise ``ForEach-Object`` Kill must not hit the mass-kill deny rules.

    The tiered policy may still return ASK via the shell-AST unknown-structure
    floor (tree-sitter parse limits on script blocks) — that is framework
    behavior outside these rules; the contract here is only ``not DENY``.
    """
    level = _level("Get-Process -Id 4242 | ForEach-Object { $_.Kill() }")
    assert level != PermissionLevel.DENY
