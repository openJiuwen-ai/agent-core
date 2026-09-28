# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Broad process-kill commands are denied for powershell and bash."""

from __future__ import annotations

import os

import pytest

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy
from openjiuwen.harness.tools.shell.process_guard import check_process_termination

_CFG = {
    "permission_mode": "normal",
    "defaults": {"*": "allow"},
    "rules": [],
    "tools": {},
}

_BROAD = [
    'Stop-Process -Name python -Force -ErrorAction SilentlyContinue',
    'Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force',
    'taskkill /IM python.exe /F',
    'taskkill //F //IM python.exe',
    'pkill -f flask',
    'killall python',
]


@pytest.mark.parametrize("command", _BROAD)
@pytest.mark.parametrize("tool_name", ["powershell", "bash"])
def test_broad_process_kill_is_denied(tool_name: str, command: str) -> None:
    level, rule = evaluate_tiered_policy(_CFG, tool_name, {"command": command})
    assert level == PermissionLevel.DENY
    assert "shell_broad_process_termination" in rule


def test_precise_pid_is_not_denied_by_builtin_rule() -> None:
    command = "Stop-Process -Id 4242 -Force -ErrorAction SilentlyContinue"
    level, rule = evaluate_tiered_policy(_CFG, "powershell", {"command": command})
    assert level != PermissionLevel.DENY
    assert "shell_broad_process_termination" not in rule


def test_get_process_query_stays_allowed() -> None:
    level, _rule = evaluate_tiered_policy(
        _CFG, "powershell", {"command": "Get-Process -Name python"},
    )
    assert level == PermissionLevel.ALLOW


def test_tool_layer_blocks_broad_kill_without_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENJIUWEN_BASH_STRICT", raising=False)
    result = check_process_termination("Stop-Process -Name python -Force")
    assert result.blocked
    assert result.reason


def test_tool_layer_rejects_protected_and_unregistered_pids() -> None:
    current = os.getpid()
    protected = check_process_termination(
        f"Stop-Process -Id {current} -Force",
        allowed_pids={current},
        protected={current, os.getppid()},
    )
    assert protected.blocked
    assert "protected" in (protected.reason or "")

    stranger = check_process_termination(
        "taskkill /PID 4242 /F",
        allowed_pids=set(),
        protected={current},
    )
    assert stranger.blocked
    assert "4242" in (stranger.reason or "")


def test_tool_layer_allows_registered_child_pid() -> None:
    result = check_process_termination(
        "Stop-Process -Id 4242 -Force",
        allowed_pids={4242},
        protected={os.getpid()},
    )
    assert not result.blocked


def test_incident_pipeline_is_blocked_by_tool_layer() -> None:
    command = (
        'Get-Process -Name python -ErrorAction SilentlyContinue | '
        'Stop-Process -Force; Start-Sleep -Seconds 1'
    )
    result = check_process_termination(command, allowed_pids={4242}, protected=set())
    assert result.blocked
