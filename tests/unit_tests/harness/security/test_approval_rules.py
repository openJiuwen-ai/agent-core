# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure allow-always merge helpers (``openjiuwen.harness.security.approval_rules``)."""

from __future__ import annotations

from copy import deepcopy

import pytest

from openjiuwen.harness.security.approval_rules import (
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
    merge_permission_allow_rule_into_permissions,
)
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy


def _evaluate(config: dict, tool_name: str, tool_args: dict) -> PermissionLevel:
    return evaluate_tiered_policy(config, tool_name, tool_args)[0]


def test_merge_file_guard_path_rule_writes_paths() -> None:
    perms = {"enabled": True, "external_directory": {"*": "ask"}}
    merged, wrote = merge_file_guard_path_rule(
        perms,
        "/tmp/trusted",
        read="allow",
        write="allow",
        exec_="ask",
    )
    assert wrote is True
    file_guard = merged["file_guard"]
    assert file_guard["enabled"] is True
    assert file_guard["paths"] == [
        {
            "path": "/tmp/trusted",
            "read": "allow",
            "write": "allow",
            "exec": "ask",
            "match": "prefix",
        }
    ]
    # The input mapping is not mutated.
    assert "file_guard" not in perms


def test_merge_file_guard_path_rule_escalates_axes_and_is_idempotent() -> None:
    perms = {"enabled": True, "file_guard": {"enabled": True, "paths": []}}
    merged, first = merge_file_guard_path_rule(perms, "/tmp/data", read="allow", write="ask", exec_="ask")
    assert first is True
    merged, second = merge_file_guard_path_rule(merged, "/tmp/data/", read="allow", write="allow", exec_="ask")
    assert second is True
    entry = merged["file_guard"]["paths"][0]
    assert entry["path"] == "/tmp/data"
    assert entry["read"] == "allow"
    assert entry["write"] == "allow"
    assert entry["exec"] == "ask"

    merged, again = merge_file_guard_path_rule(merged, "/tmp/data", read="allow", write="allow", exec_="ask")
    assert again is False


def test_merge_file_guard_path_rule_empty_path_is_noop() -> None:
    perms = {"enabled": True}
    merged, wrote = merge_file_guard_path_rule(perms, "")
    assert wrote is False
    assert merged == perms


def test_merge_external_directory_allow_forwards_to_file_guard() -> None:
    perms = {"enabled": True, "external_directory": {"*": "ask"}}
    path = "/tmp/outside/a.txt"
    merged, wrote = merge_external_directory_allow_into_permissions(perms, [path])
    assert wrote is True
    entry = merged["file_guard"]["paths"][0]
    assert entry["path"] == path
    assert entry["read"] == "allow"
    assert entry["write"] == "ask"
    assert entry["exec"] == "ask"


def test_merge_file_guard_access_allows_read_keeps_exact_dir() -> None:
    perms = {
        "enabled": True,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
        },
    }
    target = "/workspace/projects"
    merged, wrote = merge_file_guard_access_allows(perms, [(target, "read")])
    assert wrote is True
    entry = merged["file_guard"]["paths"][0]
    assert entry["path"] == target
    assert entry["read"] == "allow"
    assert entry["write"] == "ask"
    assert entry["exec"] == "ask"
    # Never escalates to the parent directory.
    assert all(item["path"] != "/workspace" for item in merged["file_guard"]["paths"])


def test_merge_file_guard_access_allows_write_escalates_axes() -> None:
    perms = {"enabled": True, "file_guard": {"enabled": True, "paths": []}}
    merged, _ = merge_file_guard_access_allows(perms, [("/tmp/data", "read")])
    merged, wrote = merge_file_guard_access_allows(merged, [("/tmp/data", "write")])
    assert wrote is True
    entry = merged["file_guard"]["paths"][0]
    assert entry["read"] == "allow"
    assert entry["write"] == "allow"
    assert entry["exec"] == "ask"


def test_merge_file_guard_access_allows_exec_axis() -> None:
    perms = {"enabled": True}
    merged, wrote = merge_file_guard_access_allows(perms, [("/tmp/bin", "exec")])
    assert wrote is True
    entry = merged["file_guard"]["paths"][0]
    assert entry["read"] == "allow"
    assert entry["write"] == "ask"
    assert entry["exec"] == "allow"


