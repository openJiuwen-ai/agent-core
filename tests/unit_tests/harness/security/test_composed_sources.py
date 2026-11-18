"""Optional composed sources must not change unmarked permission hosts."""

from pathlib import Path

import pytest

from openjiuwen.harness.security.permission_engine.approve.operation_grants import build_operation_grant
from openjiuwen.harness.security.permission_engine.core import (
    COMPOSITION_SCHEMA_VERSION,
    PermissionEngine,
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.netguard.net_guard import build_net_guard_checker
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import evaluate_tiered_policy


def composition(order, layers=None):
    return {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "revision": "sec-test",
        "source_order": order,
        "net_url_layers": layers or {},
    }


def test_composition_skips_package_injection_and_rejects_bad_version():
    prepared = prepare_permissions_for_engine({
        "enabled": True, "package_builtin_rules": True,
        "file_guard": {"enabled": True, "paths": []},
        "net_guard": {"enabled": True, "urls": {}},
        "rules": [], "composition": composition(["user"]),
    })
    assert prepared["rules"] == []
    assert prepared["file_guard"]["paths"] == []
    assert prepared["net_guard"]["urls"] == {}
    with pytest.raises(ValueError, match="schema_version"):
        prepare_permissions_for_engine({"composition": {"schema_version": 999}})


def test_composed_unlayered_restriction_is_diagnosed_without_assigning_a_source(monkeypatch):
    from unittest.mock import Mock
    from openjiuwen.core.common.logging import logger

    warning = Mock()
    monkeypatch.setattr(logger, "warning", warning)
    rule = {"id": "missing-source", "tools": ["bash"], "pattern": "re:protected", "action": "ask"}
    prepared = prepare_permissions_for_engine({
        "enabled": True, "tools": {"bash": "allow"}, "rules": [rule],
        "composition": composition(["user", "builtin"]),
    })
    assert prepared["rules"] == [rule]
    warning.assert_called_once()
    assert "unlayered_restrictions" in warning.call_args.args[0]
    assert warning.call_args.args[1] == ["missing-source"]


def test_command_priority_deny_and_remembered_approval():
    cfg = {
        "enabled": True,
        "tools": {"bash": "allow"},
        "rules": [
            {"id": "user", "tools": ["bash"], "pattern": "demo *", "action": "allow", "layer": "user"},
            {"id": "cloud", "tools": ["bash"], "pattern": "demo *", "action": "ask", "layer": "cloud"},
            {"id": "builtin", "tools": ["bash"], "pattern": "demo *", "action": "ask", "layer": "builtin"},
        ],
        "approval_overrides": [{"tools": ["bash"], "pattern": "demo *", "action": "allow"}],
        "composition": composition(["cloud", "user", "builtin"]),
    }
    assert evaluate_tiered_policy(cfg, "bash", {"command": "demo x"})[0] == PermissionLevel.ASK
    cfg["composition"] = composition(["user", "cloud", "builtin"])
    assert evaluate_tiered_policy(cfg, "bash", {"command": "demo x"})[0] == PermissionLevel.ALLOW
    cfg["rules"][2]["action"] = "deny"
    assert evaluate_tiered_policy(cfg, "bash", {"command": "demo x"})[0] == PermissionLevel.DENY
    cfg["tools"] = {"mcp_custom": "deny", "bash": "allow"}
    assert evaluate_tiered_policy(cfg, "mcp_custom", {})[0] == PermissionLevel.DENY
    assert evaluate_tiered_policy(cfg, "mcp_other", {})[0] != PermissionLevel.DENY


def test_file_explicit_deny_and_missing_axis(tmp_path: Path):
    parent = tmp_path / "private"
    child = parent / "public"
    target = child / "data.txt"
    cfg = {
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
            "paths": [
                {"path": str(parent), "read": "deny", "layer": "cloud"},
                {"path": str(child), "read": "allow", "layer": "user"},
            ],
        },
        "composition": composition(["user", "cloud"]),
    }
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(target, "read")[0] == PermissionLevel.DENY
    cfg["file_guard"]["paths"][0] = {"path": str(parent), "read": "ask", "layer": "cloud"}
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(target, "read")[0] == PermissionLevel.ALLOW
    cfg["file_guard"]["paths"][1] = {"path": str(child), "write": "allow", "layer": "user"}
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(target, "read")[0] == PermissionLevel.ASK


