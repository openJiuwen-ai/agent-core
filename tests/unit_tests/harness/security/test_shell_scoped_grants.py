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
    build_operation_grant,
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
@pytest.mark.parametrize("strict", [False, True])
async def test_full_command_ask_can_reuse_individually_approved_children(tmp_path, strict):
    cfg = config(strict)
    compile_args = arguments(tmp_path, "compile")
    deploy_args = arguments(tmp_path, "deploy")
    cfg["approval_overrides"] = [
        *build_operation_grants("bash", compile_args, cfg, mode="allow_with_scope", scope="subcommands"),
        *build_operation_grants("bash", deploy_args, cfg, mode="allow_with_scope", scope="subcommands"),
    ]
    cfg["rules"] = [{"tools": ["bash"], "pattern": "compile && deploy", "action": "ask"}]
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", compile_args)).is_allowed
    assert (await engine.check_permission("bash", deploy_args)).is_allowed
    combined = arguments(tmp_path, "compile && deploy")
    assert (await engine.check_permission("bash", combined)).is_allowed
    cfg["rules"][0]["action"] = "deny"
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", combined)).is_denied


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


@pytest.mark.parametrize("command", ["ls 中文目录", "echo 😀", "ls 文档A"])
def test_non_ascii_fallback_does_not_offer_unverified_subcommands(tmp_path, monkeypatch, command):
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)
    assert shell_ast.split_shell_for_grants(command) == ()
    assert scope_options(operation_subject("bash", arguments(tmp_path, command), config())) == [
        {"value": "exact", "label": "仅当前对象"},
    ]


@pytest.mark.parametrize("span", [(-1, 3), (0, 99), (2, 1), (0, 0), (0, 1), (1, 3)])
def test_invalid_or_non_utf8_spans_disable_split_grants(monkeypatch, span):
    parsed = shell_ast.ShellAstParseResult(
        kind="simple", subcommands=(shell_ast.ShellSubcommand(text="中", source_span=span),),
    )
    monkeypatch.setattr(shell_ast, "parse_shell_for_permission", lambda command: parsed)
    assert shell_ast.split_shell_for_grants("中") == ()


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("command", ["cd repo && git status", "cat ./secret.txt"])
def test_persistence_sends_one_complete_batch_without_promoting_snapshot_grants(tmp_path, success, command):
    cfg, calls = config(), []
    cfg["approval_overrides"] = [build_operation_grant("bash", arguments(tmp_path, "git diff"), cfg)]
    initial = deepcopy(cfg)

    def save(updated, *, approval_grants):
        calls.append(approval_grants)
        return success

    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path))
    result = rail._persist_merged_allow(
        "bash", arguments(tmp_path, command), persist_hook=save, write_yaml_fallback=False,
        authorization_mode="allow_with_scope", authorization_scope="subcommands",
    )
    assert result is success
    assert len(calls) == 1
    saved = calls[0]
    expected = ["cd repo", "git status"] if "&&" in command else [command, command]
    assert [json.loads(grant["pattern"])["subject"]["command"] for grant in saved] == expected
    assert all(grant["id"] != initial["approval_overrides"][0]["id"] for grant in saved)
    if success:
        assert rail._engine.config["approval_overrides"] == [*initial["approval_overrides"], *saved]
    else:
        assert rail._engine.config == initial


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["once", "session", "always"])
@pytest.mark.parametrize("command,guard", [
    ("cat ./secret.txt", "file_guard"), ("curl https://api.example.co.uk/a", "net_guard"),
])
async def test_single_default_scope_remembers_exact_without_widening_other_calls(
    tmp_path, monkeypatch, choice, command, guard,
):
    cfg, state, writes = config(), {}, []
    cfg["file_guard"] = {
        "enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "ask"},
    }
    cfg["net_guard"] = {"enabled": True, "defaults": "ask", "urls": {}}
    cfg["rules"] = [{"tools": ["bash"], "pattern": "git status", "action": "allow"}]

    def save(updated, *, approval_grants=None):
        cfg.update(deepcopy(updated))
        writes.append(approval_grants)
        return True

    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(
        get_permissions_snapshot=lambda: cfg, persist_session_allow_rule=save, persist_allow_rule=save,
        resolve_workspace_dir=lambda: tmp_path,
    ))
    if guard == "net_guard":
        # Native NetGuard extracts fetch-tool URLs, not curl arguments. Inject
        # its decision to exercise the shared merge/override path for a shell.
        monkeypatch.setattr(type(rail._engine._net_guard), "evaluate", lambda *_: PermissionResult(
            PermissionLevel(cfg["net_guard"]["defaults"]), "net_guard:test_decision",
        ))
    session = SimpleNamespace(session_id="single", get_state=state.get, update_state=state.update)
    ctx = AgentCallbackContext(agent=object(), session=session)
    args = arguments(tmp_path, command)
    call = ToolCall(id="single", type="function", name="bash", arguments=json.dumps(args))
    before = await rail._engine.check_permission("bash", args)
    assert before.needs_approval and guard in before.matched_rule
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
    assert isinstance(await rail.resolve_interrupt(ctx, call, {
        "approved": True, "auto_confirm": choice != "once", "persist_allow": choice == "always",
    }), ApproveResult)
    again = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(again, InterruptResult if choice == "once" else ApproveResult)
    assert not state
    if choice == "once":
        assert not writes and not cfg.get("approval_overrides")
        return
    assert len(writes) == 1
    assert [grant["match_type"] for grant in writes[0]] == ["shell_subcommand", "exact_operation"]
    assert (await rail._engine.check_permission("bash", {**args, "workdir": str(tmp_path / "elsewhere")})).needs_approval
    combined = {**args, "command": command + " && git status"}
    assert (await rail._engine.check_permission("bash", combined)).needs_approval
    shell_only = deepcopy(cfg)
    shell_only["file_guard"] = shell_only["net_guard"] = {"enabled": False}
    assert (await PermissionEngine(shell_only).check_permission("bash", combined)).is_allowed
    if guard == "file_guard":
        cfg[guard]["defaults"]["read"] = cfg[guard]["workspace"]["read"] = "deny"
    else:
        cfg[guard]["defaults"] = "deny"
    rail.update_config(cfg)
    assert (await rail._engine.check_permission("bash", args)).is_denied


def test_repeated_children_do_not_add_an_exact_grant_for_a_compound(tmp_path):
    cfg = config()
    rail = PermissionInterruptRail(config=cfg)
    assert rail._persist_merged_allow(
        "bash", arguments(tmp_path, "git status && git status"), persist_hook=None, write_yaml_fallback=False,
        authorization_mode="allow_with_scope", authorization_scope="subcommands",
    )
    assert [grant["match_type"] for grant in rail._engine.config["approval_overrides"]] == ["shell_subcommand"]


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
        persist_session_allow_rule=lambda cfg, **kwargs: save(overlay, cfg, **kwargs),
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

    def save(updated, *, approval_grants=None):
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
