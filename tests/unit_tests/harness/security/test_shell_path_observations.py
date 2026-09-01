# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import pytest

from openjiuwen.harness.security.files import (
    FileToolSpec,
    extract_accesses_native,
    extract_path_aware_command_accesses,
    extract_shell_path_accesses,
    lookup_file_tool_specs,
    register_file_tool,
)
from openjiuwen.harness.security.permission_engine import PermissionEngine
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import FileGuardChecker
from openjiuwen.harness.security.permission_engine.fileguard import path_extract
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_accesses_native as canonical_extract_accesses_native,
)
from openjiuwen.harness.security.permission_engine.models import (
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.permission_engine.toolguard import shell_ast
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import ShellSubcommand


def _accesses(command: str, workspace: Path) -> list[tuple[Path, str, str]]:
    return extract_accesses_native("bash", {"command": command}, workspace)


def test_public_permission_contracts_are_unchanged() -> None:
    assert [field.name for field in fields(PermissionResult)] == [
        "permission",
        "matched_rule",
        "reason",
        "external_paths",
    ]
    assert "path_evidence" not in inspect.signature(FileGuardChecker.evaluate).parameters
    assert [field.name for field in fields(ShellSubcommand)] == [
        "text",
        "argv",
        "redirects",
        "source_span",
        "parent_operators",
    ]


def test_compatibility_exports_share_implementation_and_registry() -> None:
    assert extract_accesses_native is canonical_extract_accesses_native
    assert extract_accesses_native.__module__.endswith("fileguard.path_extract")
    assert tuple(inspect.signature(extract_accesses_native).parameters) == (
        "tool_name",
        "tool_args",
        "workspace",
    )
    assert tuple(inspect.signature(extract_path_aware_command_accesses).parameters) == (
        "command",
        "workdir",
    )
    assert tuple(inspect.signature(extract_shell_path_accesses).parameters) == (
        "command",
        "workdir",
    )

    spec = FileToolSpec("stable_parser_contract_tool", "path", "read")
    register_file_tool(spec)

    assert lookup_file_tool_specs(spec.tool_name) == [spec]


def test_timeout_interpreter_and_unrelated_quotes_are_observed(tmp_path: Path) -> None:
    accesses = _accesses(
        'timeout 120 bash ./test.sh 2>&1; echo "finished safely"',
        tmp_path,
    )

    assert [(path.name, action, source) for path, action, source in accesses] == [
        ("test.sh", "exec", "shlex")
    ]


@pytest.mark.parametrize("option", ["-i", "-ni", "-Ei", "-Eni"])
def test_gnu_sed_in_place_emits_read_and_write(
    option: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    accesses = _accesses(
        f"sed {option} 's/\\r$//' ./test.sh && echo finished",
        tmp_path,
    )

    assert {(path.name, action, source) for path, action, source in accesses} == {
        ("test.sh", "read", "shlex"),
        ("test.sh", "write", "shlex"),
    }


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'source ./test.sh'",
        "bash -O extglob ./test.sh",
        "python -c 'open(\"./test.sh\").read()'",
        "python --check-hash-based-pycs always ./test.py",
        "node --inspect-port 9229 ./test.js",
        "timeout 5 bash -c 'source ./test.sh'",
    ],
)
def test_dynamic_or_unknown_interpreter_forms_do_not_emit_wrong_paths(
    command: str,
    tmp_path: Path,
) -> None:
    assert _accesses(command, tmp_path) == []


def test_forced_fallback_observes_original_timeout_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    accesses = _accesses(
        'timeout 120 bash ./test.sh 2>&1; echo "finished safely"',
        tmp_path,
    )

    assert [(path.name, action) for path, action, _source in accesses] == [
        ("test.sh", "exec")
    ]


