# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.harness.security.permission_engine.core import (
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.severity_mapping import (
    normalize_permission_mode,
    severity_to_action,
    severity_to_decision,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
    evaluate_tiered_policy,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("normal", "normal"),
        ("STRICT", "strict"),
        ("", "normal"),
        (None, "normal"),
        ("yolo", "normal"),
        (1, "normal"),
    ],
)
def test_normalize_permission_mode(raw, expected) -> None:
    assert normalize_permission_mode(raw) == expected


@pytest.mark.parametrize(
    ("severity", "mode", "expected"),
    [
        ("LOW", "normal", "allow"),
        ("MEDIUM", "normal", "allow"),
        ("HIGH", "normal", "ask"),
        ("CRITICAL", "normal", "ask"),
        ("LOW", "strict", "allow"),
        ("MEDIUM", "strict", "ask"),
        ("HIGH", "strict", "ask"),
        ("CRITICAL", "strict", "deny"),
        ("UNKNOWN", "normal", "ask"),
        ("", "strict", None),
        (None, "strict", None),
    ],
)
def test_severity_to_action_table(severity, mode, expected) -> None:
    assert severity_to_action(severity, mode) == expected


def test_severity_to_decision_returns_permission_level() -> None:
    assert severity_to_decision("MEDIUM", "strict") == PermissionLevel.ASK
    assert severity_to_decision("", "strict") is None


def test_prepare_permissions_maps_medium_to_ask_under_strict() -> None:
    cfg = prepare_permissions_for_engine(
        {
            "enabled": True,
            "permission_mode": "strict",
            "package_builtin_rules": False,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [
                {
                    "id": "pwd_medium",
                    "tools": ["bash"],
                    "pattern": "pwd *",
                    "severity": "MEDIUM",
                },
                {
                    "id": "ls_medium",
                    "tools": ["bash"],
                    "pattern": "ls *",
                    "severity": "MEDIUM",
                },
            ],
        }
    )
    by_id = {r["id"]: r for r in cfg["rules"]}
    assert by_id["pwd_medium"]["action"] == "ask"
    assert by_id["ls_medium"]["action"] == "ask"

    for command in ("pwd", "ls"):
        level, matched = evaluate_tiered_policy(
            cfg, "bash", {"command": command},
        )
        assert level == PermissionLevel.ASK
        assert "medium" in matched


def test_prepare_permissions_keeps_explicit_action_under_strict() -> None:
    cfg = prepare_permissions_for_engine(
        {
            "enabled": True,
            "permission_mode": "strict",
            "package_builtin_rules": False,
            "tools": {"bash": "allow"},
            "rules": [
                {
                    "id": "explicit_allow",
                    "tools": ["bash"],
                    "pattern": "pwd *",
                    "severity": "MEDIUM",
                    "action": "allow",
                }
            ],
        }
    )
    assert cfg["rules"][0]["action"] == "allow"
    level, _ = evaluate_tiered_policy(cfg, "bash", {"command": "pwd"})
    assert level == PermissionLevel.ALLOW


def test_prepare_permissions_maps_medium_to_allow_under_normal() -> None:
    cfg = prepare_permissions_for_engine(
        {
            "enabled": True,
            "permission_mode": "normal",
            "package_builtin_rules": False,
            "tools": {"bash": "allow"},
            "rules": [
                {
                    "id": "pwd_medium",
                    "tools": ["bash"],
                    "pattern": "pwd *",
                    "severity": "MEDIUM",
                }
            ],
        }
    )
    assert cfg["rules"][0]["action"] == "allow"
    level, matched = evaluate_tiered_policy(cfg, "bash", {"command": "pwd"})
    assert level == PermissionLevel.ALLOW
    assert "pwd_medium" in matched
