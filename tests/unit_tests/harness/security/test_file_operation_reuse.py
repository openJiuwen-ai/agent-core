"""Equivalent operations reuse receipts without crossing action/DENY boundaries."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.approve.operation_grants import build_operation_grants
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import split_shell_for_grants


def arguments(folder, command):
    return {"command": command, "workdir": str(folder), "shell_type": "bash"}


def configuration():
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "defaults": {"*": "allow"},
        "rules": [{"tools": ["bash"], "match_type": "command", "pattern": "re:.*rm.*", "action": "ask"}],
        "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"}},
    }


def test_redirect_stays_with_its_command():
    assert split_shell_for_grants("printf hi > A && rm B && ls B 2>&1") == ("printf hi > A", "rm B", "ls B 2>&1")
    assert split_shell_for_grants("cat < A | rm B") == ("cat < A", "rm B")
    assert split_shell_for_grants("echo $(rm B) > A") == ()


@pytest.mark.asyncio
async def test_rm_receipt_normalizes_only_the_same_single_file(tmp_path):
    target = tmp_path / "temp_test.txt"
    target.write_text("test")
    cfg = configuration()
    cfg["approval_overrides"] = build_operation_grants(
        "bash",
        arguments(tmp_path, "printf hi > temp_test.txt && rm temp_test.txt && echo done"),
        cfg,
        mode="allow_with_scope",
        scope="subcommands",
    )
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    for command in ["rm temp_test.txt", "rm ./temp_test.txt", f'rm "{target.as_posix()}"']:
        assert (await engine.check_permission("bash", arguments(tmp_path, command))).is_allowed
    for command in [
        "rm other.txt",
        "rm -f temp_test.txt",
        "rm -r temp_test.txt",
        "rm -i temp_test.txt",
        "rm *.txt",
        "rm temp_test.txt other.txt",
        "rm $TARGET",
        "rm temp_test.txt > other.txt",
    ]:
        assert not (await engine.check_permission("bash", arguments(tmp_path, command))).is_allowed, command
    cfg["file_guard"]["paths"] = [{"path": str(target), "write": "deny"}]
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm temp_test.txt"))).is_denied
    if os.name == "nt":
        assert engine.check_file_accesses([(Path(str(target).lower()), "write")]).is_denied


@pytest.mark.asyncio
async def test_shared_receipt_does_not_override_batch_delete_or_tool_deny(tmp_path):
    cfg = configuration()
    cfg["approval_overrides"] = build_operation_grants("bash", arguments(tmp_path, "rm A"), cfg)
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    engine._deletion_guard = SimpleNamespace(
        evaluate=lambda *_: PermissionResult(PermissionLevel.ASK, "del_guard:batch_delete")
    )
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm A"))).needs_approval
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm -f ./A"))).needs_approval
    cfg["tools"] = {"bash": "deny"}
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm A"))).is_denied


@pytest.mark.asyncio
async def test_shared_file_receipts_keep_actions_and_paths(tmp_path):
    target = tmp_path / "A"
    cfg = configuration()
    cfg["tools"] = {"write_file": "ask", "edit_file": "ask", "read_file": "ask"}
    cfg["approval_overrides"] = build_operation_grants("write_file", {"file_path": str(target)}, cfg, tmp_path)
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    assert (await engine.check_permission("edit_file", {"file_path": str(target)})).is_allowed
    assert (await engine.check_permission("read_file", {"file_path": str(target)})).needs_approval
    assert (await engine.check_permission("edit_file", {"file_path": str(tmp_path / "B")})).needs_approval
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm A"))).needs_approval


@pytest.mark.asyncio
async def test_duplicate_children_reuse_and_redirect_targets_stay_bound(tmp_path):
    cfg = configuration()
    cfg["tools"] = {"bash": "ask"}
    command = "printf hi > A && echo done && echo done"
    cfg["approval_overrides"] = build_operation_grants(
        "bash", arguments(tmp_path, command), cfg, mode="allow_with_scope", scope="subcommands"
    )
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", arguments(tmp_path, command))).is_allowed
    assert (await engine.check_permission("bash", arguments(tmp_path, command.replace("> A", "> B")))).needs_approval
    assert (await engine.check_permission("bash", arguments(tmp_path, "printf hi"))).needs_approval


def test_exact_compound_does_not_promote_its_child_effects(tmp_path):
    cfg = configuration()
    grants = build_operation_grants("bash", arguments(tmp_path, "rm A && echo done"), cfg)
    assert not any(grant["tools"] == ["file_resource"] for grant in grants)


@pytest.mark.asyncio
async def test_legacy_command_keeps_literal_reuse_without_widening(tmp_path):
    cfg = configuration()
    grant = build_operation_grants("bash", arguments(tmp_path, "rm A"), cfg)[0]
    pattern = json.loads(grant["pattern"])
    pattern["subject"].pop("command_identity", None)
    grant["pattern"] = json.dumps(pattern)
    cfg["approval_overrides"] = [grant]
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm A"))).is_allowed
    assert (await engine.check_permission("bash", arguments(tmp_path, "rm -f A"))).needs_approval


def test_changed_directory_never_creates_a_receipt_for_initial_cwd(tmp_path):
    cfg = configuration()
    grants = build_operation_grants(
        "bash", arguments(tmp_path, "cd elsewhere && rm A"), cfg, mode="allow_with_scope", scope="subcommands"
    )
    assert not any(grant["tools"] == ["file_resource"] for grant in grants)


@pytest.mark.asyncio
async def test_replacing_approved_file_with_directory_does_not_match(tmp_path):
    target = tmp_path / "A"
    target.write_text("approved file")
    cfg = configuration()
    cfg["approval_overrides"] = build_operation_grants("bash", arguments(tmp_path, "rm A"), cfg)
    assert (await PermissionEngine(cfg).check_permission("bash", arguments(tmp_path, "rm A"))).is_allowed
    target.unlink()
    target.mkdir()
    assert not (await PermissionEngine(cfg).check_permission("bash", arguments(tmp_path, "rm A"))).is_allowed
