# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-injected ``permissions.categories.shell`` drives command-rule matching."""

from __future__ import annotations

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.tool_categories import (
    _DEFAULT_SHELL_TOOLS,
    is_shell_tool,
    shell_tools_from_config,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
    evaluate_tiered_policy,
    rule_tools_category_consistent,
)


def test_injected_shell_tool_gets_builtin_command_rule() -> None:
    cfg = {
        "categories": {"shell": ["run_cmd"]},
        "defaults": {"*": "allow"},
        "rules": [{
            "id": "shell_fs_recursive_or_forced_delete",
            "layer": "builtin",
            "tools": ["shell"],
            "pattern": r"re:(?i)\brm\s+-rf\b",
            "action": "ask",
        }],
    }
    level, _ = evaluate_tiered_policy(cfg, "run_cmd", {"command": "rm -rf /tmp/x"})
    assert level == PermissionLevel.ASK


def test_grep_is_not_shell_even_if_command_looks_dangerous() -> None:
    cfg = {
        "categories": {"shell": ["bash"]},
        "defaults": {"*": "allow"},
        "rules": [{
            "id": "shell_fs_recursive_or_forced_delete",
            "layer": "builtin",
            "tools": ["shell"],
            "pattern": r"re:(?i)\brm\s+-rf\b",
            "action": "ask",
        }],
    }
    level, rule = evaluate_tiered_policy(cfg, "grep", {"pattern": "rm -rf", "path": "."})
    assert level != PermissionLevel.ASK or "shell_fs_recursive" not in (rule or "")


def test_shell_mixed_with_path_tool_is_skipped() -> None:
    cfg = {
        "categories": {"shell": ["bash"]},
        "defaults": {"*": "allow"},
        "rules": [{
            "id": "bad_mix",
            "tools": ["shell", "read_file"],
            "pattern": r"re:(?i)\brm\s+-rf\b",
            "action": "ask",
        }],
    }
    level, _ = evaluate_tiered_policy(cfg, "bash", {"command": "rm -rf /tmp/x"})
    assert level == PermissionLevel.ALLOW


def test_rule_tools_shell_token_is_consistent() -> None:
    assert rule_tools_category_consistent(["shell"]) is True
    assert rule_tools_category_consistent(["shell", "bash"]) is True
    assert rule_tools_category_consistent(["shell", "read_file"]) is False


def test_is_shell_tool_takes_name_list_not_full_config() -> None:
    assert is_shell_tool("bash") is True
    assert is_shell_tool("run_cmd") is False
    assert is_shell_tool("run_cmd", ["run_cmd", "bash"]) is True
    assert is_shell_tool("bash", ["run_cmd"]) is False


def test_missing_or_empty_categories_use_package_default() -> None:
    assert shell_tools_from_config(None) == _DEFAULT_SHELL_TOOLS
    assert shell_tools_from_config({}) == _DEFAULT_SHELL_TOOLS
    assert shell_tools_from_config({"categories": {}}) == _DEFAULT_SHELL_TOOLS
    assert shell_tools_from_config({"categories": {"shell": []}}) == _DEFAULT_SHELL_TOOLS
    assert shell_tools_from_config({"categories": {"shell": ["run_cmd"]}}) == frozenset({"run_cmd"})
    assert "cmd" not in shell_tools_from_config(None)


def test_default_shell_tools_still_match_without_host_categories() -> None:
    cfg = {
        "defaults": {"*": "allow"},
        "rules": [{
            "id": "shell_fs_recursive_or_forced_delete",
            "layer": "builtin",
            "tools": ["shell"],
            "pattern": r"re:(?i)\brm\s+-rf\b",
            "action": "ask",
        }],
    }
    for name in ("bash", "powershell", "core.powershell", "mcp_exec_command", "create_terminal"):
        level, _ = evaluate_tiered_policy(cfg, name, {"command": "rm -rf /tmp/x"})
        assert level == PermissionLevel.ASK, name
