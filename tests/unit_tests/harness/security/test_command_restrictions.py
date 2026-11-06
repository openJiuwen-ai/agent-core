# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Restrictions follow argv across launchers without broadening authorization."""

import os
import shlex

import pytest

from openjiuwen.harness.security.permission_engine.approve.command_identity import (
    bind_native_command,
    restriction_command_views,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import evaluate_tiered_policy


def config(pattern, action="deny", *, cross_tool=True):
    return {
        "defaults": {"*": "allow"},
        "shell_guard": {"cross_tool_restrictions": cross_tool},
        "rules": [
            {
                "id": "command-rule",
                "tools": ["bash", "mcp_exec_command", "create_terminal"],
                "pattern": pattern,
                "action": action,
            }
        ],
    }


@pytest.mark.parametrize(
    "tool", ["bash", "powershell", "pwsh", "core.powershell", "mcp_exec_command", "create_terminal"]
)
@pytest.mark.parametrize(
    "name,argv",
    [
        ("git", ["version"]),
        ("curl", ["--version"]),
        ("python", ["--version"]),
        ("node", ["--version"]),
        ("custom-tool", ["run", "--mode", "check"]),
    ],
)
@pytest.mark.parametrize("action", ["deny", "ask"])
def test_native_restrictions_use_actual_argv(tool, name, argv, action):
    words = [f"C:/managed tools/空间/{name}.exe" if os.name == "nt" else f"/managed tools/空间/{name}", *argv]
    # Deliberately not a decodable shell command: only authenticated argv counts.
    args = {"command": "unrelated opaque policy representation", "shell_type": "powershell"}
    with bind_native_command(tool, args, None, words=words):
        level, rule = evaluate_tiered_policy(config(shlex.join([name, *argv]), action), tool, args)
    assert level == PermissionLevel(action) and "command-rule" in rule


@pytest.mark.parametrize(
    "tool,shell,command",
    [
        ("bash", "bash", "git 'version'"),
        ("bash", "bash", "'/managed tools/git' \"version\""),
        ("powershell", "powershell", "& \"C:/managed tools/git.exe\" 'version'"),
        ("core.powershell", "powershell", "& 'C:/managed tools/git.exe' version"),
        ("pwsh", "powershell", "git 'version'"),
        ("mcp_exec_command", "bash", '"/managed tools/git" version'),
    ],
)
def test_literal_entry_restrictions(tool, shell, command):
    pattern = "git version" if os.name == "nt" or "git.exe" not in command else "git.exe version"
    assert (
        evaluate_tiered_policy(config(pattern), tool, {"command": command, "shell_type": shell})[0]
        == PermissionLevel.DENY
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["-C", "repo", "version"],
        ["version", "--build-options"],
        ["version other"],
        ["version", ""],
        ["VERSION"],
        ["version; evil"],
        ["version\nother"],
    ],
)
def test_different_argv_is_never_equal(argv):
    args = {"command": "opaque", "shell_type": "powershell"}
    with bind_native_command("powershell", args, None, words=["/tools/git", *argv]):
        assert evaluate_tiered_policy(config("git version"), "powershell", args)[0] == PermissionLevel.ALLOW


def test_argv_values_cannot_become_an_extra_command():
    args = {"command": "opaque", "shell_type": "powershell"}
    with bind_native_command("powershell", args, None, words=["/tools/echo", "hello; git version"]):
        views = restriction_command_views("powershell", args)
        assert all(shlex.split(view)[1:] == ["hello; git version"] for view in views)
        assert evaluate_tiered_policy(config("git version"), "powershell", args)[0] == PermissionLevel.ALLOW


def test_literal_child_of_compound_bash_is_checked():
    args = {"command": "echo ready && '/managed tools/git' 'version'", "shell_type": "bash"}
    assert evaluate_tiered_policy(config("git version"), "bash", args)[0] == PermissionLevel.DENY