def test_merge_file_guard_access_allows_empty_is_noop() -> None:
    perms = {"enabled": True}
    merged, wrote = merge_file_guard_access_allows(perms, [])
    assert wrote is False
    assert merged == perms


@pytest.mark.parametrize(
    "tools_fragment",
    [
        pytest.param({"read_file": {"*": "ask"}}, id="legacy_dict_star_ask"),
        pytest.param({"read_file": "ask"}, id="scalar_ask"),
    ],
)
def test_path_tool_merge_after_auto_confirm_elevates_whole_tool_allow(
    tools_fragment: dict,
) -> None:
    cfg = {
        "enabled": True,
        "tools": {**tools_fragment, "write_file": "deny"},
        "approval_overrides": [],
    }
    tool_args = {"file_path": "notes.txt"}
    assert _evaluate(cfg, "read_file", tool_args) == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(deepcopy(cfg), "read_file", tool_args)
    assert applied is True

    assert merged.get("approval_overrides") == []
    assert merged["tools"]["read_file"] == "allow"
    assert _evaluate(merged, "read_file", tool_args) == PermissionLevel.ALLOW

    again, applied_again = merge_permission_allow_rule_into_permissions(deepcopy(merged), "read_file", tool_args)
    assert applied_again is False
    assert again.get("approval_overrides") == merged.get("approval_overrides")
    assert again["tools"]["read_file"] == "allow"


def test_merge_path_tool_elevates_tools_not_path_override() -> None:
    cfg = {
        "enabled": True,
        "tools": {"write_file": "ask"},
        "approval_overrides": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "write_file",
        {"file_path": "/tmp/x.txt"},
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert not any(isinstance(item, dict) and item.get("match_type") == "path" for item in overrides)
    assert merged["tools"]["write_file"] == "allow"


def test_legacy_bash_star_ask_merge_adds_command_override() -> None:
    cfg = {"enabled": True, "tools": {"bash": {"*": "ask"}}}
    tool_args = {"command": "git status"}
    assert _evaluate(cfg, "bash", tool_args) == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(deepcopy(cfg), "bash", tool_args)
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert any(
        isinstance(item, dict)
        and item.get("match_type") == "command"
        and "git" in str(item.get("pattern", "")).lower()
        and item.get("action") == "allow"
        for item in overrides
    )
    assert _evaluate(merged, "bash", tool_args) == PermissionLevel.ALLOW


def test_plain_tool_auto_confirm_sets_whole_tool_allow() -> None:
    cfg = {
        "enabled": True,
        "tools": {"cron_create_job": "ask"},
        "approval_overrides": [],
    }
    tool_args = {"cron": "0 * * * *", "name": "sync"}
    assert _evaluate(cfg, "cron_create_job", tool_args) == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(deepcopy(cfg), "cron_create_job", tool_args)
    assert applied is True
    assert merged["tools"]["cron_create_job"] == "allow"
    assert merged.get("approval_overrides") == []
    assert _evaluate(merged, "cron_create_job", tool_args) == PermissionLevel.ALLOW


def test_merge_skips_when_current_permission_is_not_ask() -> None:
    cfg = {"enabled": True, "tools": {"read_file": "allow"}}
    merged, applied = merge_permission_allow_rule_into_permissions(cfg, "read_file", {"file_path": "notes.txt"})
    assert applied is False
    assert merged["tools"]["read_file"] == "allow"
    assert merged.get("approval_overrides") in (None, [])


def test_merge_shell_without_command_is_not_applied() -> None:
    cfg = {"enabled": True, "tools": {"bash": "ask"}}
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "bash",
        {},
    )
    assert applied is False
    assert merged.get("approval_overrides") in (None, [])


def test_merge_shell_redirect_suggestion_is_applied() -> None:
    """User remember is not blocked by structure; redirect still gets a suggestion."""
    cfg = {"enabled": True, "tools": {"bash": "ask"}}
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "bash",
        {"command": "git status > /tmp/out"},
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert any("git status > /tmp/out" in str(item.get("pattern", "")) for item in overrides)
