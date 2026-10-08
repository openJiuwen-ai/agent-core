"""ASK approvals must be bounded by objects, actions, lifetime and guard DENY."""

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
    has_operation_grant,
    operation_subject,
    scope_options,
)
from openjiuwen.harness.security.permission_engine.core import PermissionEngine


def config():
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "tools": {},
        "defaults": {"*": "allow"},
        "net_guard": {"enabled": True, "defaults": "ask", "urls": {}},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "ask", "write": "ask", "exec": "ask"},
        },
    }


@pytest.mark.asyncio
async def test_file_exact_and_parent_do_not_allow_different_actions_or_denied_paths(tmp_path):
    cfg = config()
    args = {"file_path": str(tmp_path / "a.txt")}
    grant = build_operation_grant("read_file", args, cfg, tmp_path)
    cfg["approval_overrides"] = [grant]
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    assert (await engine.check_permission("read_file", args)).is_allowed
    assert (await engine.check_permission("read_file", {"file_path": str(tmp_path / "b.txt")})).needs_approval
    assert (await engine.check_permission("write_file", args)).needs_approval
    cfg["approval_overrides"] = [
        build_operation_grant("read_file", args, cfg, tmp_path, mode="allow_with_scope", scope="parent")
    ]
    cfg["file_guard"]["paths"] = [{"path": str(tmp_path / "secret.txt"), "read": "deny"}]
    engine.update_config(cfg)
    assert (await engine.check_permission("read_file", {"file_path": str(tmp_path / "b.txt")})).is_allowed
    assert (await engine.check_permission("read_file", {"file_path": str(tmp_path.parent / "b.txt")})).needs_approval
    assert (await engine.check_permission("read_file", {"file_path": str(tmp_path / "secret.txt")})).is_denied


@pytest.mark.asyncio
async def test_network_exact_domain_and_deny(tmp_path):
    cfg = config()
    tool, args = "mcp_fetch_webpage", {"url": "https://api.example.co.uk/a"}
    cfg["approval_overrides"] = [build_operation_grant(tool, args, cfg)]
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission(tool, args)).is_allowed
    assert (await engine.check_permission(tool, {"url": "https://api.example.co.uk/b"})).needs_approval
    cfg["approval_overrides"] = [build_operation_grant(tool, args, cfg, mode="allow_with_scope", scope="domain")]
    cfg["net_guard"]["urls"] = {"https://api.example.co.uk/secret": "deny"}
    engine.update_config(cfg)
    assert (await engine.check_permission(tool, {"url": "https://other.example.co.uk/b"})).is_allowed
    for url in [
        "https://other.co.uk/",
        "https://example.co.uk.evil.com/",
        "http://api.example.co.uk/",
        "https://api.example.co.uk:444/",
    ]:
        assert (await engine.check_permission(tool, {"url": url})).needs_approval
    assert (await engine.check_permission(tool, {"url": "https://api.example.co.uk/secret"})).is_denied


def test_command_grants_are_literal_unsplit_and_cwd_bound(tmp_path):
    cfg = config()
    args = {"command": "echo * && echo done", "workdir": str(tmp_path), "shell_type": "bash"}
    cfg["approval_overrides"] = [build_operation_grant("bash", args, cfg)]
    assert has_operation_grant(cfg, "bash", args)
    for changed in [
        {**args, "command": "echo secret && echo done"},
        {**args, "command": "echo *"},
        {**args, "workdir": str(tmp_path.parent)},
        {**args, "cwd": str(tmp_path), "workdir": str(tmp_path.parent)},
        {**args, "shell_type": "powershell"},
        {**args, "shell_type": "cmd"},
    ]:
        assert not has_operation_grant(cfg, "bash", changed)
    with pytest.raises(ValueError):
        build_operation_grant("bash", args, cfg, mode="allow_with_scope", scope="parent")


