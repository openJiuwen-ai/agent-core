# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""模拟用户选择「记住 / 总是允许」(auto_confirm) 后的权限合并。

优先 pattern 级 approval_overrides（command）或 file_guard 路径；无安全 suggestion 时回退 allow_tools。
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.toolguard.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.toolguard.tiered_policy import evaluate_tiered_policy


def _base_tiered() -> dict:
    return {
        "enabled": True,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [],
    }


@pytest.mark.parametrize(
    "tools_fragment",
    [
        pytest.param({"read_file": {"*": "ask"}}, id="legacy_dict_star_ask"),
        pytest.param({"read_file": "ask"}, id="scalar_ask"),
    ],
)
def test_read_file_merge_falls_back_to_allow_tools(
    tools_fragment: dict,
) -> None:
    """路径工具无 command suggestion 时回退 allow_tools（路径细则仍可由 file_guard 并行落盘）。"""
    cfg = {**_base_tiered(), "tools": {**tools_fragment, "write_file": "deny"}}
    tool_args = {"file_path": "notes.txt"}

    before, _rule = evaluate_tiered_policy(cfg, "read_file", tool_args)
    assert before == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "read_file", tool_args
    )
    assert applied is True
    assert (merged.get("approval_overrides") or []) == []
    assert "read_file" in (merged.get("allow_tools") or [])
    assert merged.get("_allow_tools_added") == ["read_file"]


def test_legacy_bash_star_ask_merge_adds_command_override() -> None:
    """``bash: {\"*\": \"ask\"}`` + 简单 ``git status`` 应对应 command 类 override 并变为 ALLOW。"""
    cfg = {
        **_base_tiered(),
        "tools": {"bash": {"*": "ask"}},
    }
    tool_args = {"command": "git status"}

    assert evaluate_tiered_policy(cfg, "bash", tool_args)[0] == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "bash", tool_args
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert any(
        isinstance(o, dict)
        and o.get("match_type") == "command"
        and "git" in str(o.get("pattern", "")).lower()
        and o.get("action") == "allow"
        for o in overrides
    )

    assert evaluate_tiered_policy(merged, "bash", tool_args)[0] == PermissionLevel.ALLOW


def test_plain_tool_without_suggestion_falls_back_to_allow_tools() -> None:
    """无安全 suggestion 时 HITL 回退写入 allow_tools。"""
    cfg = {
        **_base_tiered(),
        "tools": {"cron_create_job": "ask"},
        "ask_tools": ["cron_create_job"],
    }
    tool_args = {"cron": "0 * * * *", "name": "sync"}

    before, _rule = evaluate_tiered_policy(cfg, "cron_create_job", tool_args)
    assert before == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "cron_create_job", tool_args
    )

    assert applied is True
    assert "cron_create_job" in (merged.get("allow_tools") or [])
    assert "cron_create_job" not in (merged.get("ask_tools") or [])
    assert merged.get("approval_overrides") == []


def test_hitl_default_ask_writes_allow_tools() -> None:
    cfg = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "tools": {},
        "allow_tools": [],
        "approval_overrides": [],
        "rules": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "todo_list", {},
    )
    assert applied is True
    assert merged.get("_allow_tools_added") == ["todo_list"]
    assert not any(
        isinstance(o, dict) and "todo_list" in (o.get("tools") or [])
        for o in (merged.get("approval_overrides") or [])
    )


def test_hitl_blocked_by_deny_tools() -> None:
    cfg = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "deny_tools": ["todo_list"],
        "tools": {"todo_list": "deny"},
        "approval_overrides": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "todo_list", {},
    )
    assert applied is False
    assert "todo_list" not in (merged.get("allow_tools") or [])


def test_hitl_blocked_by_global_baseline_rule() -> None:
    cfg = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "tools": {"bash": "ask"},
        "rules": [
            {
                "id": "global_git_status_requires_confirmation",
                "tools": ["bash"],
                "pattern": "git status",
                "action": "ask",
                "_config_layer": "global",
            }
        ],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "bash", {"command": "git status"},
    )
    assert applied is False
    assert "bash" not in (merged.get("allow_tools") or [])


def test_hitl_unsafe_compound_shell_does_not_write_allow_tools() -> None:
    cfg = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "tools": {"bash": "ask"},
        "ask_tools": ["bash"],
        "allow_tools": [],
        "approval_overrides": [],
        "rules": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "bash", {"command": "echo a && echo b"},
    )
    assert applied is False
    assert "bash" not in (merged.get("allow_tools") or [])
    assert not (merged.get("_allow_tools_added") or [])
