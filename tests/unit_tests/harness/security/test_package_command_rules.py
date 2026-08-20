# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package command rules: default action in YAML, inline into effective rules."""

from __future__ import annotations

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
    load_package_command_rules,
)

_SEVERITY_DEFAULT_ACTION = {
    "HIGH": "ask",
    "CRITICAL": "deny",
}


def test_package_command_rules_carry_default_action() -> None:
    rules = load_package_command_rules()
    assert rules
    ids = {r.get("id") for r in rules}
    assert "shell_system_shutdown_or_reboot" in ids
    assert "shell_chmod_world_writable" in ids
    assert "shell_ld_preload_hijack" in ids
    assert "shell_clear_audit_history" in ids
    assert "shell_disable_firewall" in ids
    assert "shell_docker_privileged" in ids
    for rule in rules:
        severity = str(rule.get("severity") or "").upper()
        assert severity in _SEVERITY_DEFAULT_ACTION, rule.get("id")
        assert rule.get("action") == _SEVERITY_DEFAULT_ACTION[severity], rule.get("id")


def test_inlined_high_command_is_ask_critical_is_deny() -> None:
    cfg = inline_package_command_rules(
        {
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    chmod_level, _ = evaluate_tiered_policy(
        cfg, "bash", {"command": "chmod -R 777 /tmp/app"},
    )
    assert chmod_level == PermissionLevel.ASK
    rm_level, _ = evaluate_tiered_policy(
        cfg, "bash", {"command": "rm -rf /tmp/workspace-dist"},
    )
    assert rm_level == PermissionLevel.ASK
    mkfs_level, _ = evaluate_tiered_policy(
        cfg, "bash", {"command": "mkfs.ext4 /dev/sdb1"},
    )
    assert mkfs_level == PermissionLevel.DENY
