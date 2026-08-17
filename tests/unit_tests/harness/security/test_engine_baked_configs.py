# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine evaluates Host-baked permission dicts (no product-mode compose).

Product triad compose lives in jiuwenswarm. This module only checks that
PermissionEngine / factory consume already-baked allow/ask/deny documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.security.engine import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel

from tests.unit_tests.harness.security._baked import (
    baked_unrestricted,
    baked_workspace_ask,
    baked_workspace_trust,
)


@pytest.mark.asyncio
async def test_engine_file_guard_off_allows_outside_read(tmp_path: Path) -> None:
    cfg = baked_unrestricted(ask_tools=["bash"], tools={"bash": "ask"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(cfg, workspace_root=workspace)
    outside = tmp_path / "secret" / ".ssh" / "id_rsa"
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ALLOW
    bash_result = await engine.check_permission("bash", {"command": "echo hi"})
    assert bash_result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_unmatched_read_outside_workspace_allows(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_workspace_write_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(workspace / "a.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_outside_write_ask(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(outside), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_write_parent_of_workspace_asks(tmp_path: Path) -> None:
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=project)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(agent_ws / "leak.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_update_workspace_root_narrows_write_allow(tmp_path: Path) -> None:
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    target = agent_ws / "leak.txt"
    engine = PermissionEngine(baked_workspace_trust(), workspace_root=agent_ws)
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
async def test_engine_unmatched_read_asks_when_defaults_ask(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "a.txt"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_ask(), workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(outside)})
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_workspace_read_allow_when_workspace_read_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("x", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_ask(), workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(target)})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_workspace_write_ask_when_workspace_write_ask(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(baked_workspace_ask(), workspace_root=workspace)
    result = await engine.check_permission(
        "write_file",
        {"file_path": str(workspace / "a.txt"), "content": "x"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_env_path_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env_file = workspace / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    engine = PermissionEngine(baked_workspace_ask(), workspace_root=workspace)
    result = await engine.check_permission("read_file", {"file_path": str(env_file)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_engine_unknown_tool_asks_when_default_ask() -> None:
    engine = PermissionEngine(baked_workspace_ask())
    result = await engine.check_permission("custom_tool", {})
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_allow_tools_overrides_default_ask() -> None:
    cfg = baked_workspace_ask()
    cfg.setdefault("allow_tools", []).append("todo_list")
    tools = cfg.setdefault("tools", {})
    tools["todo_list"] = "allow"
    engine = PermissionEngine(cfg)
    result = await engine.check_permission("todo_list", {})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_engine_unknown_tool_allows_when_default_allow() -> None:
    engine = PermissionEngine(baked_workspace_trust())
    result = await engine.check_permission("custom_tool", {})
    assert result.permission == PermissionLevel.ALLOW


def test_compose_helper_removed_from_factory() -> None:
    import openjiuwen.harness.security.engine.factory as factory
    import openjiuwen.harness.security as security

    assert not hasattr(factory, "compose_effective_permissions")
    assert not hasattr(security, "compose_effective_permissions")
    assert not hasattr(security, "PermissionModeController")
    assert not hasattr(security, "MODE_PRESETS")


def test_rail_does_not_compose_auto_preset_from_product_mode() -> None:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

    rail = PermissionInterruptRail(config={"enabled": True, "mode": "auto"})
    cfg = rail._static_config
    assert cfg.get("defaults") == {"*": "ask"}
    assert cfg.get("file_guard", {}).get("defaults") == {
        "read": "ask",
        "write": "ask",
        "exec": "ask",
    }
    assert cfg.get("file_guard", {}).get("workspace") in (None, {})


def test_rail_uses_baked_defaults() -> None:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {"enabled": False},
            "mode": "full_access",
            "sandbox_intent": "optional",
        }
    )
    assert rail._static_config["defaults"]["*"] == "allow"
    assert rail._static_config["file_guard"]["enabled"] is False
    assert rail.sandbox_intent == "optional"
    assert rail.permission_mode == "full_access"


def test_rail_constructor_has_no_product_mode_kwargs() -> None:
    import inspect

    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

    params = inspect.signature(PermissionInterruptRail.__init__).parameters
    assert "permission_mode" not in params
    assert "sandbox_intent" not in params


def test_factory_does_not_migrate_enabled_false() -> None:
    from openjiuwen.harness.security.engine.factory import build_permission_interrupt_rail

    assert build_permission_interrupt_rail(permissions={"enabled": False}) is None


def test_factory_passes_baked_config_through() -> None:
    from openjiuwen.harness.security.engine.factory import build_permission_interrupt_rail

    rail = build_permission_interrupt_rail(
        permissions={
            "enabled": True,
            "defaults": {"*": "ask"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            },
            "mode": "strict",
            "sandbox_intent": "required",
        }
    )
    assert rail is not None
    assert rail._static_config["defaults"]["*"] == "ask"
    assert rail.sandbox_intent == "required"


def test_resolve_sandbox_not_exported() -> None:
    import openjiuwen.harness.security as security

    assert not hasattr(security, "resolve_sandbox")
    assert "resolve_sandbox" not in getattr(security, "__all__", ())


def test_security_package_has_no_product_mode_enum() -> None:
    import openjiuwen.harness.security as security

    root = Path(security.__file__).resolve().parent
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "full_access" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []
