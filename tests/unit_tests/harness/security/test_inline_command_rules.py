# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inline package command rules; Engine does not load YAML at check time."""

from __future__ import annotations

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import inline_package_command_rules


def test_evaluate_does_not_autoload_package_command_rules() -> None:
    cfg = {
        "enabled": True,
        "permission_mode": "normal",
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [],
    }
    level, matched = evaluate_tiered_policy(
        cfg, "bash", {"command": "rm -rf /tmp/workspace-dist"},
    )
    assert level == PermissionLevel.ALLOW
    assert "builtin" not in matched


def test_engine_inlines_package_rules_for_legacy_host_config() -> None:
    """Old swarm passes raw Global YAML; engine ingest still applies package policy."""
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    engine = PermissionEngine(
        {
            "enabled": True,
            "permission_mode": "normal",
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    builtin = [r for r in (engine.config.get("rules") or []) if r.get("layer") == "builtin"]
    assert builtin
    level, matched = evaluate_tiered_policy(
        engine.config, "bash", {"command": "shutdown -h now"},
    )
    assert level == PermissionLevel.DENY
    assert "builtin" in matched


def test_engine_does_not_duplicate_already_inlined_package_rules() -> None:
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    seeded = inline_package_command_rules(
        {
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    before = [r.get("id") for r in seeded["rules"] if r.get("layer") == "builtin"]
    engine = PermissionEngine(seeded)
    after = [r.get("id") for r in engine.config["rules"] if r.get("layer") == "builtin"]
    assert after == before


def test_engine_fills_legacy_severity_only_host_rules() -> None:
    """Old swarm passes severity-only product rules; ingest fills action."""
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    engine = PermissionEngine(
        {
            "enabled": True,
            "permission_mode": "normal",
            "tools": {"bash": "ask"},
            "defaults": {"*": "ask"},
            "rules": [
                {
                    "id": "shell_allow_ls",
                    "tools": ["bash"],
                    "pattern": "ls *",
                    "severity": "LOW",
                },
                {
                    "id": "shell_ask_rm",
                    "tools": ["bash"],
                    "pattern": "rm *",
                    "severity": "HIGH",
                },
            ],
        }
    )
    by_id = {r.get("id"): r for r in engine.config["rules"] if isinstance(r, dict)}
    assert by_id["shell_allow_ls"]["action"] == "allow"
    assert by_id["shell_ask_rm"]["action"] == "ask"
    level, matched = evaluate_tiered_policy(engine.config, "bash", {"command": "ls"})
    assert level == PermissionLevel.ALLOW
    assert "shell_allow_ls" in matched


def test_engine_does_not_override_existing_rule_action() -> None:
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [
                {
                    "id": "user_rm_explicit_deny",
                    "tools": ["bash"],
                    "match_type": "command",
                    "pattern": "re:(?i)compose-legacy-action-marker",
                    "severity": "HIGH",
                    "action": "deny",
                }
            ],
        }
    )
    host = next(
        r for r in engine.config["rules"] if r.get("id") == "user_rm_explicit_deny"
    )
    assert host["action"] == "deny"


def test_evaluate_skips_rule_without_action() -> None:
    cfg = {
        "enabled": True,
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "rules": [
            {
                "id": "user_rm_no_action",
                "tools": ["bash"],
                "match_type": "command",
                "pattern": "re:(?i)rm\\s+-rf",
                "severity": "HIGH",
            }
        ],
    }
    level, matched = evaluate_tiered_policy(
        cfg, "bash", {"command": "rm -rf /tmp/workspace-dist"},
    )
    assert level == PermissionLevel.ALLOW
    assert "user_rm_no_action" not in matched


def test_effective_package_critical_is_deny() -> None:
    cfg = inline_package_command_rules(
        {
            "enabled": True,
            "permission_mode": "normal",
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    package_rules = [r for r in cfg["rules"] if r.get("layer") == "builtin"]
    assert package_rules
    assert all(isinstance(r.get("action"), str) and r["action"] for r in package_rules)
    shutdown = next(r for r in package_rules if r.get("id") == "shell_system_shutdown_or_reboot")
    assert shutdown["action"] == "deny"

    level, matched = evaluate_tiered_policy(
        cfg, "bash", {"command": "shutdown -h now"},
    )
    assert level == PermissionLevel.DENY
    assert "builtin" in matched
