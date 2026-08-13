# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Findings severity：简单管道/复合命令不应抬 ASK。"""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.findings import (
    escalate_with_findings,
    scan_shell_findings,
)
from openjiuwen.harness.security.models import PermissionLevel


def test_simple_pipeline_finding_is_info_not_medium() -> None:
    findings = scan_shell_findings("Get-ChildItem | Select-Object Name")
    assert findings
    assert all(f.severity == "INFO" for f in findings)
    assert any(f.reason == "shell_simple_compound" for f in findings)


def test_simple_and_compound_finding_is_info() -> None:
    findings = scan_shell_findings("echo a && echo b")
    assert findings
    assert all(f.severity == "INFO" for f in findings)


def test_redirection_finding_stays_medium() -> None:
    findings = scan_shell_findings("echo hi > out.txt")
    assert any(f.severity == "MEDIUM" and f.reason == "shell_risky_structure" for f in findings)


def test_info_findings_do_not_escalate_in_strict() -> None:
    findings = scan_shell_findings("ls | wc -l")
    assert findings
    assert (
        escalate_with_findings(PermissionLevel.ALLOW, findings, mode="strict")
        == PermissionLevel.ALLOW
    )


def test_maybe_escalate_keeps_allow_for_simple_pipeline() -> None:
    from openjiuwen.harness.security.tiered_policy import maybe_escalate_shell_operators

    assert (
        maybe_escalate_shell_operators(
            "powershell",
            {"command": "Get-ChildItem | Select-Object Name"},
            PermissionLevel.ALLOW,
        )
        == PermissionLevel.ALLOW
    )


def test_maybe_escalate_asks_for_redirection() -> None:
    from openjiuwen.harness.security.tiered_policy import maybe_escalate_shell_operators

    assert (
        maybe_escalate_shell_operators(
            "bash",
            {"command": "echo hi > out.txt"},
            PermissionLevel.ALLOW,
        )
        == PermissionLevel.ASK
    )


@pytest.mark.asyncio
async def test_engine_simple_pipeline_keeps_allow_under_strict_defaults_allow() -> None:
    """defaults allow 时，单纯管道不应被 findings 抬成 ASK。"""
    cfg = {
        "enabled": True,
        "mode": "strict",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "approval_overrides": [],
    }
    engine = PermissionEngine(cfg)
    result = await engine.check_permission(
        "powershell",
        {"command": "Get-ChildItem | Select-Object Name"},
    )
    assert result.permission == PermissionLevel.ALLOW
    assert result.findings  # 仍可展示 INFO
