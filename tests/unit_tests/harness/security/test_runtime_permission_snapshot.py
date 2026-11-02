"""A v2 rail refreshes the host root and refuses a missing required snapshot."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel


def permissions():
    return {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "composition": {"schema_version": 2, "source_order": ["user", "user_group"], "workspace_layer": "user_group"},
        "file_guard": {"enabled": True, "defaults": {"read": "deny"}, "workspace": {"read": "allow"}},
    }


def callback(target):
    tool = ToolCall(id="read", type="function", name="read_file", arguments=json.dumps({"file_path": str(target)}))
    session = SimpleNamespace(get_state=lambda *_: None, session_id="session-a")
    return SimpleNamespace(session=session, extra={}, inputs=SimpleNamespace(tool_call=tool)), tool


@pytest.mark.asyncio
async def test_runtime_root_is_refreshed_and_missing_root_revokes_old_workspace(tmp_path):
    root = [None]
    cfg = permissions()
    host = ToolPermissionHost(get_permissions_snapshot=lambda **_: deepcopy(cfg), resolve_workspace_dir=lambda: root[0])
    rail = PermissionInterruptRail(config=cfg, host=host)
    target_a = tmp_path / "a" / "data"
    ctx, call = callback(target_a)
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), RejectResult)
    root[0] = tmp_path / "a"
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)
    root[0] = tmp_path / "b"
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), RejectResult)
    ctx_b, call_b = callback(tmp_path / "b" / "data")
    assert isinstance(await rail.resolve_interrupt(ctx_b, call_b, None), ApproveResult)
    root[0] = None
    assert isinstance(await rail.resolve_interrupt(ctx_b, call_b, None), RejectResult)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "error"])
async def test_required_snapshot_failure_cannot_fall_back_to_old_allow(failure):
    def snapshot():
        if failure == "error":
            raise RuntimeError("snapshot unavailable")
        return None

    rail = PermissionInterruptRail(
        config={"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "allow"}},
        host=ToolPermissionHost(get_permissions_snapshot=snapshot, permissions_snapshot_required=True),
    )
    ctx, call = callback("/data")
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, RejectResult)


@pytest.mark.asyncio
async def test_legacy_snapshot_failure_keeps_static_fallback():
    rail = PermissionInterruptRail(
        config={"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "allow"}},
        host=ToolPermissionHost(get_permissions_snapshot=lambda: None),
    )
    ctx, call = callback("/data")
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)


@pytest.mark.asyncio
async def test_relative_operands_use_execution_cwd_without_changing_authorized_root(tmp_path, monkeypatch):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    cwd = [outside]
    monkeypatch.setattr(
        "openjiuwen.harness.security.permission_engine.fileguard.path_extract.get_cwd",
        lambda: str(cwd[0]),
    )
    engine = PermissionEngine(permissions(), workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": "data"})
    assert result.permission == PermissionLevel.DENY
    cwd[0] = workspace
    result = await engine.check_permission("read_file", {"file_path": "data"})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_static_deny_is_checked_even_without_workspace_root(tmp_path):
    cfg = permissions()
    cfg["file_guard"]["defaults"]["read"] = "allow"
    cfg["file_guard"]["paths"] = [{"path": str(tmp_path), "read": "deny", "layer": "user"}]
    engine = PermissionEngine(cfg)
    result = await engine.check_permission("read_file", {"file_path": str(tmp_path / "data")})
    assert result.permission == PermissionLevel.DENY
