# coding: utf-8
from copy import deepcopy
from openjiuwen.harness.security.patterns import merge_permission_allow_rule_into_permissions


def test_hitl_no_pattern_writes_allow_tools_when_mode_default_ask() -> None:
    cfg = {
        "enabled": True,
        "mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {},
        "ask_tools": [],
        "deny_tools": [],
        "allow_tools": [],
        "approval_overrides": [],
        "rules": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "todo_list", {},
    )
    assert applied is True
    assert "todo_list" in (merged.get("allow_tools") or [])
    assert merged.get("_allow_tools_added") == ["todo_list"]
    assert not any(
        isinstance(o, dict) and "todo_list" in (o.get("tools") or [])
        for o in (merged.get("approval_overrides") or [])
    )


def test_hitl_no_pattern_blocked_by_deny_tools() -> None:
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


def test_hitl_no_pattern_blocked_by_global_baseline() -> None:
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


def test_hitl_path_tool_under_strict_defaults_writes_allow_tools() -> None:
    cfg = {
        "enabled": True,
        "mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {},
        "allow_tools": [],
        "approval_overrides": [],
        "rules": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg),
        "write_file",
        {"file_path": r"C:\tmp\test2.txt", "content": "x"},
    )
    assert applied is True
    assert "write_file" in (merged.get("allow_tools") or [])
    assert merged.get("_allow_tools_added") == ["write_file"]


def test_hitl_with_shell_pattern_still_uses_overrides_not_allow_tools() -> None:
    cfg = {
        "enabled": True,
        "defaults": {"*": "ask"},
        "tools": {"bash": "ask"},
        "ask_tools": ["bash"],
        "approval_overrides": [],
        "rules": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "bash", {"command": "ls -la"},
    )
    assert applied is True
    assert "bash" not in (merged.get("allow_tools") or [])
    assert merged.get("approval_overrides")


def test_hitl_shell_without_safe_suggestion_does_not_write_allow_tools() -> None:
    """bash/powershell 无安全 command pattern 时不得回退整工具 allow_tools。"""
    cfg = {
        "enabled": True,
        "mode": "strict",
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