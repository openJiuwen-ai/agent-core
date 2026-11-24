# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local POSIX path analysis and policy regression tests."""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from shlex import quote
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.fileguard.file_guard import build_file_guard_checker
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import extract_accesses_native
from openjiuwen.harness.security.permission_engine.fileguard.shell_paths import analyze_posix_shell_paths
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.shell_path_context import shell_path_call

A44 = (
    "cd sub && printf '%s' 'SUB_OUTPUT' > output.txt; echo \"exit=$?\"; "
    "echo \"--- sub/output.txt ---\"; ls -l output.txt; echo \"[content]\"; cat output.txt; echo; "
    "echo \"--- root output.txt (after) ---\"; cd .. && ls -l output.txt && md5sum output.txt "
    "&& echo \"[content]\"; cat output.txt; echo"
)
BUILD_CHECK = (
    'mkdir -p ./project/src ./project/logs && cd ./project/src && echo "main() {}" > ./app.c '
    '&& ls -la ../non_existent_dir >> ../logs/build.log 2>&1 ; cd ../logs '
    '&& echo "--- Build Check ---" >> ./build.log && cat ./build.log && cd - && pwd'
)
BASH_STACK = (
    'mkdir -p "./dir with spaces/sub_a" ../sibling_dir && cd "./dir with spaces/sub_a" '
    '&& pushd ../../sibling_dir > /dev/null && touch ./file1.txt "../dir with spaces/file2.txt" '
    r'&& popd && echo "data" >> \~/test_output.log 2>&1 && cd - && cat < ./file1.txt > ../sub_a/file3.txt'
)
SUBSHELL_ARCHIVE = (
    '(cd /tmp && tar -cf ./backup.tar /var/log/syslog) && cp /tmp/backup.tar ./local_backup.tar '
    '&& echo $(cd ../ && pwd)/config.json > ./path_ref.txt'
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX path semantics")


def _analysis(command, workspace):
    return analyze_posix_shell_paths(command, str(workspace), "auto", workspace)


def test_a44_candidates_include_both_directory_outcomes(tmp_path):
    workspace = tmp_path.resolve() / "root"
    result = _analysis(A44, workspace)
    assert result.coverage == "complete"
    assert {
        (entry.path.relative_to(workspace.parent).as_posix(), entry.action,
         entry.kind, entry.requires_prior_directory_change_failure)
        for entry in result.accesses
    } == {
        ("root/sub", "read", "directory_change", False),
        ("root", "read", "directory_change", False),
        (".", "read", "directory_change", True),
        ("root/sub/output.txt", "write", "file_io", False),
        ("root/sub/output.txt", "read", "file_io", False),
        ("root/output.txt", "read", "file_io", False),
        ("output.txt", "read", "file_io", True),
    }


@pytest.mark.parametrize("sub_exists", [True, False])
def test_a44_file_effects_in_isolated_posix_shell(tmp_path, sub_exists):
    if shutil.which("md5sum") is None:
        pytest.skip("The unchanged A44 command requires md5sum")
    workspace = tmp_path / "root"
    workspace.mkdir()
    if sub_exists:
        (workspace / "sub").mkdir()
    root = workspace / "output.txt"
    outer = tmp_path / "output.txt"
    root.write_bytes(b"ROOT_INPUT")
    outer.write_bytes(b"PARENT_INPUT")
    completed = subprocess.run(
        ["/bin/sh", "-c", A44], cwd=workspace, check=True, timeout=10,
        capture_output=True, env={"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin"},
    )
    child = workspace / "sub/output.txt"
    assert root.read_bytes() == b"ROOT_INPUT"
    assert outer.read_bytes() == b"PARENT_INPUT"
    if sub_exists:
        assert child.read_bytes() == b"SUB_OUTPUT"
        assert b"exit=0" in completed.stdout
        assert b"PARENT_INPUT" not in completed.stdout
    else:
        assert not child.exists()
        assert b"exit=0" not in completed.stdout
        assert b"PARENT_INPUT" in completed.stdout


def test_a44_file_guard_checks_possible_parent_read_without_root_write(tmp_path):
    workspace = tmp_path.resolve() / "root"
    args = {"command": A44, "workdir": str(workspace)}
    guarded = build_file_guard_checker(
        {
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow"},
                "paths": [
                    {"path": str(workspace / "output.txt"), "read": "allow", "write": "deny"},
                    {"path": str(workspace.parent / "output.txt"), "read": "deny"},
                ],
            },
        },
        workspace_root=workspace,
    )
    with shell_path_call() as scope:
        scope.bind(SimpleNamespace(analyze_local_paths=lambda _: _analysis(A44, workspace)), args)
        assert not any(
            path == workspace / "output.txt" and action == "write"
            for path, action, _ in extract_accesses_native("bash", args, workspace)
        )
        result = guarded.evaluate("bash", args)
        assert result.permission == PermissionLevel.DENY
        assert result.external_paths == [str(workspace.parent / "output.txt")]


