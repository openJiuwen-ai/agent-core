"""Split approvals retain the invocation directory and only lift ShellGuard ASK."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.approve.operation_grants import (
    build_operation_grants,
    operation_subject,
    scope_options,
)
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.toolguard import shell_ast


def config(strict=False):
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "tools": {"bash": "ask"},
        "defaults": {"*": "ask"},
        "file_guard": {"enabled": False},
        "net_guard": {"enabled": False},
        **({"defer_unmatched": True, "strict_tool_command_merge": True} if strict else {}),
    }


def arguments(tmp_path, command="cd repo && git status"):
    return {"command": command, "workdir": str(tmp_path), "shell_type": "bash"}


def remember(cfg, args):
    cfg["approval_overrides"] = build_operation_grants(
        "bash", args, cfg, mode="allow_with_scope", scope="subcommands",
    )


def test_children_share_initial_cwd_and_are_deduplicated(tmp_path):
    cfg, args = config(), arguments(tmp_path, "cd repo && git status && git status")
    remember(cfg, args)
    subjects = [json.loads(grant["pattern"])["subject"] for grant in cfg["approval_overrides"]]
    assert [subject["command"] for subject in subjects] == ["cd repo", "git status"]
    assert all(subject["cwd"] == str(tmp_path.resolve()) for subject in subjects)
    assert all(grant["match_type"] == "shell_subcommand" for grant in cfg["approval_overrides"])
    options = scope_options(operation_subject("bash", args, cfg))
    assert [option["value"] for option in options] == ["exact", "subcommands"]
    assert "cd repo" in options[-1]["label"] and "git status" in options[-1]["label"]


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_children_reuse_independently_and_in_new_combinations(tmp_path, strict):
    cfg, args = config(strict), arguments(tmp_path)
    remember(cfg, args)
    engine = PermissionEngine(cfg)
    for command in ["cd repo", "git status", args["command"], "git status && cd repo", "git status; cd repo"]:
        assert (await engine.check_permission("bash", {**args, "command": command})).is_allowed
    for changed in [
        {**args, "command": "git diff"},
        {**args, "command": "git status && git push"},
        {**args, "workdir": str(tmp_path / "repo")},
        {**args, "shell_type": "powershell"},
    ]:
        assert not (await engine.check_permission("bash", changed)).is_allowed
    assert not (await engine.check_permission("mcp_exec_command", args)).is_allowed


@pytest.mark.asyncio
async def test_known_rule_and_remembered_child_can_be_combined(tmp_path):
    cfg, args = config(True), arguments(tmp_path, "git status")
    remember(cfg, args)
    cfg["rules"] = [{"tools": ["bash"], "pattern": "cd repo", "action": "allow"}]
    assert (await PermissionEngine(cfg).check_permission(
        "bash", {**args, "command": "cd repo && git status"},
    )).is_allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["tool", "child", "full"])
async def test_fresh_denies_override_saved_children(tmp_path, restriction):
    cfg, args = config(), arguments(tmp_path)
    remember(cfg, args)
    if restriction == "tool":
        cfg["tools"]["bash"] = "deny"
    else:
        cfg["rules"] = [{
            "tools": ["bash"], "pattern": "git status" if restriction == "child" else args["command"],
            "action": "deny",
        }]
    assert (await PermissionEngine(cfg).check_permission("bash", args)).is_denied


@pytest.mark.asyncio
async def test_literal_wildcards_and_relative_cwd_context(tmp_path, monkeypatch):
    from openjiuwen.harness.security.permission_engine.approve import operation_grants

    monkeypatch.setattr(operation_grants, "get_cwd", lambda: str(tmp_path))
    cfg, args = config(), {"command": "echo *", "workdir": ".", "shell_type": "bash"}
    remember(cfg, args)
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", args)).is_allowed
    assert not (await engine.check_permission("bash", {**args, "command": "echo secret"})).is_allowed
    monkeypatch.setattr(operation_grants, "get_cwd", lambda: str(tmp_path.parent))
    assert not (await engine.check_permission("bash", args)).is_allowed


@pytest.mark.asyncio
async def test_split_grants_preserve_other_guard_asks_and_sink_check(tmp_path):
    cfg, args = config(), arguments(tmp_path, "cat ./secret.txt")
    remember(cfg, args)
    cfg["file_guard"] = {
        "enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "ask"},
    }
    result = await PermissionEngine(cfg, workspace_root=tmp_path).check_permission("bash", args)
    assert result.needs_approval and "file_guard" in result.matched_rule

    cfg["file_guard"] = {"enabled": False}
    engine = PermissionEngine(cfg)
    engine._deletion_guard = SimpleNamespace(evaluate=lambda *_: PermissionResult(PermissionLevel.ASK, "del_guard"))
    assert (await engine.check_permission("bash", args)).needs_approval

    args = arguments(tmp_path, "cat script.sh && bash")
    remember(cfg, args)
    result = await PermissionEngine(cfg).check_permission("bash", {**args, "command": "cat script.sh | bash"})
    assert result.needs_approval and "interpreter_sink" in result.matched_rule


@pytest.mark.parametrize("command", [
    "echo hi > out.txt", "echo $(git status)", "for x in a; do git status; done",
    "cmd /c \"git status && git diff\"", "git status &", "git status # comment",
])
def test_unsupported_structures_offer_exact_only(tmp_path, command):
    cfg, args = config(), arguments(tmp_path, command)
    assert scope_options(operation_subject("bash", args, cfg)) == [{"value": "exact", "label": "仅当前对象"}]
    with pytest.raises(ValueError, match="Invalid authorization scope"):
        build_operation_grants("bash", args, cfg, mode="allow_with_scope", scope="subcommands")


def test_parser_unavailable_does_not_offer_split_compound(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)
    cfg, args = config(), arguments(tmp_path)
    assert [option["value"] for option in scope_options(operation_subject("bash", args, cfg))] == ["exact"]
    with pytest.raises(ValueError):
        build_operation_grants("bash", args, cfg, mode="allow", scope="subcommands")


@pytest.mark.asyncio
async def test_composed_source_priority_still_blocks_lower_user_grants(tmp_path):
    cfg, args = config(), arguments(tmp_path, "git status")
    remember(cfg, args)
    cfg["composition"] = {"schema_version": 1, "source_order": ["cloud", "user", "builtin"]}
    cfg["rules"] = [{"tools": ["bash"], "pattern": "git status", "action": "ask", "layer": "cloud"}]
    assert (await PermissionEngine(cfg).check_permission("bash", args)).needs_approval
