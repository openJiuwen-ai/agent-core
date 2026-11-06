"""Command schemas normalize operands, never flags, values or command authority."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.approve import command_identity as identities
from openjiuwen.harness.security.permission_engine.approve.operation_grants import (
    build_operation_grants,
    matches_operation_grant,
    operation_subject,
)
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult


def args(folder, command, shell="bash"):
    return {"command": command, "workdir": str(folder), "shell_type": shell}


def config():
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "tools": {"bash": "ask", "powershell": "ask"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"}},
    }


@pytest.mark.parametrize("tool", ["powershell", "pwsh", "core.powershell"])
def test_powershell_tool_aliases_bind_workdir_and_identity(tmp_path, monkeypatch, tool):
    calls = []

    def identity(command, cwd, shell):
        calls.append((command, cwd, shell))
        return {"test_identity": True}

    monkeypatch.setattr(identities, "literal_command_identity", identity)
    inputs = {"command": "Get-Content A", "workdir": str(tmp_path), "shell_type": "auto"}
    subject = operation_subject(tool, inputs, config())
    assert subject["cwd"] == str(tmp_path.resolve())
    assert subject["shell_type"] == "powershell"
    assert subject["command_identity"] == {"test_identity": True}
    assert "arguments" not in subject
    assert calls == [(inputs["command"], str(tmp_path.resolve()), "powershell")]


@pytest.mark.parametrize("shell", ["auto", "sh"])
def test_posix_default_shell_keeps_typed_file_receipts(tmp_path, monkeypatch, shell):
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation

    monkeypatch.setattr(ShellOperation, "_resolve_execution_plan", lambda command, _: (command, True, "sh"))
    monkeypatch.setattr(identities, "_resolve_program", lambda *_: (sys.executable, True))
    cfg = config()
    inputs = args(tmp_path, "cat A", shell)
    subject = operation_subject("bash", inputs, cfg)
    identity = subject["command_identity"]
    assert identity["dialect"] == "sh"
    assert identity["accesses"] == [(str(tmp_path / "A"), "read")]
    grants = build_operation_grants("bash", inputs, cfg)
    assert any(grant["tools"] == ["file_resource"] for grant in grants)
    equivalent = operation_subject("bash", args(tmp_path, 'cat "A"', shell), cfg)
    assert matches_operation_grant(grants[0], "bash", equivalent)
    assert identities.literal_command_identity("cat $(echo A)", str(tmp_path), shell) is None


def test_executable_symlink_resolves_without_typing_symlink_operands(tmp_path):
    program = tmp_path / "linked-python"
    try:
        program.symlink_to(Path(sys.executable).resolve())
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    identity = identities.native_command_identity(str(program), ["--data", "A"], str(tmp_path), "bash")
    direct = identities.native_command_identity(sys.executable, ["--data", "A"], str(tmp_path), "bash")
    assert identity is not None and identity == direct
    assert identities._path(str(program), str(tmp_path)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,equivalent,different",
    [
        ("rm A", 'rm "{A}"', "rm -f A"),
        ("rm -f A", 'rm -f "{A}"', "rm -i A"),
        ("cat A", 'cat "{A}"', "cat -n A"),
        ("cat A B", 'cat "{A}" "{B}"', "cat B A"),
        ("cp A B", 'cp "{A}" "{B}"', "cp B A"),
        ("mv A B", 'mv "{A}" "{B}"', "mv -f A B"),
        ("head -n 5 A", 'head -n 5 "{A}"', "head -n 10 A"),
        ("tail -n 5 A", 'tail -n 5 "{A}"', "tail -n 10 A"),
        ("grep needle A", 'grep needle "{A}"', "grep other A"),
        ("grep -e needle A", 'grep -e needle "{A}"', "grep -v -e needle A"),
        ("touch A", 'touch "{A}"', "touch -c A"),
        ("mkdir C", 'mkdir "{C}"', "mkdir -p C"),
        ("ls -l A", 'ls -l "{A}"', "ls -a A"),
    ],
)
async def test_registered_commands_keep_flags_values_and_operand_order(tmp_path, command, equivalent, different):
    for name in ("A", "B"):
        (tmp_path / name).write_text("needle\n")
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("bash", args(tmp_path, command), cfg)
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    paths = {name: (tmp_path / name).as_posix() for name in ("A", "B", "C")}
    assert (await engine.check_permission("bash", args(tmp_path, equivalent.format(**paths)))).is_allowed
    # File receipts for A/B must not turn the changed command's ASK into ALLOW.
    assert (await engine.check_permission("bash", args(tmp_path, different))).needs_approval


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell")
@pytest.mark.parametrize(
    "command,different",
    [
        ("Get-Content -LiteralPath A", "Get-Content -LiteralPath A -Raw"),
        ("Set-Content -LiteralPath A -Value hello", "Set-Content -LiteralPath A -Value other"),
        ("Remove-Item -LiteralPath A", "Remove-Item -LiteralPath A -Force"),
        ("Copy-Item -LiteralPath A -Destination B", "Copy-Item -LiteralPath A -Destination B -Force"),
        ("Move-Item -LiteralPath A -Destination B", "Move-Item -LiteralPath A -Destination B -Force"),
    ],
)
async def test_powershell_named_paths_are_typed_but_values_and_switches_are_bound(tmp_path, command, different):
    (tmp_path / "A").write_text("hello")
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("powershell", args(tmp_path, command, "powershell"), cfg)
    normalized = command.replace("Path A", f"Path '{tmp_path / 'A'}'").replace(
        "Destination B", f"Destination '{tmp_path / 'B'}'"
    )
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    assert (await engine.check_permission("powershell", args(tmp_path, normalized, "powershell"))).is_allowed
    assert (await engine.check_permission("powershell", args(tmp_path, different, "powershell"))).needs_approval


@pytest.mark.asyncio
async def test_unregistered_program_preserves_literal_argv_without_guessing_paths(tmp_path):
    program = Path(sys.executable).as_posix()
    original = f'"{program}" -c "print(1)" A'
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("bash", args(tmp_path, original), cfg)
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", args(tmp_path, f"'{program}' -c 'print(1)' A"))).is_allowed
    for changed in [f'"{program}" -c "print(2)" A', f'"{program}" -c "print(1)" "{tmp_path / "A"}"']:
        assert (await engine.check_permission("bash", args(tmp_path, changed))).needs_approval
    assert not any(g["tools"] == ["file_resource"] for g in cfg["approval_overrides"])


@pytest.mark.parametrize(
    "command", ["cat $FILE", "cat $(pwd)/A", "cat *.txt", "FILE=A cat A", "cat A > B", "cat A; rm B"]
)
def test_dynamic_and_compound_syntax_never_becomes_literal_identity(tmp_path, command):
    assert identities.literal_command_identity(command, str(tmp_path), "bash") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell")
@pytest.mark.parametrize(
    "command",
    [
        "Get-Content -LiteralPath A,B",
        "Get-Content '-LiteralPath' A",
        "Get-Content --% A",
        "Get-Content -LiteralPath A\nRemove-Item B",
        "Get-Content -LiteralPath $FILE",
        "Get-Content -LiteralPath [string]A",
        "Get-Content -LiteralPath A; Remove-Item B",
    ],
)
def test_powershell_arrays_expressions_and_quoted_switches_are_not_argv_equivalence(tmp_path, command):
    assert identities.literal_command_identity(command, str(tmp_path), "powershell") is None


@pytest.mark.asyncio
async def test_unknown_option_retains_literal_operands_and_all_option_bytes(tmp_path):
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("bash", args(tmp_path, "cat --unknown A"), cfg)
    engine = PermissionEngine(cfg)
    assert (await engine.check_permission("bash", args(tmp_path, "cat --unknown A"))).is_allowed
    assert (await engine.check_permission("bash", args(tmp_path, f'cat --unknown "{tmp_path / "A"}"'))).needs_approval
    assert (await engine.check_permission("bash", args(tmp_path, "cat A"))).needs_approval


def test_schema_registration_does_not_require_matching_engine_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(identities, "_SCHEMAS", dict(identities._SCHEMAS))
    before = identities.literal_command_identity("sort A", str(tmp_path), "bash")
    assert before["schema"] == "literal"
    identities.register_command_schema(identities.CommandSchema("sort", "read"))
    after = identities.literal_command_identity("sort A", str(tmp_path), "bash")
    assert after["argv"] == [["path", str(tmp_path / "A")]]
    assert after["accesses"] == [(str(tmp_path / "A"), "read")]


def test_parent_traversal_does_not_guess_cross_link_path_equivalence(tmp_path):
    identity = identities.literal_command_identity("cat link/../A", str(tmp_path), "bash")
    assert identity["schema"] == "literal"
    assert identity["argv"] == [["literal", "link/../A"]]
    assert not identity["accesses"]


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["tool", "file", "batch"])
async def test_fresh_denies_and_delete_protection_precede_identity_reuse(tmp_path, restriction):
    target = tmp_path / "A"
    target.write_text("keep")
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("bash", args(tmp_path, "rm A"), cfg)
    if restriction == "tool":
        cfg["tools"]["bash"] = "deny"
    elif restriction == "file":
        cfg["file_guard"]["paths"] = [{"path": str(target), "write": "deny"}]
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    if restriction == "batch":
        engine._deletion_guard = SimpleNamespace(
            evaluate=lambda *_: PermissionResult(PermissionLevel.ASK, "del_guard:batch_delete")
        )
    result = await engine.check_permission("bash", args(tmp_path, f'rm "{target.as_posix()}"'))
    assert result.needs_approval if restriction == "batch" else result.is_denied


def test_previous_rm_identity_without_argv_is_restricted_to_original_literal(tmp_path):
    original = args(tmp_path, "rm A")
    subject = operation_subject("bash", original, {})
    subject.pop("command_identity")
    subject["file_command"] = {"exe": "old/rm.exe", "accesses": [(str(tmp_path / "A"), "delete")]}
    grant = {
        "tools": ["bash"],
        "match_type": "exact_operation",
        "action": "allow",
        "pattern": json.dumps({"scope": "exact", "subject": subject}),
    }
    assert matches_operation_grant(grant, "bash", operation_subject("bash", original, {}))
    assert not matches_operation_grant(grant, "bash", operation_subject("bash", args(tmp_path, "rm -f A"), {}))


def test_native_literal_values_are_never_rewritten_as_shell_paths(tmp_path):
    value = r"C:\literal\data"
    identity = identities.native_command_identity(sys.executable, ["--data", value], str(tmp_path), "bash")
    assert identity["argv"] == [["literal", "--data"], ["literal", value]]
    inputs = args(tmp_path, f"'{Path(sys.executable).as_posix()}' --data '{value}'")
    with identities.bind_native_command("bash", inputs, identity):
        assert operation_subject("bash", inputs, {})["command_identity"]["argv"][-1] == ["literal", value]
        # An inner policy candidate cannot inherit the outer native identity.
        assert "command_identity" not in operation_subject("bash", args(tmp_path, "cat A"), {})
    assert identities.bound_native_identity("bash", inputs) == (False, None)


@pytest.mark.asyncio
async def test_native_binding_is_task_local(tmp_path):
    import asyncio

    inputs = args(tmp_path, "cat A")

    async def check(value):
        identity = identities.native_command_identity(sys.executable, [value], str(tmp_path), "bash")
        with identities.bind_native_command("bash", inputs, identity):
            await asyncio.sleep(0)
            return operation_subject("bash", inputs, {})["command_identity"]["argv"]

    assert await asyncio.gather(check("first"), check("second")) == [[["literal", "first"]], [["literal", "second"]]]


def test_malformed_semantic_receipt_fails_closed(tmp_path):
    subject = operation_subject("bash", args(tmp_path, "cat A"), {})
    grant = {
        "tools": ["bash"],
        "match_type": "exact_operation",
        "action": "allow",
        "pattern": json.dumps({"scope": "exact", "subject": {**subject, "command_identity": None}}),
    }
    assert not matches_operation_grant(grant, "bash", subject)


@pytest.mark.asyncio
async def test_copy_destination_file_to_directory_change_is_not_equivalent(tmp_path):
    (tmp_path / "A").write_text("source")
    cfg = config()
    cfg["approval_overrides"] = build_operation_grants("bash", args(tmp_path, "cp A B"), cfg)
    (tmp_path / "B").mkdir()
    assert (await PermissionEngine(cfg).check_permission("bash", args(tmp_path, "cp A B"))).needs_approval


@pytest.mark.asyncio
async def test_native_program_receipt_matches_command_layer_without_authorizing_files(tmp_path):
    identity = identities.literal_command_identity("cp A B", str(tmp_path), "bash")
    program = {
        "kind": "program",
        "exe": identity["exe"],
        "cwd": str(tmp_path),
        "argv": ["A", "B"],
        "command_identity": identity,
    }
    cfg = config()
    cfg["approval_overrides"] = [
        {
            "tools": ["dll_runtime_program"],
            "match_type": "exact_operation",
            "action": "allow",
            "pattern": json.dumps({"scope": "exact", "subject": program}),
        }
    ]
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    command = f'cp "{(tmp_path / "A").as_posix()}" "{(tmp_path / "B").as_posix()}"'
    assert (await engine.check_permission("bash", args(tmp_path, command))).is_allowed
    assert (await engine.check_permission("bash", args(tmp_path, "cp -f A B"))).needs_approval
    cfg["file_guard"]["paths"] = [{"path": str(tmp_path / "B"), "write": "ask"}]
    engine.update_config(cfg)
    assert (await engine.check_permission("bash", args(tmp_path, command))).needs_approval