def test_append_and_semicolon_keep_their_correct_cwd(tmp_path):
    workspace = tmp_path.resolve()
    analysis = _analysis("cd sub && cat 'input file' >> out; cat after", workspace)
    paths = {(entry.path, entry.action) for entry in analysis.accesses}
    assert analysis.coverage == "complete"
    assert (workspace / "sub/input file", "read") in paths
    assert (workspace / "sub/out", "write") in paths
    assert (workspace / "out", "write") not in paths
    assert (workspace / "after", "read") in paths
    assert (workspace / "sub/after", "read") in paths


def test_partial_evidence_keeps_confirmed_prefix(tmp_path):
    workspace = tmp_path.resolve()
    analysis = _analysis('cd sub && cat "$target"; cat after', workspace)
    assert analysis.coverage == "partial"
    # The branch that skips the opaque command stays at the initial cwd.
    assert {entry.path for entry in analysis.accesses} == {workspace / "sub", workspace / "after"}


def test_directory_lookup_and_file_access_keep_both_kinds(tmp_path):
    workspace = tmp_path.resolve()
    analysis = _analysis("ls sub; cd sub", workspace)
    assert analysis.coverage == "complete"
    assert {entry.kind for entry in analysis.accesses} == {"directory_change", "file_io"}


def test_failed_second_cd_skips_and_chain_but_reaches_tail(tmp_path):
    workspace = tmp_path.resolve() / "root"
    result = _analysis("cd sub && echo ready; cd .. && cat chained; cat tail", workspace)
    files = {entry.path for entry in result.accesses if entry.kind == "file_io"}
    assert workspace / "sub/tail" in files
    assert workspace / "sub/chained" not in files


def test_output_builtin_is_partial_if_it_has_unverified_options(tmp_path):
    result = _analysis("printf -v name %s data; cat out", tmp_path.resolve())
    assert result.coverage == "partial"
    assert not result.accesses


def test_windows_does_not_bind_local_posix_analysis(tmp_path, monkeypatch):
    import sys

    from openjiuwen.core.sys_operation import LocalWorkConfig, SysOperation, SysOperationCard
    from openjiuwen.harness.tools.shell.bash._tool import BashTool

    tool = BashTool(SysOperation(SysOperationCard(
        id="shell-path-windows-compat",
        work_config=LocalWorkConfig(sandbox_root=[str(tmp_path)]),
    )))
    monkeypatch.setattr(sys, "platform", "win32")
    assert tool.analyze_local_paths({"command": A44, "workdir": str(tmp_path)}) is None


def test_local_path_analysis_resolves_workdir_and_rejects_custom_executor(tmp_path, monkeypatch):
    from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
    from openjiuwen.harness.tools.shell.bash._tool import BashTool

    for name in ("CDPATH", "ENV", "BASH_ENV", "SHELLOPTS", "BASHOPTS", "PWD"):
        monkeypatch.delenv(name, raising=False)
    workdir = tmp_path / "work"
    workdir.mkdir()
    link = tmp_path / "linked-work"
    link.symlink_to(workdir, target_is_directory=True)
    config = LocalWorkConfig(sandbox_root=[str(tmp_path)])
    tool = BashTool(SysOperation(SysOperationCard(id="local-path-analysis", work_config=config)))
    args = {"command": "cd sub && cat file.txt > output.txt", "workdir": str(link)}
    result = tool.analyze_local_paths(args)
    assert result is not None and result.coverage == "complete"
    assert result.cwd == workdir.resolve()
    assert {(entry.path, entry.action) for entry in result.accesses} == {
        (workdir.resolve() / "sub", "read"),
        (workdir.resolve() / "sub/file.txt", "read"),
        (workdir.resolve() / "sub/output.txt", "write"),
    }

    class CustomShellOperation(ShellOperation):
        pass

    custom = CustomShellOperation("shell", OperationMode.LOCAL, "custom test executor", config)
    assert BashTool(SimpleNamespace(shell=lambda: custom)).analyze_local_paths(args) is None


