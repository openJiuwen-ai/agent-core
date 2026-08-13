# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P0: PermissionModeController presets, migrate, compose."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.mode import resolve_sandbox
from openjiuwen.harness.security.mode_controller import PermissionModeController
from openjiuwen.harness.security.mode_presets import MODE_PRESETS
from openjiuwen.harness.security.models import PermissionLevel


def test_presets_exist() -> None:
    assert set(MODE_PRESETS) == {"full_access", "auto", "strict"}
    assert MODE_PRESETS["full_access"]["sandbox_intent"] == "optional"
    assert MODE_PRESETS["full_access"]["severity_map"] == "normal"
    assert MODE_PRESETS["full_access"]["defaults"]["*"] == "allow"
    assert MODE_PRESETS["full_access"]["file_guard"]["enabled"] is False

    assert MODE_PRESETS["auto"]["sandbox_intent"] == "required"
    assert MODE_PRESETS["auto"]["defaults"]["*"] == "allow"
    assert MODE_PRESETS["auto"]["file_guard"]["enabled"] is True
    assert MODE_PRESETS["auto"]["file_guard"]["defaults"] == {
        "read": "allow",
        "write": "ask",
        "exec": "ask",
    }
    assert MODE_PRESETS["auto"]["file_guard"]["workspace"] == {
        "read": "allow",
        "write": "allow",
        "exec": "allow",
    }

    assert MODE_PRESETS["strict"]["sandbox_intent"] == "required"
    assert MODE_PRESETS["strict"]["severity_map"] == "strict"
    assert MODE_PRESETS["strict"]["defaults"]["*"] == "ask"
    assert MODE_PRESETS["strict"]["file_guard"]["defaults"] == {
        "read": "ask",
        "write": "ask",
        "exec": "ask",
    }
    assert MODE_PRESETS["strict"]["file_guard"]["workspace"] == {
        "read": "allow",
        "write": "ask",
        "exec": "ask",
    }


def test_resolve_sandbox() -> None:
    assert resolve_sandbox("optional", enabled=True, available=True) == ("sandbox", False)
    assert resolve_sandbox("optional", enabled=False, available=True) == ("host", False)
    assert resolve_sandbox("required", enabled=False, available=True) == ("sandbox", False)
    assert resolve_sandbox("required", enabled=True, available=False) == ("host", True)


def test_migrate_enabled_false_to_full_access() -> None:
    ctrl = PermissionModeController()
    out = ctrl.migrate_legacy({"enabled": False, "tools": {"bash": "ask"}})
    assert out["enabled"] is True
    assert out["mode"] == "full_access"
    assert out["ask_tools"] == ["bash"]
    assert "tools" not in out or out.get("tools") == {}


def test_migrate_permission_mode_strict() -> None:
    ctrl = PermissionModeController()
    out = ctrl.migrate_legacy({"enabled": True, "permission_mode": "strict"})
    assert out["mode"] == "strict"
    assert "permission_mode" not in out


def test_migrate_existing_mode_wins() -> None:
    ctrl = PermissionModeController()
    out = ctrl.migrate_legacy(
        {"enabled": True, "mode": "auto", "permission_mode": "strict"},
    )
    assert out["mode"] == "auto"
    assert "permission_mode" not in out


def test_compose_default_mode_auto() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True})
    assert eff.mode == "auto"
    assert eff.sandbox_intent == "required"
    assert eff.severity_map == "normal"
    assert eff.permissions["defaults"]["*"] == "allow"
    assert eff.permissions["file_guard"]["enabled"] is True
    assert eff.permissions["file_guard"]["defaults"]["write"] == "ask"
    assert eff.permissions["file_guard"]["workspace"]["write"] == "allow"
    assert eff.permissions["permission_mode"] == "normal"


def test_compose_full_access_forces_file_guard_off() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {
            "enabled": True,
            "mode": "full_access",
            "file_guard": {"enabled": True, "defaults": {"read": "ask", "write": "ask", "exec": "ask"}},
        },
    )
    assert eff.mode == "full_access"
    assert eff.sandbox_intent == "optional"
    assert eff.permissions["file_guard"]["enabled"] is False
    assert eff.permissions["defaults"]["*"] == "allow"


def test_compose_ignores_product_defaults() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto", "defaults": {"*": "ask"}})
    assert eff.permissions["defaults"]["*"] == "allow"


def test_compose_unknown_mode_falls_back_auto() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "weird"})
    assert eff.mode == "auto"


def test_compose_user_ask_tools_under_full_access() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "full_access"},
        {"ask_tools": ["bash"]},
    )
    assert eff.permissions["tools"]["bash"] == "ask"
    assert eff.permissions["defaults"]["*"] == "allow"


def test_compose_deny_wins_over_ask() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "auto"},
        {"ask_tools": ["bash"], "deny_tools": ["bash"]},
    )
    assert eff.permissions["tools"]["bash"] == "deny"


def test_compose_session_ignores_tool_lists() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "auto"},
        None,
        {"deny_tools": ["bash"], "ask_tools": ["read_file"]},
    )
    tools = eff.permissions.get("tools") or {}
    assert "bash" not in tools
    assert "read_file" not in tools


def test_compose_strict_sensitive_paths() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    paths = eff.permissions["file_guard"]["paths"]
    patterns = {p["path"] for p in paths}
    assert "**/.ssh/**" in patterns
    assert "**/.env*" in patterns
    assert eff.permissions["permission_mode"] == "strict"
    assert eff.permissions["defaults"]["*"] == "ask"
    assert eff.permissions["file_guard"]["workspace"]["read"] == "allow"
    assert eff.permissions["tools"]["read_file"] == "allow"
    assert eff.permissions["tools"]["write_file"] == "allow"


