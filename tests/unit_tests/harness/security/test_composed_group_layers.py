"""Group layers use native source decisions and preserve the v1 host contract."""

from copy import deepcopy

import pytest

from openjiuwen.harness.security.permission_engine.core import (
    COMPOSITION_SCHEMA_VERSION,
    SUPPORTED_COMPOSITION_SCHEMA_VERSIONS,
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.netguard.net_guard import build_net_guard_checker
from openjiuwen.harness.security.permission_engine.policy_sources import (
    DEFAULT_LAYER_ORDER,
    DEFAULT_SOURCE_ORDER,
    validate_policy_sources,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import evaluate_tiered_policy


def snapshot(order=DEFAULT_LAYER_ORDER, **metadata):
    return {"schema_version": 2, "revision": "sec-test", "source_order": list(order), **metadata}


def test_legacy_marker_and_default_are_preserved():
    assert COMPOSITION_SCHEMA_VERSION == 1
    assert DEFAULT_SOURCE_ORDER == ("user", "cloud", "builtin")
    assert SUPPORTED_COMPOSITION_SCHEMA_VERSIONS == (1, 2)
    validate_policy_sources([{"id": layer, "enabled": True} for layer in DEFAULT_LAYER_ORDER])
    legacy = {"composition": {**snapshot(DEFAULT_SOURCE_ORDER), "schema_version": 1}, "rules": []}
    assert prepare_permissions_for_engine(legacy) == legacy


@pytest.mark.parametrize(
    "metadata",
    [
        {"schema_version": 1},
        {"schema_version": True},
        {"schema_version": 3},
        {"source_order": ["user_group"]},
        {"source_order": ["user", "user", "user_group"]},
        {"workspace_layer": "cloud_group"},
        {"workspace_layer": {"id": "user_group"}},
    ],
)
def test_bad_or_incompatible_snapshot_is_rejected(metadata):
    with pytest.raises(ValueError):
        prepare_permissions_for_engine({"composition": snapshot(**metadata)})


@pytest.mark.parametrize(
    "first,second,expected",
    [
        ("allow", "ask", PermissionLevel.ALLOW),
        ("ask", "allow", PermissionLevel.ASK),
        ("allow", "deny", PermissionLevel.DENY),
    ],
)
@pytest.mark.parametrize("owner", DEFAULT_SOURCE_ORDER)
def test_independent_and_group_command_priority(owner, first, second, expected):
    cfg = {
        "tools": {"bash": "allow"},
        "rules": [
            {"id": layer, "tools": ["bash"], "pattern": "demo *", "action": action, "layer": layer}
            for layer, action in ((owner, first), (f"{owner}_group", second))
        ],
        "composition": snapshot(),
    }
    prepared = prepare_permissions_for_engine(cfg)
    assert evaluate_tiered_policy(prepared, "bash", {"command": "demo x"})[0] == expected
    assert prepared == cfg


def test_builtin_switch_also_suppresses_builtin_group_command_rules():
    cfg = {
        "tools": {"bash": "allow"},
        "shell_guard": {"builtin_rules_enabled": False},
        "rules": [
            {"id": "package-group", "tools": ["bash"], "pattern": "demo *", "action": "deny", "layer": "builtin_group"}
        ],
        "composition": snapshot(),
    }
    assert evaluate_tiered_policy(cfg, "bash", {"command": "demo x"})[0] == PermissionLevel.ALLOW


def test_workspace_group_has_only_explicit_axes_and_follows_order(tmp_path):
    cfg = {
        "composition": snapshot(workspace_layer="user_group"),
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "deny", "write": "ask", "exec": "deny"},
            "workspace": {"read": "allow", "write": "allow"},
            "paths": [{"path": str(tmp_path), "write": "ask", "layer": "user"}],
        },
    }
    original = deepcopy(cfg)
    checker = FileGuardChecker(
        normalize_path_guard_config(prepare_permissions_for_engine(cfg), workspace_root=tmp_path)
    )
    target = tmp_path / "file.txt"
    assert checker._resolve_one(target, "read")[0] == PermissionLevel.ALLOW
    assert checker._resolve_one(target, "write")[0] == PermissionLevel.ASK
    assert checker._resolve_one(target, "exec")[0] == PermissionLevel.DENY
    cfg["composition"]["source_order"] = ["user_group", "user"]
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(target, "write")[0] == PermissionLevel.ALLOW
    cfg["file_guard"]["paths"][0]["write"] = "deny"
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(target, "write")[0] == PermissionLevel.DENY
    assert original["file_guard"]["workspace"] == {"read": "allow", "write": "allow"}


@pytest.mark.parametrize("order", [["user"], []])
def test_disabled_group_or_owner_has_no_workspace_contribution(tmp_path, order):
    cfg = {
        "composition": snapshot(order, workspace_layer="user_group"),
        "file_guard": {"enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "allow"}},
    }
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(tmp_path / "file.txt", "read")[0] == PermissionLevel.ASK


def test_workspace_deny_still_clamps_write_and_exec(tmp_path):
    cfg = {
        "composition": snapshot(workspace_layer="user_group"),
        "file_guard": {"enabled": True, "defaults": {"write": "allow", "exec": "allow"}, "workspace": {"read": "deny"}},
    }
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    for axis in ("read", "write", "exec"):
        assert checker._resolve_one(tmp_path / "file.txt", axis)[0] == PermissionLevel.DENY


def test_network_group_obeys_order_and_deny_first():
    cfg = {
        "composition": snapshot(net_url_layers={"api.example.org": "user", "*.example.org": "user_group"}),
        "net_guard": {"enabled": True, "urls": {"api.example.org": "allow", "*.example.org": "ask"}},
    }
    checker = build_net_guard_checker(prepare_permissions_for_engine(cfg))
    assert checker.check_url("https://api.example.org/") is None
    cfg["net_guard"]["urls"]["*.example.org"] = "deny"
    checker = build_net_guard_checker(cfg)
    assert checker.check_url("https://api.example.org/").permission == PermissionLevel.DENY


def test_v1_workspace_keeps_legacy_missing_axis_ask(tmp_path):
    cfg = {
        "composition": {**snapshot(DEFAULT_SOURCE_ORDER), "schema_version": 1},
        "file_guard": {"enabled": True, "defaults": {"exec": "allow"}, "workspace": {"read": "allow"}},
    }
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path))
    assert checker._resolve_one(tmp_path / "file.txt", "exec")[0] == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engines_bind_same_group_to_different_roots_without_granting_other_root(tmp_path):
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    root_a, root_b = tmp_path / "a", tmp_path / "b"
    cfg = {
        "enabled": True,
        "tools": {"read_file": "allow"},
        "composition": snapshot(workspace_layer="user_group"),
        "file_guard": {"enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "allow"}},
    }
    original = deepcopy(cfg)
    engine_a = PermissionEngine(cfg, workspace_root=root_a)
    engine_b = PermissionEngine(cfg, workspace_root=root_b)
    for engine, own, other in ((engine_a, root_a, root_b), (engine_b, root_b, root_a)):
        allowed = await engine.check_permission("read_file", {"file_path": str(own / "data")})
        outside = await engine.check_permission("read_file", {"file_path": str(other / "data")})
        assert allowed.permission == PermissionLevel.ALLOW
        assert outside.permission == PermissionLevel.ASK
    assert cfg == original