def test_forced_fallback_observes_original_sed_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)
    monkeypatch.setattr("sys.platform", "linux")

    accesses = _accesses(
        "sed -i 's/x/y/' ./test.sh && echo finished",
        tmp_path,
    )

    assert {(path.name, action) for path, action, _source in accesses} == {
        ("test.sh", "read"),
        ("test.sh", "write"),
    }


def test_bsd_sed_consumes_separate_in_place_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")

    accesses = _accesses("sed -i .bak p ./target.txt", tmp_path)

    assert {(path.name, action) for path, action, _source in accesses} == {
        ("target.txt", "read"),
        ("target.txt", "write"),
    }
    assert all(path.name != "p" for path, _action, _source in accesses)


def test_unknown_sed_dialect_rejects_bare_in_place_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "sunos")

    assert _accesses("sed -i p ./target.txt", tmp_path) == []


def test_gsed_uses_gnu_in_place_semantics_on_bsd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")

    accesses = _accesses("gsed -i p ./target.txt", tmp_path)

    assert {(path.name, action) for path, action, _source in accesses} == {
        ("target.txt", "read"),
        ("target.txt", "write"),
    }


def test_forced_fallback_rejects_malformed_or_dynamic_operands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert _accesses("timeout 5 bash './test.sh", tmp_path) == []
    assert _accesses("timeout 5 bash $SCRIPT && echo done", tmp_path) == []


def test_structural_redirect_is_observed(tmp_path: Path) -> None:
    accesses = _accesses("echo hi > output.txt", tmp_path)

    assert any(path.name == "output.txt" and action == "write" for path, action, _source in accesses)


