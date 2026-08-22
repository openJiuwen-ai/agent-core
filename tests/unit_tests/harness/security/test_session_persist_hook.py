# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session vs User persist hooks on PermissionInterruptRail."""

from __future__ import annotations

from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost


def test_session_remember_calls_session_persist_hook() -> None:
    user_calls: list[dict] = []
    session_calls: list[dict] = []

    host = ToolPermissionHost(
        persist_allow_rule=lambda cfg: user_calls.append(cfg) or True,
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_session_allow("bash", {"command": "git status"})
    assert ok is True
    assert session_calls
    assert user_calls == []
    overrides = session_calls[0].get("approval_overrides") or []
    assert any(
        isinstance(o, dict) and o.get("action") == "allow" for o in overrides
    )


def test_permanent_remember_calls_user_persist_hook() -> None:
    user_calls: list[dict] = []
    session_calls: list[dict] = []
    host = ToolPermissionHost(
        persist_allow_rule=lambda cfg: user_calls.append(cfg) or True,
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_allow_always("bash", {"command": "git status"})
    assert ok is True
    assert user_calls
    assert session_calls == []


def test_builtin_deny_does_not_persist_session_allow() -> None:
    from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import inline_package_command_rules

    session_calls: list[dict] = []
    effective = inline_package_command_rules(
        {
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    host = ToolPermissionHost(
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: effective,
    )
    rail = PermissionInterruptRail(config=effective, host=host)
    ok = rail._persist_session_allow("bash", {"command": "shutdown -h now"})
    assert ok is False
    assert session_calls == []