def test_a44_root_write_deny_does_not_block_subdirectory_write(tmp_path):
    workspace = tmp_path.resolve() / "root"
    args = {"command": A44, "workdir": str(workspace)}
    guarded = build_file_guard_checker({
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "allow"},
            "paths": [{"path": str(workspace / "output.txt"), "read": "allow", "write": "deny"}],
        },
    }, workspace_root=workspace)
    baseline = guarded.evaluate("bash", args)
    assert baseline.permission == PermissionLevel.DENY
    assert baseline.external_paths == [str(workspace / "output.txt")]
    with shell_path_call() as scope:
        scope.bind(SimpleNamespace(analyze_local_paths=lambda _: _analysis(A44, workspace)), args)
        assert guarded.evaluate("bash", args) is None


def test_partial_keeps_confirmed_access_and_legacy_deny(tmp_path):
    workspace = tmp_path.resolve()
    protected = workspace / "protected.txt"
    local_file = workspace / "local file"
    command = 'cat "local file"; echo "$VALUE"; cat ./protected.txt'
    analysis = _analysis(command, workspace)
    assert analysis.coverage == "partial"
    assert [entry.path for entry in analysis.accesses] == [local_file]
    args = {"command": command, "workdir": str(workspace)}
    guarded = build_file_guard_checker({
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow"},
            "paths": [{"path": str(protected), "read": "deny"}],
        },
    }, workspace_root=workspace)
    with shell_path_call() as scope:
        scope.bind(SimpleNamespace(analyze_local_paths=lambda _: analysis), args)
        assert set(extract_accesses_native("bash", args, workspace)) == {
            (local_file, "read", "shell_ast"),
            (protected, "read", "shlex"),
        }
        result = guarded.evaluate("bash", args)
        assert result.permission == PermissionLevel.DENY
        assert result.external_paths == [str(protected)]


def test_build_check_projects_both_log_branches_and_created_parents(tmp_path):
    work = tmp_path.resolve() / "work"
    work.mkdir()
    analysis = _analysis(BUILD_CHECK, work)
    assert analysis.coverage == "complete"
    targets = {(entry.path.relative_to(work.parent).as_posix(), entry.action, entry.kind): entry
               for entry in analysis.accesses}
    assert {path for path, action, _ in targets if action == "write"} == {
        "work/project", "work/project/src", "work/project/logs", "work/project/src/app.c",
        "work/project/logs/build.log", "logs/build.log",
    }
    assert {path for path, _, kind in targets if kind == "directory_change"} == {
        "work/project/src", "work/project/logs", "logs", "work",
    }
    assert ("work/project/logs/build.log", "read", "file_io") in targets
    assert ("work/project/non_existent_dir", "read", "file_io") in targets
    assert not targets["logs/build.log", "write", "file_io"].requires_prior_directory_change_failure


