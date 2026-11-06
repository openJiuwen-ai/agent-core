# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Direct resource checks preserve the existing tool permission decisions."""

from pathlib import Path

import pytest

from openjiuwen.harness.security.permission_engine.approve import operation_grants
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)
from openjiuwen.harness.security.permission_engine.fileguard.immutable_control_plane import (
    ImmutableControlPlaneGuard,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel


@pytest.mark.asyncio
@pytest.mark.parametrize("argument,observed,expected", [
    ("control/settings", "data.txt", "deny"),
    ("control/settings", None, "deny"),
    ("package.json", "control/settings", "deny"),
    ("control/settings", "package.json", "deny"),
    ("package.json", "data.txt", "ask"),
    ("data.txt", "package.json", "ask"),
])
async def test_observed_paths_supplement_control_plane_argument_checks(tmp_path, argument, observed, expected):
    engine = PermissionEngine(
        {"enabled": True, "package_builtin_rules": False, "tools": {"write_file": "allow"}},
        workspace_root=tmp_path, immutable_control_roots=[tmp_path / "control"],
    )
    accesses = [(tmp_path / observed, "write")] if observed else []
    result = await engine.check_permission("write_file", {"file_path": str(tmp_path / argument)}, file_accesses=accesses)
    assert result.permission == PermissionLevel(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("remembered", [False, True])
async def test_identity_control_plane_ask_survives_command_approval(tmp_path, monkeypatch, remembered):
    identity = {
        "version": 2, "exe": "opaque-program", "dialect": "bash", "name": "", "schema": "test:1",
        "argv": [["literal", "write-config"]], "accesses": [(str(tmp_path / "package.json"), "write")],
    }
    subject = {"kind": "command", "cwd": str(tmp_path), "shell_type": "bash",
               "command": "opaque-program write-config", "command_identity": identity}
    monkeypatch.setattr(operation_grants, "operation_subject", lambda *_: subject)
    cfg = {"enabled": True, "package_builtin_rules": False, "tools": {"bash": "ask" if remembered else "allow"},
           "file_guard": {"enabled": False}}
    args = {"command": subject["command"], "workdir": str(tmp_path), "shell_type": "bash"}
    if remembered:
        cfg["approval_overrides"] = operation_grants.build_operation_grants("bash", args, cfg)
    result = await PermissionEngine(cfg, workspace_root=tmp_path).check_permission("bash", args)
    assert result.needs_approval
    assert "immutable_project_execution_config" in result.matched_rule
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

    assert PermissionInterruptRail._requires_one_time_approval(result)


@pytest.mark.parametrize("level", ["allow", "ask", "deny"])
@pytest.mark.parametrize("action,tool", [("read", "read_file"), ("write", "write_file")])
@pytest.mark.parametrize("enabled", [False, True])
def test_direct_matches_tool_file_policy(tmp_path, level, action, tool, enabled):
    target = tmp_path / "data.txt"
    config = {"file_guard": {"enabled": enabled, "defaults": {
        "read": "allow", "write": "allow", "exec": "allow",
    }, "paths": [{"path": str(target), "read": level, "write": level, "exec": level}]}}
    checker = FileGuardChecker(
        normalize_path_guard_config(config, workspace_root=tmp_path / "workspace"), permission_config=config,
    )
    direct = checker.evaluate_accesses([(target, action)])
    assert direct == checker.evaluate(tool, {"file_path": str(target)})
    if enabled and level != "allow":
        assert direct.permission == PermissionLevel(level)
    else:
        assert direct is None


def test_direct_composition_deny_wins_over_user_allow(tmp_path):
    target = tmp_path / "private" / "data.txt"
    config = {
        "composition": {"schema_version": 1, "source_order": ["user", "cloud"]},
        "file_guard": {"enabled": True, "defaults": {"read": "allow"}, "paths": [
            {"path": str(tmp_path / "private"), "read": "deny", "layer": "cloud"},
            {"path": str(target), "read": "allow", "layer": "user"},
        ]},
    }
    checker = FileGuardChecker(
        normalize_path_guard_config(config, workspace_root=tmp_path), permission_config=config,
    )
    result = checker.evaluate_accesses([(target, "read")])
    assert result == checker.evaluate("read_file", {"file_path": str(target)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.parametrize("relative,level", [
    ("control/config.yaml", "deny"), ("package.json", "ask"), ("data.txt", None),
])
def test_control_plane_direct_matches_tool(tmp_path, relative, level):
    guard = ImmutableControlPlaneGuard([tmp_path / "control"], workspace_root=tmp_path)
    target = tmp_path / relative
    result = guard.evaluate_accesses([(target, "write")])
    assert result == guard.evaluate("write_file", {"file_path": str(target), "content": "x"})
    if level:
        assert result.permission == PermissionLevel(level)
    else:
        assert result is None
    assert guard.evaluate_accesses([(target, "read")]) is None


@pytest.mark.parametrize("control", [False, True])
@pytest.mark.parametrize("path,action", [(Path("relative"), "read"), (None, "read")])
def test_direct_rejects_unresolved_paths(tmp_path, control, path, action):
    checker = (ImmutableControlPlaneGuard([], workspace_root=tmp_path) if control else
               FileGuardChecker(normalize_path_guard_config({}, workspace_root=tmp_path)))
    with pytest.raises(ValueError, match="absolute Path"):
        checker.evaluate_accesses([(path, action)])


@pytest.mark.parametrize("control", [False, True])
def test_direct_rejects_unknown_action(tmp_path, control):
    checker = (ImmutableControlPlaneGuard([], workspace_root=tmp_path) if control else
               FileGuardChecker(normalize_path_guard_config({}, workspace_root=tmp_path)))
    with pytest.raises(ValueError, match="Unsupported runtime"):
        checker.evaluate_accesses([(tmp_path / "data", "unknown")])


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("immutable_control_enabled", [None, False, True])
@pytest.mark.parametrize("relative,expected", [
    ("control/config.yaml", PermissionLevel.DENY),
    ("package.json", PermissionLevel.ASK),
])
async def test_runtime_preserves_existing_control_plane_rules(
    tmp_path, enabled, relative, expected, immutable_control_enabled,
):
    engine = PermissionEngine(
        {"enabled": enabled, "package_builtin_rules": False,
         "immutable_control_enabled": immutable_control_enabled, "tools": {"write_file": "allow"}},
        workspace_root=tmp_path, immutable_control_roots=[tmp_path / "control"],
    )
    target = tmp_path / relative
    direct = engine.check_file_accesses([(target, "write")])
    original = await engine.check_permission("write_file", {"file_path": str(target), "content": "x"})
    assert direct.permission == original.permission
    if enabled and immutable_control_enabled is not False:
        assert direct == original
    assert original.permission == (
        expected if enabled and immutable_control_enabled is not False else PermissionLevel.ALLOW
    )


@pytest.mark.asyncio
async def test_observed_executable_does_not_treat_interpreter_input_as_exec(tmp_path):
    root, exe = tmp_path / "skills", tmp_path / "python.exe"
    root.mkdir()
    (root / "script.py").write_text("print('skill')\n")
    engine = PermissionEngine({"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"}}},
        workspace_root=tmp_path, immutable_control_roots=[root])
    args = {"command": f'"{exe.as_posix()}" "{(root / "script.py").as_posix()}"', "shell_type": "cmd"}
    assert (await engine.check_permission("write_file", {"file_path": str(root / "script.py"),
                                                        "file_accesses": []})).is_denied
    assert (await engine.check_permission("bash", args, file_accesses=[(exe, "exec")])).is_allowed
    assert engine.check_file_accesses([(root / "script.py", "read")]).is_allowed
    assert engine.check_file_accesses([(root / "script.py", "write")]).is_denied
    assert (await engine.check_permission("bash", args, file_accesses=[(root / "python.exe", "exec")])).is_denied


@pytest.mark.asyncio
async def test_observed_resources_keep_batch_delete_pipeline(tmp_path):
    paths = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for path in paths:
        path.write_text("test")
    engine = PermissionEngine({"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"},
        "soft_delete": {"enabled": True, "batch_delete_threshold": 2}}, workspace_root=tmp_path)
    args = {"command": "rm " + " ".join(p.as_posix() for p in paths), "shell_type": "bash", "workdir": str(tmp_path)}
    original = await engine.check_permission("bash", args)
    observed = await engine.check_permission("bash", args, file_accesses=[(tmp_path / "rm.exe", "exec")])
    assert original.needs_approval and observed.needs_approval
    assert "del_guard:batch_delete" in observed.matched_rule


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["exec", "read"])
async def test_observed_exe_keeps_existing_interpreter_file_rules(tmp_path, action):
    exe, script = tmp_path / "python.exe", tmp_path / "script.py"
    script.write_text("print('test')")
    engine = PermissionEngine({"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                       "paths": [{"path": str(script), action: "deny"}]}}, workspace_root=tmp_path)
    import subprocess
    args = {"command": subprocess.list2cmdline([str(exe), str(script)]), "shell_type": "cmd"}
    original = await engine.check_permission("bash", args)
    observed = await engine.check_permission("bash", args, file_accesses=[(exe, "exec")])
    assert original.is_denied and observed.is_denied


def test_runtime_engine_policy_reload_and_disable(tmp_path):
    cfg = {"enabled": True, "package_builtin_rules": False, "file_guard": {
        "enabled": True, "defaults": {"read": "deny"},
    }}
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    accesses = [(tmp_path / "data", "read")]
    assert engine.check_file_accesses(accesses).is_denied
    engine.set_permission_checks_active(lambda: False)
    assert engine.check_file_accesses(accesses).is_allowed
    engine.set_permission_checks_active(None)
    engine.update_config({**cfg, "enabled": False})
    assert engine.check_file_accesses(accesses).is_allowed


def test_runtime_batch_preserves_configured_deny(tmp_path):
    engine = PermissionEngine(
        {"enabled": True, "package_builtin_rules": False, "file_guard": {
            "enabled": True, "defaults": {"write": "allow"}, "paths": [
                {"path": str(tmp_path / "control"), "write": "deny"},
            ]}}, workspace_root=tmp_path, immutable_control_roots=[tmp_path / "control"],
    )
    accesses = [(tmp_path / "package.json", "write"), (tmp_path / "control" / "settings", "write")]
    assert engine.check_file_accesses(accesses).is_denied
