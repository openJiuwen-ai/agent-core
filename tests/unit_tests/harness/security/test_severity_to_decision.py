# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Severity → action resolution under ``permission_mode`` (spec S_08 contract).

Locks the spec table so neither ``_apply_product_p1_rule_actions`` (jiuwenswarm
compose path) nor ``_fill_legacy_host_rule_actions`` (legacy host path) can
regress to a mode-blind constant.
"""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.permission_engine.core import (
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
    severity_to_decision,
)


@pytest.mark.parametrize(
    "permission_mode,severity,expected",
    [
        ("normal", "LOW", "allow"),
        ("normal", "MEDIUM", "allow"),
        ("normal", "HIGH", "ask"),
        ("normal", "CRITICAL", "ask"),
        ("strict", "LOW", "allow"),
        ("strict", "MEDIUM", "ask"),
        ("strict", "HIGH", "ask"),
        ("strict", "CRITICAL", "deny"),
    ],
)
def test_severity_to_decision_matches_spec(
    permission_mode: str, severity: str, expected: str,
) -> None:
    assert severity_to_decision(severity, permission_mode) == expected


@pytest.mark.parametrize(
    "permission_mode,severity,expected",
    [
        ("normal", "low", "allow"),
        ("normal", " medium ", "allow"),
        ("strict", "critical", "deny"),
    ],
)
def test_severity_to_decision_normalizes_case_and_whitespace(
    permission_mode: str, severity: str, expected: str,
) -> None:
    assert severity_to_decision(severity, permission_mode) == expected


@pytest.mark.parametrize("severity", ["", None])
def test_severity_to_decision_returns_none_when_severity_empty(severity) -> None:
    """Empty severity = caller leaves rule's action alone."""
    assert severity_to_decision(severity, "normal") is None
    assert severity_to_decision(severity, "strict") is None


def test_severity_to_decision_unknown_severity_fails_safe_to_ask() -> None:
    assert severity_to_decision("HIGH_PRIORITY", "normal") == "ask"
    assert severity_to_decision("HIGH_PRIORITY", "strict") == "ask"


@pytest.mark.parametrize("permission_mode", [None, "", "  ", "WEIRD"])
def test_severity_to_decision_defaults_to_normal_when_mode_unknown(
    permission_mode,
) -> None:
    assert severity_to_decision("MEDIUM", permission_mode) == "allow"
    assert severity_to_decision("CRITICAL", permission_mode) == "ask"


def test_prepare_permissions_for_engine_honors_permission_mode_strict() -> None:
    cfg = prepare_permissions_for_engine({
        "enabled": True,
        "permission_mode": "strict",
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [
            {"id": "r_med", "tools": ["bash"], "pattern": "re:foo", "severity": "MEDIUM"},
            {"id": "r_crit", "tools": ["bash"], "pattern": "re:bar", "severity": "CRITICAL"},
        ],
    })
    by_id = {r["id"]: r for r in cfg["rules"] if r.get("id") in {"r_med", "r_crit"}}
    assert by_id["r_med"]["action"] == "ask"
    assert by_id["r_crit"]["action"] == "deny"


def test_prepare_permissions_for_engine_honors_permission_mode_normal() -> None:
    cfg = prepare_permissions_for_engine({
        "enabled": True,
        "permission_mode": "normal",
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [
            {"id": "r_med", "tools": ["bash"], "pattern": "re:foo", "severity": "MEDIUM"},
            {"id": "r_crit", "tools": ["bash"], "pattern": "re:bar", "severity": "CRITICAL"},
        ],
    })
    by_id = {r["id"]: r for r in cfg["rules"] if r.get("id") in {"r_med", "r_crit"}}
    assert by_id["r_med"]["action"] == "allow"
    assert by_id["r_crit"]["action"] == "ask"


def test_prepare_permissions_for_engine_default_mode_is_normal() -> None:
    cfg = prepare_permissions_for_engine({
        "enabled": True,
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [
            {"id": "r_med", "tools": ["bash"], "pattern": "re:foo", "severity": "MEDIUM"},
        ],
    })
    rule = next(r for r in cfg["rules"] if r.get("id") == "r_med")
    assert rule["action"] == "allow"


def test_prepare_permissions_for_engine_preserves_explicit_action() -> None:
    """Explicit ``action`` always wins; severity mapping only fills missing action."""
    cfg = prepare_permissions_for_engine({
        "enabled": True,
        "permission_mode": "strict",
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [
            {
                "id": "explicit_low_with_action",
                "tools": ["bash"],
                "pattern": "re:foo",
                "severity": "LOW",
                "action": "ask",
            },
        ],
    })
    rule = next(r for r in cfg["rules"] if r.get("id") == "explicit_low_with_action")
    assert rule["action"] == "ask"