@pytest.mark.parametrize("layout", ["normal", "outer_logs", "no_logs"])
def test_build_check_native_file_effects(tmp_path, layout):
    root = tmp_path.resolve()
    work = root / "work"
    work.mkdir()
    if layout != "normal":
        (work / "project").mkdir()
        (work / "project/src").write_text("blocker")
    if layout == "outer_logs":
        (root / "logs").mkdir()
        (root / "logs/build.log").write_text("BEFORE\n")
    result = subprocess.run(
        ["/bin/sh", "-c", BUILD_CHECK], cwd=work, env={"PATH": os.defpath},
        capture_output=True, text=True, timeout=10,
    )
    analysis = _analysis(BUILD_CHECK, work)
    writes = {entry.path for entry in analysis.accesses if entry.action == "write"}
    app = work / "project/src/app.c"
    inner_log = work / "project/logs/build.log"
    outer_log = root / "logs/build.log"
    if layout == "normal":
        assert result.returncode == 0 and not result.stderr
        assert {app, inner_log} <= writes
        assert app.read_text() == "main() {}\n"
        assert "non_existent_dir" in inner_log.read_text()
        assert inner_log.read_text().endswith("--- Build Check ---\n")
        assert result.stdout.splitlines()[-2:] == [str(app.parent)] * 2
        assert not outer_log.exists()
    else:
        assert (work / "project/src").read_text() == "blocker"
        assert (work / "project/logs").is_dir()
        assert not app.is_file() and not inner_log.exists()
        if layout == "outer_logs":
            assert result.returncode == 0
            assert outer_log in writes and outer_log.read_text() == "BEFORE\n--- Build Check ---\n"
            assert result.stdout.splitlines()[-2:] == [str(work)] * 2
        else:
            assert result.returncode != 0 and not outer_log.exists()


def test_unknown_previous_directory_can_recover_after_absolute_cd(tmp_path):
    work = tmp_path.resolve()
    destination = work / "destination"
    command = f"cd - && cat before; cd {quote(str(destination))} && cat after"
    analysis = _analysis(command, work)
    assert analysis.coverage == "partial"
    assert {(entry.path, entry.kind) for entry in analysis.accesses} == {
        (destination, "directory_change"), (destination / "after", "file_io"),
    }


@pytest.mark.parametrize("prepared", [False, True])
def test_bash_directory_stack_uses_native_semantics_with_partial_posix_evidence(tmp_path, prepared):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Requires native Bash")
    work = tmp_path.resolve() / "work"
    work.mkdir()
    assert _analysis(BASH_STACK, work).coverage == "partial"
    if prepared:
        for directory in ("sibling_dir", "sub_a", "dir with spaces/sub_a/~"):
            (work / directory).mkdir(parents=True)
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", BASH_STACK],
        cwd=work, env={"PATH": os.defpath, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=10,
    )
    assert (work.parent / "sibling_dir").is_dir()
    if prepared:
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == str(work / "sibling_dir")
        assert (work / "dir with spaces/sub_a/~/test_output.log").read_text() == "data\n"
        assert all((work / file).is_file()
                   for file in ("sibling_dir/file1.txt", "dir with spaces/file2.txt", "sub_a/file3.txt"))
    else:
        assert result.returncode != 0 and "pushd" in result.stderr
        assert not any(path.is_file() for path in work.rglob("*"))


@pytest.mark.parametrize("has_source", [True, False])
def test_subshell_archive_isolated_from_parent_cwd(tmp_path, has_source):
    root = tmp_path.resolve()
    work, archive, source = root / "work", root / "archive", root / "sentinel.log"
    work.mkdir()
    archive.mkdir()
    if has_source:
        source.write_bytes(b"SYNTHETIC LOG\n")
    assert _analysis(SUBSHELL_ARCHIVE, work).coverage == "partial"
    command = SUBSHELL_ARCHIVE.replace("/tmp", quote(str(archive))).replace("/var/log/syslog", quote(str(source)))
    result = subprocess.run(
        ["/bin/sh", "-c", command], cwd=work, env={"PATH": os.defpath}, capture_output=True, timeout=10,
    )
    copy, ref = work / "local_backup.tar", work / "path_ref.txt"
    assert (archive / "backup.tar").is_file()
    if has_source:
        assert result.returncode == 0 and copy.read_bytes() == (archive / "backup.tar").read_bytes()
        assert ref.read_text() == f"{root}/config.json\n"
        with tarfile.open(copy) as saved:
            member = next(item for item in saved.getmembers() if Path(item.name).name == source.name)
            assert saved.extractfile(member).read() == b"SYNTHETIC LOG\n"
    else:
        assert result.returncode != 0 and not copy.exists() and not ref.exists()
