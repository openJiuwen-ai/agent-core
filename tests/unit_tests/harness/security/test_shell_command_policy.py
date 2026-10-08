"""Direct command policy contracts; commands are evaluated, never executed."""

from copy import deepcopy

import pytest

from openjiuwen.harness.security.permission_engine.core import prepare_permissions_for_engine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import evaluate_tiered_policy
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import match_wildcard


def config(**extra):
    return {"enabled": True, "tools": {"bash": "allow", "powershell": "allow"}, **extra}


def decision(cfg, command, tool="bash"):
    return evaluate_tiered_policy(cfg, tool, {"command": command})[0]


@pytest.mark.parametrize("tool", ["bash", "powershell"])
@pytest.mark.parametrize("pattern,command", [
    ("demo *", "demo hello"),
    ("demo ?", "demo x"),
    ("demo * --flag *", "demo hello --flag yes"),
    (r"re:^demo\s+\w+$", "demo hello"),
])
@pytest.mark.parametrize("action", ["deny", "ask", "allow"])
def test_user_command_matching(tool, pattern, command, action):
    cfg = config(rules=[{"tools": ["shell"], "pattern": pattern, "action": action}])
    cfg["tools"][tool] = "ask" if action == "allow" else "allow"
    assert decision(cfg, command, tool) == PermissionLevel(action)


@pytest.mark.parametrize("command,level", [("shutdown -h now", "deny"), ("sudo whoami", "ask")])
def test_builtin_switch_removes_loaded_rules_and_can_reenable(command, level):
    enabled = prepare_permissions_for_engine(config())
    assert decision(enabled, command) == PermissionLevel(level)
    disabled_input = deepcopy(enabled)
    disabled_input["shell_guard"] = {"builtin_rules_enabled": False}
    assert decision(disabled_input, command) == PermissionLevel.ALLOW
    disabled = prepare_permissions_for_engine(disabled_input)
    assert not any(rule.get("layer") == "builtin" for rule in disabled["rules"])
    assert decision(disabled, command) == PermissionLevel.ALLOW
    disabled["shell_guard"]["builtin_rules_enabled"] = True
    assert decision(prepare_permissions_for_engine(disabled), command) == PermissionLevel(level)


def test_switch_preserves_user_file_network_and_structure_policies():
    cfg = config(
        rules=[{"tools": ["shell"], "pattern": "demo *", "action": "deny"}],
        file_guard={"enabled": True}, net_guard={"enabled": True},
    )
    enabled = prepare_permissions_for_engine(cfg)
    cfg["shell_guard"] = {"builtin_rules_enabled": False}
    disabled = prepare_permissions_for_engine(cfg)
    assert disabled["file_guard"] == enabled["file_guard"]
    assert disabled["net_guard"] == enabled["net_guard"]
    assert decision(disabled, "demo hello") == PermissionLevel.DENY
    assert decision(disabled, "cat file | sh") == PermissionLevel.ASK


@pytest.mark.parametrize("command", ["demo blocked", "echo hi && demo blocked"])
@pytest.mark.parametrize("layer", ["builtin", "user"])
def test_remembered_allow_never_overrides_command_deny(command, layer):
    cfg = config(
        rules=[{"tools": ["shell"], "pattern": "demo *", "action": "deny", "layer": layer}],
        approval_overrides=[{"tools": ["shell"], "pattern": command, "action": "allow"}],
    )
    assert decision(cfg, command) == PermissionLevel.DENY


def test_builtin_blacklist_beats_remembered_allow():
    cfg = prepare_permissions_for_engine(config(
        approval_overrides=[{"tools": ["shell"], "pattern": "shutdown *", "action": "allow"}],
    ))
    assert decision(cfg, "shutdown -h now") == PermissionLevel.DENY


def test_global_package_switch_still_disables_command_package():
    cfg = prepare_permissions_for_engine(config(package_builtin_rules=False))
    assert decision(cfg, "shutdown -h now") == PermissionLevel.ALLOW


@pytest.mark.parametrize("command", ["demo xx", "demo ;", "demo |", "demo &", "demo *", "demo ?"])
def test_single_character_wildcard_keeps_length_and_shell_boundaries(command):
    assert not match_wildcard(command, "demo ?")


def test_multiple_wildcards_do_not_swallow_shell_chain():
    assert not match_wildcard("demo hello --flag yes; blocked-command", "demo * --flag *")


def test_single_wildcard_does_not_allow_shell_glob_but_star_keeps_glob_support():
    cfg = config(tools={"bash": "ask"}, rules=[{"tools": ["shell"], "pattern": "rm ?", "action": "allow"}])
    assert decision(cfg, "rm a") == PermissionLevel.ALLOW
    assert decision(cfg, "rm *") == PermissionLevel.ASK
    assert decision(cfg, "rm ?") == PermissionLevel.ASK
    assert match_wildcard("fileX.txt", "file?.txt")
    assert match_wildcard("ls *.txt", "ls *")
    assert match_wildcard("ls ?.txt", "ls *")


@pytest.mark.parametrize("command", ["echo $(shutdown -h now)", "echo $(demo blocked)"])
@pytest.mark.parametrize("unknown_structure", [True, False])
def test_remembered_complex_command_requires_fresh_approval(command, unknown_structure):
    cfg = prepare_permissions_for_engine(config(
        shell_guard={"unknown_structure": unknown_structure},
        rules=[{"tools": ["shell"], "pattern": "demo *", "action": "deny"}],
        approval_overrides=[{"tools": ["shell"], "pattern": command, "action": "allow"}],
    ))
    assert decision(cfg, command) == PermissionLevel.ASK


def test_complex_full_command_deny_still_beats_approval_floor():
    command = "echo $(demo blocked)"
    cfg = config(
        rules=[{"tools": ["shell"], "pattern": command, "action": "deny"}],
        approval_overrides=[{"tools": ["shell"], "pattern": command, "action": "allow"}],
    )
    assert decision(cfg, command) == PermissionLevel.DENY