@pytest.mark.parametrize("tool", ["bash", "powershell", "core.powershell", "mcp_exec_command"])
def test_command_grants_ignore_spoofed_cwd_and_bind_workdir(tmp_path, tool):
    cfg = config()
    args = {"command": "cat relative.txt", "workdir": str(tmp_path), "shell_type": "bash"}
    cfg["approval_overrides"] = [build_operation_grant(tool, args, cfg)]
    assert has_operation_grant(cfg, tool, {**args, "cwd": str(tmp_path.parent)})
    assert not has_operation_grant(cfg, tool, {
        **args, "cwd": str(tmp_path), "workdir": str(tmp_path.parent),
    })


def test_relative_command_grant_is_bound_to_agent_context(tmp_path, monkeypatch):
    from openjiuwen.harness.security.permission_engine.approve import operation_grants as mod

    cfg = config()
    monkeypatch.setattr(mod, "get_cwd", lambda: str(tmp_path))
    args = {"command": "cat file.txt", "workdir": ".", "shell_type": "bash"}
    cfg["approval_overrides"] = [build_operation_grant("mcp_exec_command", args, cfg)]
    monkeypatch.setattr(mod, "get_cwd", lambda: str(tmp_path.parent))
    assert not has_operation_grant(cfg, "mcp_exec_command", args)


def test_scope_validation_and_ip_urls(tmp_path):
    with pytest.raises(ValueError):
        build_operation_grant(
            "read_file", {"file_path": str(tmp_path / "a")}, config(), tmp_path, mode="allow", scope="parent"
        )
    for url in ["http://localhost/", "http://127.0.0.1/", "http://[::1]/", "https://co.uk/"]:
        subject = operation_subject("mcp_fetch_webpage", {"url": url}, config())
        assert [o["value"] for o in scope_options(subject)] == ["exact"], url


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["once", "session", "always", "reject"])
async def test_rail_interrupt_resume_and_lifetime(tmp_path, choice):
    disk, session_cfg, state = config(), {}, {}
    writes = []

    def snapshot():
        return {
            **disk,
            "approval_overrides": [*disk.get("approval_overrides", []), *session_cfg.get("approval_overrides", [])],
        }

    def persist(target, delta):
        target.update(deepcopy(delta))
        writes.append(deepcopy(delta))
        return True

    host = ToolPermissionHost(
        get_permissions_snapshot=snapshot,
        resolve_workspace_dir=lambda: tmp_path,
        persist_allow_rule=lambda delta: persist(disk, delta),
        persist_session_allow_rule=lambda delta: persist(session_cfg, delta),
    )
    rail = PermissionInterruptRail(config=disk, host=host)
    session = SimpleNamespace(get_state=lambda key: state.get(key), update_state=state.update, session_id="one")
    ctx = AgentCallbackContext(agent=object(), session=session)
    call = ToolCall(id="one", type="function", name="fetch_webpage", arguments='{"url":"https://example.com/a"}')
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
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
    other = ToolCall(id="two", type="function", name="fetch_webpage", arguments='{"url":"https://example.com/b"}')
    assert isinstance(await rail.resolve_interrupt(ctx, other, None), InterruptResult)
    # A different session loads permanent grants only.
    other_engine = PermissionEngine(disk)
    check = await other_engine.check_permission("mcp_fetch_webpage", {"url": "https://example.com/a"})
    assert check.is_allowed == (choice == "always")
    assert bool(writes) == (choice in {"session", "always"})


@pytest.mark.asyncio
async def test_deny_added_while_waiting_blocks_resume():
    cfg = config()
    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(get_permissions_snapshot=lambda: cfg))
    ctx = AgentCallbackContext(agent=object(), session=None)
    call = ToolCall(id="one", type="function", name="fetch_webpage", arguments='{"url":"https://example.com/a"}')
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
    cfg["net_guard"]["urls"]["example.com"] = "deny"
    assert isinstance(await rail.resolve_interrupt(ctx, call, {"approved": True}), RejectResult)
