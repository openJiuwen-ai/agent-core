# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HITL approval_overrides may relax ASK, never relax rules/builtin DENY."""

from __future__ import annotations

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy


def _cfg(**extra: object) -> dict:
    cfg: dict = {
        "enabled": True,
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "rules": [],
        "approval_overrides": [],
    }
    cfg.update(extra)
    return cfg


def _deny_rm() -> dict:
    return {
        "id": "deny_rm",
        "tools": ["bash"],
        "pattern": "rm *",
        "action": "deny",
    }


def _remember_rm_perm_a() -> dict:
    return {
        "id": "user_allow_bash_command_rm_perm_a_txt",
        "tools": ["bash"],
        "match_type": "command",
        "pattern": "rm perm_a.txt",
        "action": "allow",
    }


def test_rules_deny_beats_matching_approval_override() -> None:
    level, matched = evaluate_tiered_policy(
        _cfg(rules=[_deny_rm()], approval_overrides=[_remember_rm_perm_a()]),
        "bash",
        {"command": "rm perm_a.txt"},
    )
    assert level == PermissionLevel.DENY
    assert "deny_rm" in matched
    assert "approval_overrides" not in matched


def test_approval_override_still_allows_when_no_deny() -> None:
    level, matched = evaluate_tiered_policy(
        _cfg(approval_overrides=[_remember_rm_perm_a()]),
        "bash",
        {"command": "rm perm_a.txt"},
    )
    assert level == PermissionLevel.ALLOW
    assert "approval_overrides" in matched


def test_unrelated_rm_still_denied_when_other_command_remembered() -> None:
    level, matched = evaluate_tiered_policy(
        _cfg(rules=[_deny_rm()], approval_overrides=[_remember_rm_perm_a()]),
        "bash",
        {"command": "rm other.txt"},
    )
    assert level == PermissionLevel.DENY
    assert "deny_rm" in matched


def test_remembered_compound_cannot_bypass_subcommand_deny() -> None:
    level, matched = evaluate_tiered_policy(
        _cfg(
            rules=[_deny_rm()],
            approval_overrides=[
                {
                    "id": "remember_compound",
                    "tools": ["bash"],
                    "match_type": "command",
                    "pattern": "rm perm_a.txt && echo done",
                    "action": "allow",
                }
            ],
        ),
        "bash",
        {"command": "rm perm_a.txt && echo done"},
    )
    assert level == PermissionLevel.DENY
    assert "deny_rm" in matched
    assert "approval_overrides" not in matched
