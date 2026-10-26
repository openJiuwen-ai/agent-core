"""Split approvals retain the invocation directory and only lift ShellGuard ASK."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
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
@pytest.mark.parametrize("choice", ["once", "session", "always", "reject"])
@pytest.mark.parametrize("explicit_scope", [False, True])
async def test_one_approval_persists_a_batch_with_the_requested_lifetime(tmp_path, choice, explicit_scope):
    disk, overlay, state, writes = config(), {}, {}, []

    def snapshot():
        return {**disk, "approval_overrides": [*disk.get("approval_overrides", []), *overlay.get("approval_overrides", [])]}

    def save(target, cfg, *, approval_grants=None, session_id=None):
        target.update(deepcopy(cfg))
        writes.append(approval_grants or cfg["approval_overrides"])
        return True

    host = ToolPermissionHost(
        get_permissions_snapshot=snapshot,
        persist_allow_rule=lambda cfg, **kwargs: save(disk, cfg, **kwargs),
        persist_session_allow_rule=lambda cfg: save(overlay, cfg),
    )
    rail = PermissionInterruptRail(config=disk, host=host)
    session = SimpleNamespace(get_state=lambda key: state.get(key), update_state=state.update, session_id="scope-test")
    ctx = AgentCallbackContext(agent=object(), session=session)
    args = arguments(tmp_path)
    call = ToolCall(id="chain", type="function", name="bash", arguments=json.dumps(args))
    pending = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(pending, InterruptResult)
    result = await rail.resolve_interrupt(ctx, call, {
        "approved": choice != "reject", "auto_confirm": choice in {"session", "always"},
        "persist_allow": choice == "always",
        **({"authorization_mode": "allow_with_scope", "authorization_scope": "subcommands"} if explicit_scope else {}),
    })
    assert isinstance(result, RejectResult if choice == "reject" else ApproveResult)
    child = ToolCall(id="child", type="function", name="bash", arguments=json.dumps({**args, "command": "git status"}))
    assert isinstance(await rail.resolve_interrupt(ctx, child, None),
                      ApproveResult if choice in {"session", "always"} else InterruptResult)
    other = await PermissionEngine(disk).check_permission("bash", {**args, "command": "git status"})
    assert other.is_allowed == (choice == "always")
    assert len(writes) == (1 if choice in {"session", "always"} else 0)
    if writes:
        assert len(writes[0]) == 2
        # A session auto-confirm key would skip fresh FileGuard ASK on this chain.
        assert not state


@pytest.mark.asyncio
async def test_composed_source_priority_still_blocks_lower_user_grants(tmp_path):
    cfg, args = config(), arguments(tmp_path, "git status")
    remember(cfg, args)
    cfg["composition"] = {"schema_version": 1, "source_order": ["cloud", "user", "builtin"]}
    cfg["rules"] = [{"tools": ["bash"], "pattern": "git status", "action": "ask", "layer": "cloud"}]
    assert (await PermissionEngine(cfg).check_permission("bash", args)).needs_approval


@pytest.mark.asyncio
async def test_session_remember_rechecks_file_guard_for_the_same_original_call(tmp_path):
    cfg, state = config(), {}
    args = arguments(tmp_path, "cat ./secret.txt && git status")
    session = SimpleNamespace(get_state=lambda key: state.get(key), update_state=state.update, session_id="file-recheck")

    def save(updated):
        cfg.update(deepcopy(updated))
        return True

    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(
        get_permissions_snapshot=lambda: cfg, persist_session_allow_rule=save,
        resolve_workspace_dir=lambda: tmp_path,
    ))
    ctx = AgentCallbackContext(agent=object(), session=session)
    call = ToolCall(id="same-call", type="function", name="bash", arguments=json.dumps(args))
    assert isinstance(await rail.resolve_interrupt(ctx, call, {
        "approved": True, "auto_confirm": True, "persist_allow": False,
        "authorization_mode": "allow_with_scope", "authorization_scope": "subcommands",
    }), ApproveResult)
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)
    cfg["file_guard"] = {"enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "ask"}}
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
