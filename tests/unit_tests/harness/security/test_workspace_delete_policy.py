"""A scoped native delete grant still runs every existing guard."""

from copy import deepcopy

import pytest

from openjiuwen.harness.security.permission_engine.core import PermissionEngine, prepare_permissions_for_engine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.workspace_delete import LocalDeleteExecution


def config():
    return {
        "enabled": True,
        "defer_unmatched": True,
        "strict_tool_command_merge": True,
        "tools": {"bash": "ask"},
        "rules": [{"id": "product-delete", "tools": ["shell"], "pattern": "rm -- *", "action": "ask", "layer": "user"}],
        "composition": {
            "schema_version": 2,
            "source_order": ["user", "user_group", "cloud", "builtin"],
            "workspace_layer": "user_group",
            "tool_layers": {"user": {"bash": "ask"}, "user_group": {}, "cloud": {}, "builtin": {}},
            "workspace_delete_defaults": {
                "schema_version": 1,
                "rules": [{"layer": "user", "id": "product-delete"}],
                "tools": [{"layer": "user", "name": "bash"}],
            },
        },
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "allow"},
            "workspace": {"read": "allow", "write": "allow"},
        },
    }


async def check(cfg, root, command="rm -- data", *, proof=True, **kwargs):
    engine = PermissionEngine(cfg, workspace_root=root, **kwargs)
    extra = {"workspace_delete_execution": LocalDeleteExecution("posix", root)} if proof else {}
    return await engine.check_permission(
        "bash", {"command": command, "workdir": str(root), "shell_type": "bash"}, **extra
    )


@pytest.mark.asyncio
async def test_known_default_asks_are_lifted_only_with_a_host_execution_proof(tmp_path):
    cfg = config()
    result = await check(cfg, tmp_path)
    assert result.permission == PermissionLevel.ALLOW
    assert "workspace_delete" in result.matched_rule
    assert cfg == config()  # A per-operation policy input never changes the shared snapshot.
    assert (await check(cfg, tmp_path, proof=False)).permission == PermissionLevel.ASK


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ["user", "cloud", "builtin"])
@pytest.mark.parametrize("action", ["ask", "deny"])
async def test_other_explicit_command_rules_survive_default_relaxation(tmp_path, layer, action):
    cfg = config()
    cfg["rules"].append({"id": "explicit", "tools": ["shell"], "pattern": "rm -- *", "action": action, "layer": layer})
    assert (await check(cfg, tmp_path)).permission == PermissionLevel(action)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["ask", "deny"])
async def test_explicit_tool_and_path_restrictions_survive(tmp_path, action):
    cfg = config()
    cfg["composition"]["workspace_delete_defaults"]["tools"] = []
    cfg["tools"]["bash"] = action
    cfg["composition"]["tool_layers"]["user"]["bash"] = action
    assert (await check(cfg, tmp_path)).permission == PermissionLevel(action)
    cfg = config()
    cfg["file_guard"]["paths"] = [{"path": str(tmp_path), "match": "prefix", "write": action, "layer": "user"}]
    assert (await check(cfg, tmp_path)).permission == PermissionLevel(action)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["ask", "deny"])
async def test_removing_a_controlled_tool_default_exposes_other_source_restrictions(tmp_path, action):
    cfg = config()
    cfg["composition"]["tool_layers"]["cloud"]["bash"] = action
    if action == "deny":
        cfg["tools"]["bash"] = "deny"
    assert (await check(cfg, tmp_path)).permission == PermissionLevel(action)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["off", "no-workspace", "no-write", "ask", "no-layer", "stale-tools", "no-metadata"])
