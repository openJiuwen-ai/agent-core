"""Only confirmed card resources transfer to the same actual tool invocation."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import (
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("local,external,expected", [
    ("allow", "deny", "allow"), ("ask", "allow", "ask"), ("deny", "allow", "deny"),
    ("undetermined", "allow", "allow"), ("undetermined", "deny", "deny"),
    ("undetermined", "ask", "ask"), ("undetermined", None, "ask"),
    ("undetermined", "invalid", "ask"), ("undetermined", "error", "ask"),
])
async def test_runtime_permission_reuses_host_contract(local, external, expected):
    observed = []

    async def hook(request):
        observed.append(request)
        request.result.permission = PermissionLevel.ALLOW
        if external == "error":
            raise RuntimeError("host unavailable")
        if external == "invalid":
            return object()
        return PermissionResult(PermissionLevel(external)) if external else None

    rail = PermissionInterruptRail(config={"enabled": True, "package_builtin_rules": False},
                                   host=ToolPermissionHost(on_permission_evaluated=hook))
    ctx = SimpleNamespace()
    call = ToolCall(id="native", name="bash", type="function", arguments='{"command":"which bash"}')
    result = PermissionResult(PermissionLevel(local), "local_rule")
    decision = await rail.evaluate_runtime_permission(ctx, call, result)
    assert decision.permission.value == expected
    assert result.permission.value == local
    assert len(observed) == 1 and observed[0].ctx is ctx and observed[0].tool_call is call


def test_runtime_snapshot_rebuilds_only_on_change_but_revocation_is_live(tmp_path):
    snapshot = {"enabled": True, "package_builtin_rules": False,
                "net_guard": {"enabled": True, "defaults": "allow"}}
    provider = Mock(return_value=snapshot)
    rail = PermissionInterruptRail(config=snapshot, host=ToolPermissionHost(
        get_permissions_snapshot=provider, resolve_workspace_dir=lambda: tmp_path,
    ))
    original = rail.update_config
    rail.update_config = Mock(wraps=original)
    for _ in range(50):
        engine = rail.runtime_engine("s")
    assert provider.call_count == 50
    assert rail.update_config.call_count == 1
    assert engine.config["net_guard"]["defaults"] == "allow"
    # A mutable provider must not hide in-place changes or revoked policy.
    snapshot["net_guard"]["defaults"] = "deny"
    assert rail.runtime_engine("s").config["net_guard"]["defaults"] == "deny"
    assert rail.update_config.call_count == 2
    # Ordinary hot updates invalidate the remembered source snapshot.
    rail.update_config({**snapshot, "net_guard": {"enabled": False}})
    assert rail.runtime_engine("s").config["net_guard"]["enabled"] is True
    assert rail.update_config.call_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "policy", "arguments", "session", "tool"])
@pytest.mark.parametrize("include_operation", [False, True])
async def test_approved_resources_consumed_once(tmp_path, change, include_operation):
    target = tmp_path / "outside.txt"
    config = {"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "allow"},
              "file_guard": {"enabled": True, "paths": [{"path": str(target), "read": "ask", "write": "ask", "exec": "ask"}]}}
    rail = PermissionInterruptRail(config=config, host=ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path / "workspace"))
    args = {"file_path": str(target)}
    call = ToolCall(id="c", type="function", name="read_file", arguments=json.dumps(args))
    ctx = SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s"))
    # Exercise the actual confirmation path, not the transfer helper.
    await rail.resolve_interrupt(ctx=ctx, tool_call=call, user_input={"approved": True, "auto_confirm": False})
    if change == "policy":
        revised = deepcopy(config)
        revised["file_guard"]["paths"][0]["read"] = "deny"
        rail.update_config(revised)
    received = rail.take_runtime_approval("other" if change == "session" else "s", "c",
        "write_file" if change == "tool" else "read_file", {} if change == "arguments" else args,
        include_operation=include_operation)
    assert bool(received) is (change == "none")
    if received:
        assert (received["accesses"] if include_operation else received) == [(str(target), "read")]
        assert not rail.take_runtime_approval("s", "c", "read_file", args)


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("permanent", [False, True])
async def test_persisted_confirmation_transfers_pre_persistence_file_ask(tmp_path, hosted, permanent):
    target = tmp_path / "outside.txt"
    cfg = {"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "ask"},
           "file_guard": {"enabled": True, "defaults": {"read": "ask"}}}
    response = {"approved": True, "auto_confirm": True, "persist_allow": permanent}

    def save(updated, **_kwargs):
        cfg.update(deepcopy(updated))
        return True

    async def confirm(_request):
        return PermissionConfirmResponse(**response)

    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(
        resolve_workspace_dir=lambda: tmp_path / "workspace", get_permissions_snapshot=lambda: cfg,
        persist_session_allow_rule=save, persist_allow_rule=save,
        request_permission_confirmation=confirm if hosted else None,
    ))
    args = {"file_path": str(target)}
    call = ToolCall(id="persisted", type="function", name="read_file", arguments=json.dumps(args))
    state = {}
    ctx = SimpleNamespace(session=SimpleNamespace(
        get_session_id=lambda: "s", get_state=state.get, update_state=state.update))
    if not hosted:
        assert type(await rail.resolve_interrupt(ctx, call, None)).__name__ == "InterruptResult"
    assert type(await rail.resolve_interrupt(ctx, call, None if hosted else response)).__name__ == "ApproveResult"
    assert cfg["approval_overrides"]
    transferred = rail.take_runtime_approval("s", "persisted", "read_file", args, include_operation=True)
    assert transferred["accesses"] == [(str(target), "read")]
    assert not rail.take_runtime_approval("s", "persisted", "read_file", args)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "url"])
async def test_confirmed_operation_without_file_accesses_transfers_once(tmp_path, kind):
    config = {"enabled": True, "package_builtin_rules": False, "tools": {"bash": "ask", "mcp_fetch_webpage": "ask"}}
    rail = PermissionInterruptRail(config=config, host=ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path))
    args = {"command": "echo approved", "workdir": str(tmp_path)} if kind == "command" else {"url": "https://site.test/a"}
    tool = "bash" if kind == "command" else "mcp_fetch_webpage"
    call = ToolCall(id="c", type="function", name=tool, arguments=json.dumps(args))
    ctx = SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s"))
    await rail.resolve_interrupt(ctx=ctx, tool_call=call, user_input={"approved": True, "auto_confirm": False})
    received = rail.take_runtime_approval("s", "c", tool, args, include_operation=True)
    assert received["operation"]["kind"] == kind
    assert not received["accesses"]
    assert not rail.take_runtime_approval("s", "c", tool, args, include_operation=True)


@pytest.mark.asyncio
async def test_rejected_command_has_no_runtime_transfer(tmp_path):
    rail = PermissionInterruptRail(config={"enabled": True, "package_builtin_rules": False, "tools": {"bash": "ask"}})
    args = {"command": "rm temp.txt", "workdir": str(tmp_path)}
    call = ToolCall(id="c", type="function", name="bash", arguments=json.dumps(args))
    await rail.resolve_interrupt(ctx=SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s")),
                                 tool_call=call, user_input={"approved": False})
    assert not rail.take_runtime_approval("s", "c", "bash", args, include_operation=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize("kind", ["command", "file", "url"])
async def test_remembered_decision_transfers_on_next_call_and_reload(tmp_path, permanent, kind):
    config = {"enabled": True, "package_builtin_rules": False,
              "tools": {"bash": "ask", "read_file": "ask", "mcp_fetch_webpage": "ask"}}
    host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path, persist_allow_rule=lambda *_a, **_kw: True)
    rail = PermissionInterruptRail(config=config, host=host)
    args = ({"command": "rm A.txt", "workdir": str(tmp_path), "shell_type": "bash"} if kind == "command"
            else {"file_path": str(tmp_path / "A.txt")} if kind == "file" else {"url": "https://www.example.com/a"})
    tool = {"command": "bash", "file": "read_file", "url": "mcp_fetch_webpage"}[kind]
    state = {}
    ctx = SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s", get_state=state.get, update_state=state.update))

    def call(cid):
        return ToolCall(id=cid, type="function", name=tool, arguments=json.dumps(args))

    await rail.resolve_interrupt(ctx, call("first"), {"approved": True, "auto_confirm": True, "persist_allow": permanent})
    first = rail.take_runtime_approval("s", "first", tool, args, include_operation=True)
    assert first["grants"] and first["operation"]["kind"] == kind
    if permanent:
        rail = PermissionInterruptRail(config=deepcopy(rail.runtime_engine("s").config), host=host)
        state.clear()
    assert type(await rail.resolve_interrupt(ctx, call("next"), None)).__name__ == "ApproveResult"
    received = rail.take_runtime_approval("s", "next", tool, args, include_operation=True)
    assert received["operation"] == first["operation"]
    assert not rail.take_runtime_approval("s", "next", tool, args, include_operation=True)


@pytest.mark.asyncio
async def test_ordinary_policy_allow_does_not_become_a_child_command_approval(tmp_path):
    rail = PermissionInterruptRail(config={"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"}})
    args = {"command": "echo hello", "workdir": str(tmp_path)}
    call = ToolCall(id="c", type="function", name="bash", arguments=json.dumps(args))
    await rail.resolve_interrupt(SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s")), call, None)
    assert not rail.take_runtime_approval("s", "c", "bash", args, include_operation=True)


@pytest.mark.parametrize(("subject", "scope"), [
    ({"kind": "file", "accesses": [("C:/data/A.txt", "write")]}, "parent"),
    ({"kind": "url", "url": "https://www.example.com/a"}, "domain"),
    ({"kind": "command", "command": "echo one && echo two", "shell_type": "bash"}, "subcommands"),
    ({"kind": "program"}, "exact"),
])
@pytest.mark.parametrize("choice", ["once", "session", "permanent"])
def test_runtime_and_tool_scope_defaults_share_normalization(subject, scope, choice):
    payload = PermissionConfirmResponse(
        approved=True, auto_confirm=choice != "once", persist_allow=choice == "permanent",
    )
    normalized = PermissionInterruptRail.normalize_confirmation_scope(payload, subject)
    expected = "exact" if choice == "once" else scope
    assert normalized.authorization_scope == expected
    assert normalized.authorization_mode == ("allow" if expected == "exact" else "allow_with_scope")
    exact = PermissionInterruptRail.normalize_confirmation_scope(
        PermissionConfirmResponse(approved=True, authorization_mode="allow"), subject)
    assert exact.authorization_scope == "exact"

    if choice == "once":
        expanded = PermissionInterruptRail.normalize_confirmation_scope(
            PermissionConfirmResponse(
                approved=True, authorization_mode="allow_with_scope", authorization_scope=scope,
            ), subject,
        )
        assert (expanded.authorization_mode, expanded.authorization_scope) == ("allow", "exact")


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True], ids=["session", "permanent"])
async def test_project_execution_config_remember_request_grants_only_exact_file(tmp_path, permanent):
    persist_session = Mock(return_value=True)
    persist_permanent = Mock(return_value=True)
    rail = PermissionInterruptRail(
        config={"enabled": True, "package_builtin_rules": False, "tools": {"write_file": "allow"}},
        host=ToolPermissionHost(
            resolve_workspace_dir=lambda: tmp_path,
            persist_session_allow_rule=persist_session,
            persist_allow_rule=persist_permanent,
        ),
    )
    target = str((tmp_path / "package.json").resolve())
    args = {"file_path": target, "content": "{}"}
    call = ToolCall(id="config", type="function", name="write_file", arguments=json.dumps(args))
    state = {}
    ctx = SimpleNamespace(session=SimpleNamespace(
        get_session_id=lambda: "s", get_state=state.get, update_state=state.update,
    ))

    interrupt = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(interrupt, InterruptResult)
    assert interrupt.request.metadata["matched_rule"] == "immutable_project_execution_config"

    decision = await rail.resolve_interrupt(ctx, call, {
        "approved": True,
        "auto_confirm": True,
        "persist_allow": permanent,
        "authorization_mode": "allow_with_scope",
        "authorization_scope": "parent",
    })
    assert isinstance(decision, ApproveResult)
    approval = rail.take_runtime_approval("s", call.id, call.name, args, include_operation=True)
    grants = approval["grants"]
    assert sorted(grant["tools"] for grant in grants) == [["file_resource"], ["write_file"]]
    for grant in grants:
        assert grant["match_type"] == "exact_operation"
        assert json.loads(grant["pattern"]) == {
            "scope": "exact", "subject": {"kind": "file", "accesses": [[target, "write"]]},
        }
    assert state == {}
    persist_session.assert_not_called()
    persist_permanent.assert_not_called()


@pytest.mark.asyncio
async def test_scope_normalization_retains_invalid_mode_rejection(tmp_path):
    rail = PermissionInterruptRail(config={"enabled": True, "package_builtin_rules": False})
    _payload, error, _result = await rail._validate_confirmation(
        PermissionConfirmResponse(approved=True, authorization_mode=""),
        "read_file", {"file_path": str(tmp_path / "A.txt")}, "s")
    assert error and "PERMISSION_DENIED" in error


@pytest.mark.asyncio
async def test_injected_engine_preserves_deny_without_snapshot(tmp_path):
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    target = tmp_path / "secret.txt"
    engine = PermissionEngine(
        {"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "allow"},
         "file_guard": {"enabled": True, "paths": [{"path": str(target), "read": "deny"}]}},
        workspace_root=tmp_path,
    )
    rail = PermissionInterruptRail(engine=engine)
    call = ToolCall(id="read", name="read_file", type="function",
                    arguments=json.dumps({"file_path": str(target)}))
    ctx = SimpleNamespace(session=SimpleNamespace(get_session_id=lambda: "s"))
    assert type(await rail.resolve_interrupt(ctx, call, None)).__name__ == "RejectResult"
    assert rail.runtime_engine("s").check_file_accesses([(target, "read")]).is_denied