@pytest.mark.asyncio
async def test_engine_full_access_skips_file_guard(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {
            "enabled": True,
            "mode": "full_access",
            "ask_tools": ["bash"],
        },
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    # path outside workspace would ASK under Auto FG; FA skips B
    outside = tmp_path / "secret" / ".ssh" / "id_rsa"
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ALLOW
    # user ask tool still asks
    bash_result = await engine.check_permission("bash", {"command": "echo hi"})
    assert bash_result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_auto_file_guard_miss_allow(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_auto_workspace_write_allow(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(workspace / "a.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_auto_outside_write_ask(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(outside), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_auto_write_parent_of_workspace_asks(tmp_path: Path) -> None:
    """当前任务 workspace 的父目录是区外写入，Auto 应收紧为 ASK。"""
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    engine = PermissionEngine(eff.permissions, workspace_root=project)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(agent_ws / "leak.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_update_workspace_root_narrows_write_allow(tmp_path: Path) -> None:
    """workspace_root 从 agent 根收窄到任务目录后，父目录写入应变 ASK。"""
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    target = agent_ws / "leak.txt"
    engine = PermissionEngine(eff.permissions, workspace_root=agent_ws)
    allowed = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "x"},
    )
    assert allowed.permission == PermissionLevel.ALLOW
    engine.update_workspace_root(project)
    asked = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "x"},
    )
    assert asked.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_strict_file_guard_miss_ask(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_strict_workspace_read_allow(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("x", encoding="utf-8")
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(target)})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_strict_workspace_write_ask(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(workspace / "a.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


def test_compose_strict_user_ask_path_tool_not_overridden() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        {"ask_tools": ["read_file"]},
    )
    assert eff.permissions["tools"]["read_file"] == "ask"


@pytest.mark.asyncio
async def test_engine_strict_env_deny(tmp_path: Path) -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env_file = workspace / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    engine = PermissionEngine(eff.permissions, workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(env_file)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_engine_strict_unknown_tool_asks() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "strict"})
    engine = PermissionEngine(eff.permissions)
    result = await engine.check_permission("custom_tool", {})
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_strict_user_allow_tools() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={"allow_tools": ["todo_list"]},
    )
    engine = PermissionEngine(eff.permissions)
    result = await engine.check_permission("todo_list", {})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_auto_unknown_tool_allows() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    engine = PermissionEngine(eff.permissions)
    result = await engine.check_permission("custom_tool", {})
    assert result.permission == PermissionLevel.ALLOW


def test_migrate_then_compose_enabled_false() -> None:
    ctrl = PermissionModeController()
    raw = ctrl.migrate_legacy({"enabled": False})
    eff = ctrl.compose(raw)
    assert eff.permissions["enabled"] is True
    assert eff.mode == "full_access"
    assert eff.sandbox_intent == "optional"


def test_factory_mounts_rail_for_legacy_enabled_false() -> None:
    from openjiuwen.harness.security.factory import build_permission_interrupt_rail

    rail = build_permission_interrupt_rail(permissions={"enabled": False})
    assert rail is not None
    assert rail.permission_mode == "full_access"
    assert rail.sandbox_intent == "optional"
    assert rail._static_config["enabled"] is True


def test_compose_effective_permissions_helper() -> None:
    from openjiuwen.harness.security.factory import compose_effective_permissions

    eff = compose_effective_permissions(
        {"enabled": True, "mode": "strict"},
        user_permissions={"ask_tools": ["bash"]},
    )
    assert eff.mode == "strict"
    assert eff.permissions["tools"]["bash"] == "ask"


def test_migrate_tools_allow_to_allow_tools() -> None:
    ctrl = PermissionModeController()
    out = ctrl.migrate_legacy({"enabled": True, "tools": {"todo_list": "allow", "bash": "ask"}})
    assert out["allow_tools"] == ["todo_list"]
    assert out["ask_tools"] == ["bash"]
    assert "tools" not in out


def test_compose_session_allow_tools_merges_under_strict() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={"allow_tools": ["todo_list"]},
        session_cfg={"allow_tools": ["memory_get"], "ask_tools": ["bash"]},
    )
    tools = eff.permissions.get("tools") or {}
    assert tools.get("todo_list") == "allow"
    assert tools.get("memory_get") == "allow"
    assert "bash" not in tools  # Session ask_tools stripped
    assert eff.permissions["defaults"]["*"] == "ask"
    assert "bash" not in (eff.permissions.get("ask_tools") or [])


def test_compose_refeed_effective_keeps_allow_tools() -> None:
    """rail 把 Host 已合成的 effective 再当 Global compose 时，allow 必须保留。"""
    ctrl = PermissionModeController()
    first = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={"allow_tools": ["write_file", "todo_list"]},
    )
    assert (first.permissions.get("tools") or {}).get("write_file") == "allow"

    # 模拟 tool_security_rail.update_config → compose_effective_permissions(effective)
    second = ctrl.compose(first.permissions)
    assert "write_file" in (second.permissions.get("allow_tools") or [])
    assert (second.permissions.get("tools") or {}).get("write_file") == "allow"
    assert (second.permissions.get("tools") or {}).get("todo_list") == "allow"


def test_compose_precedence_deny_ask_allow() -> None:
    ctrl = PermissionModeController()
    eff = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={
            "deny_tools": ["a"],
            "ask_tools": ["b", "c"],
            "allow_tools": ["b", "c", "d"],
        },
    )
    tools = eff.permissions.get("tools") or {}
    assert tools["a"] == "deny"
    assert tools["b"] == "ask"  # ask wins over allow
    assert tools["c"] == "ask"
    assert tools["d"] == "allow"