@pytest.mark.parametrize(
    "command,shell",
    [
        ("git $value", "bash"),
        ("git $(echo version)", "bash"),
        ("& $git version", "powershell"),
        ("git @args", "powershell"),
        ("'git' 'version'", "powershell"),
        ("git '--version'", "powershell"),
        ("git 'version'", "auto"),
    ],
)
def test_dynamic_or_non_invocation_syntax_is_not_guessed(command, shell):
    assert restriction_command_views(shell, {"command": command, "shell_type": shell}) == ()


def test_native_binding_cannot_be_forged_by_tool_args_and_is_cleaned_up():
    args = {"command": "opaque", "shell_type": "powershell", "words": ["git", "version"]}
    assert restriction_command_views("powershell", args) == ("opaque",)
    with pytest.raises(RuntimeError):
        with bind_native_command("powershell", args, None, words=["git", "version"]):
            assert "git version" in restriction_command_views("powershell", args)
            assert restriction_command_views("powershell", {**args, "command": "git version"}) == ()
            raise RuntimeError("cleanup")
    assert restriction_command_views("powershell", args) == ("opaque",)


def test_allow_rule_does_not_acquire_path_quote_or_tool_equivalence():
    cfg = config("git version", "allow")
    cfg["defaults"] = {"*": "ask"}
    for tool, command in [("bash", "/tools/git version"), ("bash", "git 'version'"), ("powershell", "git version")]:
        assert evaluate_tiered_policy(cfg, tool, {"command": command, "shell_type": tool})[0] == PermissionLevel.ASK


def test_explicit_tool_baselines_and_other_hosts_keep_their_scope():
    args = {"command": "git version", "shell_type": "powershell"}
    assert (
        evaluate_tiered_policy(config("git version", cross_tool=False), "powershell", args)[0] == PermissionLevel.ALLOW
    )
    cfg = config("git version", "allow")
    cfg["tools"] = {"powershell": "deny"}
    assert evaluate_tiered_policy(cfg, "powershell", args)[0] == PermissionLevel.DENY
    cfg = config("git version")
    cfg["categories"] = {"shell": ["bash", "other-shell"]}
    cfg["rules"][0]["tools"] = ["bash"]
    assert (
        evaluate_tiered_policy(cfg, "other-shell", {"command": "git version", "shell_type": "bash"})[0]
        == PermissionLevel.DENY
    )
    assert evaluate_tiered_policy(cfg, "read_file", {"command": "git version"})[0] == PermissionLevel.ALLOW


def test_remembered_allow_never_overrides_normalized_deny():
    cfg = config("git version")
    args = {"command": "git 'version'", "shell_type": "bash"}
    cfg["approval_overrides"] = [{"id": "old-allow", "tools": ["bash"], "action": "allow", "pattern": args["command"]}]
    assert evaluate_tiered_policy(cfg, "bash", args)[0] == PermissionLevel.DENY


@pytest.mark.skipif(os.name != "nt", reason="Windows executable casing")
def test_executable_case_is_folded_without_folding_arguments():
    args = {"command": "opaque", "shell_type": "powershell"}
    with bind_native_command("powershell", args, None, words=["C:/TOOLS/GIT.EXE", "version"]):
        assert evaluate_tiered_policy(config("git version"), "powershell", args)[0] == PermissionLevel.DENY
    with bind_native_command("powershell", args, None, words=["C:/TOOLS/GIT.EXE", "VERSION"]):
        assert evaluate_tiered_policy(config("git version"), "powershell", args)[0] == PermissionLevel.ALLOW


def test_package_inline_code_deny_cannot_hide_behind_executable_suffix():
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    args = {"command": "opaque", "shell_type": "powershell"}
    engine = PermissionEngine({"enabled": True})
    with bind_native_command(
        "bash",
        args,
        None,
        words=["/tools/python.exe" if os.name == "nt" else "/tools/python", "-c", "import socket; print('network')"],
    ):
        level, rule = evaluate_tiered_policy(engine.config, "bash", args)
    assert level == PermissionLevel.DENY and "builtin" in rule