def test_network_overlapping_patterns_follow_source_order():
    cfg = {
        "net_guard": {"enabled": True, "defaults": "allow", "urls": {
            "*.example.com": "ask", "api.example.com": "allow",
        }},
        "composition": composition(
            ["cloud", "user"],
            {"*.example.com": "user", "api.example.com": "cloud"},
        ),
    }
    checker = build_net_guard_checker(cfg)
    assert checker is not None
    assert checker.check_url("https://api.example.com/") is None
    cfg["composition"]["source_order"] = ["user", "cloud"]
    checker = build_net_guard_checker(cfg)
    assert checker.check_url("https://api.example.com/").permission == PermissionLevel.ASK
    cfg["net_guard"]["urls"]["api.example.com"] = "deny"
    checker = build_net_guard_checker(cfg)
    assert checker.check_url("https://api.example.com/").permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_tool_approval_override_cannot_bypass_higher_source_ask():
    cfg = {
        "enabled": True,
        "tools": {"bash": "allow"},
        "rules": [{"id": "cloud", "tools": ["bash"], "pattern": "demo *", "action": "ask", "layer": "cloud"}],
        "approval_overrides": [{"tools": ["bash"], "pattern": "demo *", "action": "allow"}],
        "composition": composition(["cloud", "user"]),
    }
    engine = PermissionEngine(cfg)
    result = await engine.check_permission("bash", {"command": "demo x"})
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_network_allow_does_not_lift_unmatched_tool_policy():
    cfg = {
        "enabled": True, "defer_unmatched": True,
        "net_guard": {"enabled": True, "urls": {"example.org": "allow"}},
        "composition": composition(["user"], {"example.org": "user"}),
    }
    engine = PermissionEngine(cfg)
    matched = await engine.check_permission("mcp_fetch_webpage", {"url": "https://example.org/"})
    assert matched.permission == PermissionLevel.UNDETERMINED
    direct, _ = engine.evaluate_global_policy_directly("mcp_fetch_webpage", {"url": "https://example.org/"})
    assert direct == PermissionLevel.UNDETERMINED
    unmatched = await engine.check_permission("mcp_fetch_webpage", {"url": "https://other.org/"})
    assert unmatched.permission == PermissionLevel.UNDETERMINED
    cfg["tools"] = {"mcp_fetch_webpage": "deny"}
    engine.update_config(cfg)
    denied = await engine.check_permission("mcp_fetch_webpage", {"url": "https://example.org/"})
    assert denied.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_file_allow_does_not_lift_unmatched_tool_policy(tmp_path: Path):
    target = tmp_path / "data.txt"
    cfg = {
        "enabled": True, "defer_unmatched": True,
        "file_guard": {"enabled": True, "defaults": {"read": "ask"},
                       "paths": [{"path": str(target), "read": "allow", "layer": "user"}]},
        "composition": composition(["user"]),
    }
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    result = await engine.check_permission("read_file", {"file_path": str(target)})
    assert result.permission == PermissionLevel.UNDETERMINED
    direct, _ = engine.evaluate_global_policy_directly("read_file", {"file_path": str(target)})
    assert direct == PermissionLevel.UNDETERMINED
    unmatched = await engine.check_permission("read_file", {"file_path": str(tmp_path / "other.txt")})
    assert unmatched.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_composed_file_ask_keeps_existing_operation_grant_behavior(tmp_path: Path):
    target = tmp_path / "data.txt"
    args = {"file_path": str(target)}
    cfg = {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "file_guard": {
            "enabled": True,
            "paths": [{"path": str(target), "read": "ask", "layer": "cloud"}],
        },
        "composition": composition(["cloud", "user"]),
    }
    cfg["approval_overrides"] = [build_operation_grant("read_file", args, cfg, tmp_path)]
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    assert (await engine.check_permission("read_file", args)).permission == PermissionLevel.ALLOW
