"""Desktop cloud coverage contracts. Commands are checked, never executed."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.approve.operation_grants import build_operation_grant
from openjiuwen.harness.security.permission_engine.core import PermissionEngine, prepare_permissions_for_engine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.toolguard import tool_policy
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    ShellAstParseResult,
    ShellStructureFlags,
)

LEVELS = ["deny", "ask", "undetermined", "allow"]


def config(**extra):
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "defer_unmatched": True,
        "strict_tool_command_merge": True,
        "tools": {"bash": "allow"},
        **extra,
    }


def rule(pattern, action="allow", **extra):
    return {"tools": ["shell"], "pattern": pattern, "action": action, **extra}


def decision(cfg, command="demo x", tool="bash"):
    return tool_policy.evaluate_tiered_policy(cfg, tool, {"command": command})[0].value


@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("tool_level", LEVELS)
@pytest.mark.parametrize("command_level", LEVELS)
def test_all_tool_command_combinations(tool_level, command_level, composed):
    cfg = config(
        tools={} if tool_level == "undetermined" else {"bash": tool_level},
        rules=[] if command_level == "undetermined" else [rule("demo x", command_level, layer="user")],
    )
    if composed:
        cfg["composition"] = {"schema_version": 1, "source_order": ["user", "builtin"]}
    expected = LEVELS[min(LEVELS.index(tool_level), LEVELS.index(command_level))]
    assert decision(cfg) == expected


@pytest.mark.parametrize("key", ["strict_tool_command_merge", "defer_unmatched"])
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_new_strategy_requires_both_explicit_booleans(key, value):
    cfg = config(tools={"bash": "ask"}, rules=[rule("demo x")])
    if value is None:
        cfg.pop(key)
    else:
        cfg[key] = value
    assert decision(cfg) == "allow"


@pytest.mark.parametrize("tool", ["read_file", "fetch_webpage", "cron", "custom"])
def test_non_shell_tools_do_not_gain_a_command_check(tool):
    assert decision(config(tools={tool: "allow"}), tool=tool) == "allow"


@pytest.mark.parametrize("tool", ["bash", "powershell", "core.powershell", "mcp_exec_command", "create_terminal"])
def test_default_shell_categories_require_command_coverage(tool):
    assert decision(config(tools={tool: "allow"}), tool=tool) == "undetermined"


def test_host_shell_category_and_cmd_argument_are_checked():
    cfg = config(tools={"host_shell": "allow"}, categories={"shell": ["host_shell"]})
    assert tool_policy.evaluate_tiered_policy(cfg, "host_shell", {"cmd": "demo x"})[0] == PermissionLevel.UNDETERMINED
    cfg["rules"] = [rule("demo x")]
    assert tool_policy.evaluate_tiered_policy(cfg, "host_shell", {"cmd": "demo x"})[0] == PermissionLevel.ALLOW


def test_defaults_and_unrelated_rules_do_not_fill_missing_coverage():
    cfg = config(defaults={"*": "allow"}, rules=[rule("other", "deny")])
    assert decision(cfg) == "undetermined"
    cfg["rules"].append(rule("demo x"))
    assert decision(cfg) == "allow"
    cfg["tools"] = {}
    assert decision(cfg) == "undetermined"


def test_disabling_builtin_rules_keeps_user_rules_and_unmatched_state():
    cfg = config(rules=[rule("demo x", "deny", layer="builtin")])
    assert decision(cfg) == "deny"
    cfg["shell_guard"] = {"builtin_rules_enabled": False}
    assert decision(cfg) == "undetermined"
    cfg["rules"].append(rule("demo x"))
    assert decision(cfg) == "allow"


@pytest.mark.parametrize(
    "rules,expected",
    [
        ([rule("git status"), rule("npm run build")], "allow"),
        ([rule("git status")], "undetermined"),
        ([rule("git status && npm run build")], "allow"),
        ([rule("git status && npm run build"), rule("npm run build", "ask")], "ask"),
        ([rule("git status && npm run build"), rule("npm run build", "deny")], "deny"),
        ([rule(r"re:git status")], "undetermined"),
        ([rule(r"re:^git status && npm run build$")], "undetermined"),
        ([rule("git status"), rule("npm run *")], "allow"),
    ],
)
def test_compound_command_coverage(rules, expected):
    assert decision(config(rules=rules), "git status && npm run build") == expected


@pytest.mark.parametrize("separator", [" || ", "; ", "\n", " | "], ids=["or", "semicolon", "newline", "pipe"])
@pytest.mark.parametrize(
    "coverage,expected",
    [
        ("partial", "undetermined"),
        ("children", "allow"),
        ("exact", "allow"),
        ("child-ask", "ask"),
        ("child-deny", "deny"),
    ],
)
def test_compound_operator_coverage_and_child_restrictions(separator, coverage, expected):
    """Every parsed child needs coverage, regardless of the shell separator."""
    first, second = "echo TEST_FIRST", "printf TEST_SECOND"
    command = first + separator + second
    rules = {
        "partial": [rule(first)],
        "children": [rule(first), rule(second)],
        "exact": [rule(command)],
        "child-ask": [rule(command), rule(second, "ask")],
        "child-deny": [rule(command), rule(second, "deny")],
    }[coverage]
    assert decision(config(rules=rules), command) == expected


def test_full_command_allow_does_not_cover_unmatched_tool():
    cfg = config(tools={}, rules=[rule("git status && npm run build")])
    assert decision(cfg, "git status && npm run build") == "undetermined"


def test_full_command_ask_and_deny_still_restrict_allowed_children():
    for level in ("ask", "deny"):
        cfg = config(rules=[rule("git status"), rule("npm run build"), rule("git status && npm run build", level)])
        assert decision(cfg, "git status && npm run build") == level


@pytest.mark.parametrize("kind", ["too_complex", "parse_unavailable"])
@pytest.mark.parametrize("unknown_structure", [False, True])
def test_full_allow_cannot_cover_an_unverified_structure(monkeypatch, kind, unknown_structure):
    monkeypatch.setattr(
        tool_policy,
        "parse_shell_for_permission",
        lambda _: ShellAstParseResult(
            kind=kind,
            flags=ShellStructureFlags(has_compound_operators=True),
        ),
    )
    cfg = config(rules=[rule("git status && npm run build")], shell_guard={"unknown_structure": unknown_structure})
    assert decision(cfg, "git status && npm run build") == ("ask" if unknown_structure else "undetermined")


def test_interpreter_sink_guard_survives_exact_full_command_allow():
    command = "cat script.txt | sh"
    assert decision(config(rules=[rule(command)]), command) == "ask"


def test_source_precedence_is_inside_the_command_check():
    cfg = config(rules=[rule("demo x", "allow", layer="user"), rule("demo x", "ask", layer="builtin")])
    assert decision(cfg) == "ask"
    cfg["composition"] = {"schema_version": 1, "source_order": ["user", "builtin"]}
    assert decision(cfg) == "allow"
    cfg["rules"][1]["action"] = "deny"
    assert decision(cfg) == "deny"


def test_lower_priority_literal_rule_cannot_supply_full_coverage():
    cfg = config(
        rules=[
            rule(r"re:git status", layer="cloud"),
            rule("git status && npm run build", layer="user"),
        ],
        composition={"schema_version": 1, "source_order": ["cloud", "user"]},
    )
    assert decision(cfg, "git status && npm run build") == "undetermined"


@pytest.mark.parametrize("tool_level", ["ask", "allow", "undetermined"])
def test_existing_full_command_approval_still_applies(tool_level):
    cfg = config(
        tools={} if tool_level == "undetermined" else {"bash": tool_level},
        approval_overrides=[rule("git status && npm run build")],
    )
    assert decision(cfg, "git status && npm run build") == "allow"
    cfg["rules"] = [rule("npm run build", "deny")]
    assert decision(cfg, "git status && npm run build") == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("tool_level", ["allow", "ask"])
async def test_scoped_operation_grants_are_checked_after_merge(tmp_path, strict, tool_level):
    args = {"command": "demo x", "workdir": str(tmp_path), "shell_type": "bash"}
    cfg = config(tools={"bash": tool_level}, strict_tool_command_merge=strict)
    cfg["approval_overrides"] = [build_operation_grant("bash", args, cfg)]
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", args)).is_allowed
    if strict:
        for changed in [
            {**args, "command": "demo y"},
            {**args, "workdir": str(tmp_path.parent)},
            {**args, "shell_type": "powershell"},
        ]:
            assert not (await engine.check_permission("bash", changed)).is_allowed
    cfg["rules"] = [rule("demo x", "deny")]
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", args)).is_denied


@pytest.mark.asyncio
async def test_old_defer_only_host_does_not_change_grant_handling(tmp_path):
    cfg = config(tools={}, strict_tool_command_merge=False)
    args = {"command": "demo x", "workdir": str(tmp_path)}
    cfg["approval_overrides"] = [build_operation_grant("bash", args, cfg)]
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", args)).is_undetermined
    cfg["strict_tool_command_merge"] = True
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", args)).is_allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["_file_guard", "_net_guard"])
@pytest.mark.parametrize("level", [PermissionLevel.ASK, PermissionLevel.DENY])
async def test_other_guard_restrictions_are_merged_before_operation_grants(tmp_path, guard, level):
    args = {"command": "demo x", "workdir": str(tmp_path)}
    cfg = config()
    engine = PermissionEngine(cfg)
    checker = SimpleNamespace(evaluate=lambda *_: PermissionResult(level, matched_rule="guard-test"))
    setattr(engine, guard, checker)
    assert (await engine.check_permission("bash", args)).permission == level
    cfg["approval_overrides"] = [build_operation_grant("bash", args, cfg)]
    engine.update_config(cfg)
    setattr(engine, guard, checker)
    result = await engine.check_permission("bash", args)
    assert result.permission == (PermissionLevel.DENY if level == PermissionLevel.DENY else PermissionLevel.ALLOW)


def test_preparation_and_evaluation_do_not_mutate_input():
    cfg = config(rules=[rule("git status && npm run build")])
    original = deepcopy(cfg)
    assert decision(prepare_permissions_for_engine(cfg), "git status && npm run build") == "allow"
    assert cfg == original


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["once", "session", "always", "reject"])
async def test_unmatched_rail_approval_lifetime_and_deny_priority(tmp_path, choice):
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult, RejectResult
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
    from openjiuwen.harness.security.host import ToolPermissionHost

    disk, session_cfg, state = config(), {}, {}
    observed = []

    def snapshot():
        return {
            **disk,
            "approval_overrides": [
                *disk.get("approval_overrides", []),
                *session_cfg.get("approval_overrides", []),
            ],
        }

    def persist(target, delta, *, approval_grants=None, session_id=None):
        target.update(deepcopy(delta))
        return True

    async def cloud_unavailable(request):
        observed.append(request.result.permission)
        return PermissionResult(PermissionLevel.ASK) if request.result.is_undetermined else None

    host = ToolPermissionHost(
        get_permissions_snapshot=snapshot,
        resolve_workspace_dir=lambda: tmp_path,
        persist_allow_rule=lambda delta, **kwargs: persist(disk, delta, **kwargs),
        persist_session_allow_rule=lambda delta, **kwargs: persist(session_cfg, delta, **kwargs),
        on_permission_evaluated=cloud_unavailable,
    )
    rail = PermissionInterruptRail(config=disk, host=host)
    session = SimpleNamespace(get_state=state.get, update_state=state.update, session_id="strict-one")
    ctx = AgentCallbackContext(agent=object(), session=session)
    args = {"command": "demo x", "workdir": str(tmp_path)}
    call = ToolCall(id="one", type="function", name="bash", arguments=json.dumps(args))
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
    assert observed[-1] == PermissionLevel.UNDETERMINED
    result = await rail.resolve_interrupt(
        ctx,
        call,
        {
            "approved": choice != "reject",
            "auto_confirm": choice in {"session", "always"},
            "persist_allow": choice == "always",
        },
    )
    assert isinstance(result, RejectResult if choice == "reject" else ApproveResult)
    again = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(again, ApproveResult if choice in {"session", "always"} else InterruptResult)
    assert observed[-1] == (PermissionLevel.ALLOW if choice in {"session", "always"} else PermissionLevel.UNDETERMINED)
    other = ToolCall(id="two", type="function", name="bash", arguments=json.dumps({**args, "command": "demo y"}))
    assert isinstance(await rail.resolve_interrupt(ctx, other, None), InterruptResult)
    assert (await PermissionEngine(disk).check_permission("bash", args)).is_allowed == (choice == "always")
    disk["rules"] = [rule("demo x", "deny")]
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), RejectResult)