async def test_revoking_or_staling_the_workspace_grant_restores_ordinary_ask(tmp_path, change):
    cfg = config()
    if change == "off":
        cfg["file_guard"]["enabled"] = False
    elif change == "no-workspace":
        cfg["file_guard"].pop("workspace")
    elif change == "no-write":
        cfg["file_guard"]["workspace"].pop("write")
    elif change == "ask":
        cfg["file_guard"]["workspace"]["write"] = "ask"
    elif change == "no-layer":
        cfg["composition"]["source_order"].remove("user_group")
        cfg["composition"]["tool_layers"].pop("user_group")
    elif change == "stale-tools":
        cfg["tools"]["bash"] = "allow"
    else:
        cfg["composition"].pop("workspace_delete_defaults")
    assert (await check(cfg, tmp_path)).permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_matching_id_without_trusted_provenance_does_not_qualify(tmp_path):
    cfg = config()
    cfg["composition"]["workspace_delete_defaults"]["rules"] = []
    assert (await check(cfg, tmp_path)).permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_unrelated_runtime_tool_values_do_not_change_the_verified_delete_gate(tmp_path):
    cfg = config()
    cfg["tools"]["read_file"] = "deny"
    assert (await check(cfg, tmp_path)).permission == PermissionLevel.ALLOW
    assert cfg["tools"]["read_file"] == "deny"


@pytest.mark.asyncio
async def test_a_non_shell_category_cannot_consume_a_delete_exception(tmp_path):
    cfg = config()
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    result = await engine.check_permission(
        "read_file",
        {"command": "rm -- data", "workdir": str(tmp_path)},
        workspace_delete_execution=LocalDeleteExecution("posix", tmp_path),
    )
    assert result.permission != PermissionLevel.ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command", ["rm -- ../outside", "rm -- a ../outside", "rm -r -- data", "rm -- *", "rm -- data; pwd"]
)
async def test_unsupported_or_mixed_operations_keep_ordinary_checks(tmp_path, command):
    assert (await check(config(), tmp_path, command)).permission != PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_existing_batch_threshold_and_immutable_control_guard_are_retained(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).write_text("data")
    cfg = config()
    cfg["soft_delete"] = {"batch_delete_threshold": 2}
    result = await check(cfg, tmp_path, "rm -- a b", soft_delete_enabled=True)
    assert result.permission == PermissionLevel.ASK and "del_guard" in result.matched_rule
    control = tmp_path / "control"
    control.mkdir()
    result = await check(config(), tmp_path, "rm -- control/config.yaml", immutable_control_roots=[control])
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_independent_builtin_command_floor_is_never_relaxed(tmp_path):
    cfg = config()
    cfg["rules"].append(
        {"id": "package-floor", "tools": ["shell"], "pattern": "rm -- *", "action": "ask", "layer": "builtin"}
    )
    assert (await check(cfg, tmp_path)).permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_batch_count_uses_verified_execution_cwd_when_workdir_is_omitted(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    for name in ("a", "b"):
        (child / name).write_text("data")
    cfg = config()
    cfg["soft_delete"] = {"batch_delete_threshold": 2}
    engine = PermissionEngine(cfg, workspace_root=tmp_path, soft_delete_enabled=True)
    result = await engine.check_permission(
        "bash",
        {"command": "rm -- a b", "shell_type": "bash"},
        workspace_delete_execution=LocalDeleteExecution("posix", child),
    )
    assert result.permission == PermissionLevel.ASK and "del_guard" in result.matched_rule


@pytest.mark.asyncio
async def test_missing_file_guard_and_v1_keep_their_existing_policy(tmp_path):
    cfg = config()
    cfg.pop("file_guard")
    assert (await check(cfg, tmp_path, soft_delete_enabled=True)).permission == PermissionLevel.ASK
    cfg = config()
    cfg["composition"] = {"schema_version": 1, "source_order": ["user", "cloud", "builtin"]}
    assert (await check(cfg, tmp_path)).permission == PermissionLevel.ASK


@pytest.mark.parametrize(
    "invalid",
    [
        {"schema_version": True, "rules": [], "tools": []},
        {"schema_version": 1, "rules": [{"layer": "cloud", "id": "product-delete"}], "tools": []},
        {"schema_version": 1, "rules": [{"layer": "user", "id": "missing"}], "tools": []},
        {"schema_version": 1, "rules": [], "tools": [{"layer": "user", "name": "missing"}]},
        {"schema_version": 1, "rules": "forged", "tools": []},
    ],
)
def test_malformed_default_metadata_is_rejected_at_ingest(invalid):
    cfg = deepcopy(config())
    cfg["composition"]["workspace_delete_defaults"] = invalid
    with pytest.raises(ValueError, match="workspace delete"):
        prepare_permissions_for_engine(cfg)