def test_known_read_command_observes_plain_filename(tmp_path: Path) -> None:
    accesses = _accesses("cat report.csv", tmp_path)

    assert [(path.name, action, source) for path, action, source in accesses] == [
        ("report.csv", "read", "shlex")
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell escaping")
@pytest.mark.parametrize(
    "command",
    [
        r"cat ./dir\ with\ space/file.txt",
        'cat "./dir with space/file.txt"',
    ],
)
def test_posix_escaped_and_quoted_paths_use_shell_ast_argv(
    command: str,
    tmp_path: Path,
) -> None:
    expected = (tmp_path / "dir with space" / "file.txt").as_posix()

    accesses = extract_shell_path_accesses(command, tmp_path)

    assert [(path.as_posix(), action) for path, action in accesses] == [
        (expected, "read")
    ]
    assert extract_path_aware_command_accesses(command, tmp_path) == accesses


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell escaping")
@pytest.mark.parametrize(
    "command",
    [
        r"printf ok > ./dir\ with\ space/output.txt",
        'printf ok > "./dir with space/output.txt"',
    ],
)
def test_posix_escaped_and_quoted_redirect_targets_are_normalized_once(
    command: str,
    tmp_path: Path,
) -> None:
    expected = (tmp_path / "dir with space" / "output.txt").as_posix()

    accesses = extract_shell_path_accesses(command, tmp_path)

    assert [(path.as_posix(), action) for path, action in accesses] == [
        (expected, "write")
    ]
    assert extract_path_aware_command_accesses(command, tmp_path) == accesses


def test_public_shell_extractors_share_one_observation_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    (tmp_path / "sub").mkdir()
    commands = (
        "cat report.csv",
        "cd ./sub; cat report.csv",
        "timeout 5 bash ./test.sh",
        "sed -i p ./test.sh",
        "echo ok | cat > ./output.txt",
    )

    for command in commands:
        assert extract_path_aware_command_accesses(
            command,
            tmp_path,
        ) == extract_shell_path_accesses(command, tmp_path)


def test_command_observer_registry_is_used_by_both_public_extractors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = tmp_path / "observer.txt"

    def observer(
        _argv: tuple[str, ...],
        _argv_syntax: tuple[path_extract._ShellArgSyntax, ...],
        _cwd: Path,
        _depth: int,
    ) -> list[tuple[Path, path_extract.FileAction]]:
        return [(observed, "read")]

    monkeypatch.setitem(path_extract._COMMAND_OBSERVERS, "observe-path", observer)

    assert extract_path_aware_command_accesses(
        "observe-path",
        tmp_path,
    ) == [(observed, "read")]
    assert extract_shell_path_accesses("observe-path", tmp_path) == [
        (observed, "read")
    ]


def test_fallback_normalization_uses_the_same_public_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    command = 'timeout 5 bash "./dir with space/test.sh" && echo done'

    accesses = extract_shell_path_accesses(command, tmp_path)

    assert accesses == [
        (tmp_path / "dir with space" / "test.sh", "exec")
    ]
    assert extract_path_aware_command_accesses(
        command,
        tmp_path,
    ) == accesses


def test_fallback_keeps_quoted_control_characters_inside_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    command = 'timeout 5 bash "./dir;literal/test.sh" && echo done'

    assert extract_shell_path_accesses(command, tmp_path) == [
        (tmp_path / "dir;literal" / "test.sh", "exec")
    ]


@pytest.mark.parametrize(
    "command",
    [
        "VAR=x bash ./test.sh",
        "FIRST=x SECOND=y timeout 5 bash ./test.sh && echo done",
    ],
)
@pytest.mark.parametrize("force_fallback", [False, True])
def test_assignment_prefix_is_not_part_of_executable_argv(
    command: str,
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses(command, tmp_path) == [
        (tmp_path / "test.sh", "exec")
    ]


@pytest.mark.parametrize("command", ["'VAR=x' bash ./test.sh", r"V\AR=x bash ./test.sh"])
@pytest.mark.parametrize("force_fallback", [False, True])
def test_quoted_or_escaped_assignment_like_command_is_not_stripped(
    command: str,
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses(command, tmp_path) == []


@pytest.mark.parametrize(
    ("command", "relative_path"),
    [
        ("bash './$literal/test.sh'", "$literal/test.sh"),
        (r"bash ./\$literal/test.sh", "$literal/test.sh"),
        (r'''bash "./\$literal/test.sh"''', "$literal/test.sh"),
        ("bash './*.sh'", "*.sh"),
        (r"bash ./\*.sh", "*.sh"),
        ("bash './file?.sh'", "file?.sh"),
        (r"bash ./file\?.sh", "file?.sh"),
        ("bash './file[0].sh'", "file[0].sh"),
        (r"bash ./file\[0\].sh", "file[0].sh"),
        ("bash './{one,two}.sh'", "{one,two}.sh"),
        (r"bash ./\{one,two\}.sh", "{one,two}.sh"),
        ("bash '~/test.sh'", "~/test.sh"),
        (r"bash \~/test.sh", "~/test.sh"),
    ],
)
@pytest.mark.parametrize("force_fallback", [False, True])
def test_quoted_or_escaped_dynamic_characters_remain_static_paths(
    command: str,
    relative_path: str,
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses(command, tmp_path) == [
        (tmp_path / relative_path, "exec")
    ]


@pytest.mark.parametrize(
    "command",
    [
        "bash ./$SCRIPT",
        "bash ./*.sh",
        "bash ./file?.sh",
        "bash ./file[0].sh",
        "bash ./{one,two}.sh",
        'bash "./$SCRIPT/test.sh"',
    ],
)
@pytest.mark.parametrize("force_fallback", [False, True])
def test_unquoted_expansion_and_glob_operands_remain_unobserved(
    command: str,
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses(command, tmp_path) == []


@pytest.mark.parametrize(
    ("command", "relative_path"),
    [
        ("bash './$literal/test.sh'", "$literal/test.sh"),
        (r'''bash "./\$literal/test.sh"''', "$literal/test.sh"),
        ("bash '~/test.sh'", "~/test.sh"),
        (r"bash \~/test.sh", "~/test.sh"),
        ("VAR=x bash ./test.sh", "test.sh"),
        ("VAR=x timeout 5 bash ./test.sh && echo done", "test.sh"),
    ],
)
@pytest.mark.parametrize("force_fallback", [False, True])
@pytest.mark.asyncio
async def test_static_shell_path_cannot_bypass_fileguard_exec_deny(
    command: str,
    relative_path: str,
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)
    denied = tmp_path / relative_path
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(denied),
                        "read": "allow",
                        "write": "allow",
                        "exec": "deny",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission(
        "bash",
        {"command": command},
    )

    assert result.permission == PermissionLevel.DENY


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("bash ~/test.sh", lambda cwd: Path.home() / "test.sh"),
        ("bash ~+/test.sh", lambda cwd: cwd / "test.sh"),
    ],
)
@pytest.mark.parametrize("force_fallback", [False, True])
def test_unquoted_tilde_uses_shell_expansion_semantics(
    command: str,
    expected: Callable[[Path], Path],
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses(command, tmp_path) == [
        (expected(tmp_path), "exec")
    ]


@pytest.mark.parametrize("force_fallback", [False, True])
def test_runtime_dependent_tilde_forms_remain_unobserved(
    force_fallback: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if force_fallback:
        monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)

    assert extract_shell_path_accesses("bash ~-/test.sh", tmp_path) == []
    assert extract_shell_path_accesses("bash ~+1/test.sh", tmp_path) == []


def test_symlinked_workdir_preserves_lexical_and_resolved_identities(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    real_directory = tmp_path / "real"
    workspace.mkdir()
    real_directory.mkdir()
    (workspace / "linked").symlink_to(real_directory, target_is_directory=True)

    accesses = extract_accesses_native(
        "bash",
        {"command": "bash test.sh", "workdir": "linked"},
        workspace,
    )

    paths = {path.as_posix() for path, action, source in accesses if action == "exec" and source == "shlex"}
    assert paths == {
        (workspace / "linked" / "test.sh").as_posix(),
        (real_directory / "test.sh").as_posix(),
    }

    read_paths = {
        path.as_posix()
        for path, action, source in extract_accesses_native(
            "bash",
            {"command": "cat report.csv", "workdir": "linked"},
            workspace,
        )
        if action == "read" and source == "shlex"
    }
    assert read_paths == {
        (workspace / "linked" / "report.csv").as_posix(),
        (real_directory / "report.csv").as_posix(),
    }


def test_registered_tool_keeps_tool_arg_source(tmp_path: Path) -> None:
    accesses = extract_accesses_native(
        "read_file",
        {"file_path": "notes.txt"},
        tmp_path,
    )

    assert [(path.name, action, source) for path, action, source in accesses] == [
        ("notes.txt", "read", "tool_arg")
    ]


def test_existing_path_outputs_are_not_globally_truncated(tmp_path: Path) -> None:
    command = "cat " + " ".join(f"./file-{index}.txt" for index in range(80))

    accesses = _accesses(command, tmp_path)

    assert len(accesses) == 80
    assert accesses[-1][0].name == "file-79.txt"


@pytest.mark.asyncio
async def test_sed_write_is_denied_by_existing_fileguard_contract(tmp_path: Path) -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(tmp_path / "test.sh"),
                        "read": "allow",
                        "write": "deny",
                        "exec": "allow",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("sys.platform", "linux")
        result = await engine.check_permission(
            "bash",
            {"command": "sed -ni 's/x/y/' ./test.sh"},
        )

    assert result.permission == PermissionLevel.DENY


@pytest.mark.parametrize(
    "command",
    [
        "true && printf ok > ./secret.txt",
        "echo ok | cat > ./secret.txt",
    ],
)
@pytest.mark.asyncio
async def test_scoped_redirect_is_denied_by_fileguard(
    command: str,
    tmp_path: Path,
) -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(tmp_path / "secret.txt"),
                        "read": "allow",
                        "write": "deny",
                        "exec": "allow",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission("bash", {"command": command})

    assert result.permission == PermissionLevel.DENY


@pytest.mark.parametrize(
    "command",
    [
        "true && printf ok > ./secret.txt",
        "echo ok | cat > ./secret.txt",
    ],
)
@pytest.mark.asyncio
async def test_fallback_scoped_redirect_is_denied_by_fileguard(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_ast, "_get_tree_sitter_bash_parser", lambda: None)
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(tmp_path / "secret.txt"),
                        "read": "allow",
                        "write": "deny",
                        "exec": "allow",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission("bash", {"command": command})

    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_unrelated_pipeline_does_not_suppress_sequential_cd(
    tmp_path: Path,
) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(subdir / "secret.txt"),
                        "read": "allow",
                        "write": "deny",
                        "exec": "allow",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission(
        "bash",
        {
            "command": (
                "cd ./sub; echo ok | cat; printf ok > ./secret.txt"
            )
        },
    )

    assert result.permission == PermissionLevel.DENY


def test_failed_sequential_cd_preserves_original_redirect_identity(
    tmp_path: Path,
) -> None:
    accesses = _accesses(
        "cd ./missing; printf ok > ./secret.txt",
        tmp_path,
    )

    write_paths = {
        path.as_posix()
        for path, action, source in accesses
        if action == "write" and source == "shlex"
    }
    assert (tmp_path / "secret.txt").as_posix() in write_paths
    assert (tmp_path / "missing" / "secret.txt").as_posix() in write_paths


@pytest.mark.parametrize("operator", [";", "&&", "||"])
def test_sequential_cd_keeps_old_and_new_interpreter_identities(
    operator: str,
    tmp_path: Path,
) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()

    accesses = _accesses(f"cd ./sub {operator} bash ./test.sh", tmp_path)

    exec_paths = {
        path.as_posix()
        for path, action, source in accesses
        if action == "exec" and source == "shlex"
    }
    assert exec_paths == {
        (tmp_path / "test.sh").as_posix(),
        (subdir / "test.sh").as_posix(),
    }


def test_pipeline_cd_does_not_change_parent_shell_identity(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()

    accesses = _accesses("cd ./sub | cat; bash ./test.sh", tmp_path)

    exec_paths = {
        path.as_posix()
        for path, action, source in accesses
        if action == "exec" and source == "shlex"
    }
    assert exec_paths == {(tmp_path / "test.sh").as_posix()}


def test_cd_preserves_lexical_and_resolved_cwd_identities(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    real_directory = tmp_path / "real"
    workspace.mkdir()
    real_directory.mkdir()
    (workspace / "linked").symlink_to(real_directory, target_is_directory=True)

    accesses = extract_accesses_native(
        "bash",
        {"command": "cd .; cat report.csv", "workdir": "linked"},
        workspace,
    )

    read_paths = {
        path.as_posix()
        for path, action, source in accesses
        if path.name == "report.csv" and action == "read" and source == "shlex"
    }
    assert read_paths == {
        (workspace / "linked" / "report.csv").as_posix(),
        (real_directory / "report.csv").as_posix(),
    }


@pytest.mark.asyncio
async def test_failed_cd_cannot_bypass_original_cwd_fileguard(tmp_path: Path) -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(tmp_path / "secret.txt"),
                        "read": "allow",
                        "write": "deny",
                        "exec": "allow",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission(
        "bash",
        {"command": "cd ./missing; printf ok > ./secret.txt"},
    )

    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_interpreter_script_is_denied_by_existing_fileguard_contract(tmp_path: Path) -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "defaults": {"*": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [
                    {
                        "path": str(tmp_path / "test.sh"),
                        "read": "allow",
                        "write": "allow",
                        "exec": "deny",
                    }
                ],
            },
        },
        workspace_root=tmp_path,
    )

    result = await engine.check_permission("bash", {"command": "bash ./test.sh"})

    assert result.permission == PermissionLevel.DENY
