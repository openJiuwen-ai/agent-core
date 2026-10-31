"""Finite deletion recognition never executes the command under test."""

import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from openjiuwen.harness.security.permission_engine.toolguard import workspace_delete as subject


@pytest.mark.parametrize(
    "command,dialect,expected",
    [
        ("rm -- file.txt", "posix", ("file.txt",)),
        ("rm -- 'a b.txt' ./next.txt", "posix", ("a b.txt", "./next.txt")),
        ('rm -- "中文.txt"', "posix", ("中文.txt",)),
        ("Remove-Item -LiteralPath 'file name.txt'", "powershell", ("file name.txt",)),
        (r"Remove-Item -LiteralPath 'C:\project\file.txt'", "powershell", (r"C:\project\file.txt",)),
        ('remove-item -literalpath "C:/project/中文.txt"', "powershell", ("C:/project/中文.txt",)),
    ],
)
def test_recognizes_only_literal_supported_forms(command, dialect, expected):
    assert subject.literal_delete_operands(command, dialect) == expected


@pytest.mark.parametrize(
    "command",
    [
        "",
        "rm file",
        "rm -f -- file",
        "rm -- -f",
        "rm --",
        "RM -- file",
        "rm -- a; touch b",
        "rm -- a && touch b",
        "rm -- a | cat",
        "rm -- a > out",
        "rm -- a 2>&1",
        "rm -- a # note",
        "rm -- a\nrm -- b",
        "rm -- a\r",
        "rm\t-- a",
        "rm -- *.txt",
        "rm -- 'a?.txt'",
        "rm -- [ab].txt",
        "rm -- {a,b}",
        "rm -- $FILE",
        "rm -- $(echo a)",
        "rm -- `echo a`",
        "rm -- %FILE%",
        "rm -- ~/a",
        "rm -- a!",
        "rm -- a &",
        "rm -- (a)",
        "rm -- 'a'b",
        "r'm' -- a",
        "rm -- 'a",
        'rm -- "a',
        r"rm -- a\ b",
        "bash -c 'rm -- a'",
        "sudo rm -- a",
        "command rm -- a",
        "env rm -- a",
        "rm -- a\x00",
        "rm -- ''",
        "rm -- a\u007f",
        "rm -- @a",
    ],
)
def test_posix_unknown_or_dynamic_forms_do_not_qualify(command):
    assert subject.literal_delete_operands(command, "posix") is None


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item a",
        "Remove-Item -Path a",
        "ri -LiteralPath a",
        "rm -LiteralPath a",
        "Remove-Item -LiteralPath a,b",
        "Remove-Item -LiteralPath @('a')",
        "Remove-Item -LiteralPath a -Recurse",
        "Remove-Item -LiteralPath a -Force",
        "Remove-Item -LiteralPath $env:FILE",
        "Remove-Item -LiteralPath 'Registry::HKLM\\a'",
        "Remove-Item -LiteralPath HKLM:foo",
        "Remove-Item -LiteralPath Env:HOME",
        "Remove-Item -LiteralPath C:relative",
        "Remove-Item -LiteralPath C:/a:stream",
        "Remove-Item -LiteralPath 'a''b'",
        "Remove-Item -LiteralPath a` b",
        "Remove-Item -LiteralPath a; Get-Content b",
        "Remove-Item -LiteralPath a | Out-File b",
        "Remove-Item -LiteralPath a b",
        "& Remove-Item -LiteralPath a",
        "powershell -Command 'Remove-Item -LiteralPath a'",
        "Remove-Item -LiteralPath 'a*'",
        "Remove-Item -LiteralPath ''",
        "Remove-Item -LiteralPath ‘../outside’",
        "Remove-Item -LiteralPath “../outside”",
        "Remove-Item -LiteralPath –Recurse",
    ],
)
def test_powershell_unknown_or_provider_forms_do_not_qualify(command):
    assert subject.literal_delete_operands(command, "powershell") is None


def recognize(command, cwd, root, dialect="posix"):
    return subject.workspace_delete_targets(command, dialect=dialect, cwd=cwd, workspace_root=root)


def test_every_target_is_resolved_against_actual_cwd(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "a").write_text("data")
    assert recognize("rm -- a 'new file'", root, root) == (root / "a", root / "new file")
    assert recognize("rm -- a", outside, root) is None
    assert recognize(f"rm -- '{root / 'a'}'", outside, root) == ((root / "a",) if os.name != "nt" else None)


@pytest.mark.parametrize("operand", [".", "..", "../project/a", "folder/../a", "../project-other/a"])
def test_workspace_root_parent_traversal_and_prefix_confusion_do_not_qualify(tmp_path, operand):
    root = tmp_path / "project"
    root.mkdir()
    assert recognize(f"rm -- {operand}", root, root) is None


def test_mixed_targets_directory_missing_root_and_relative_cwd_do_not_qualify(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "directory").mkdir()
    assert recognize("rm -- inside ../outside", root, root) is None
    assert recognize("rm -- directory", root, root) is None
    assert recognize("rm -- file", root, None) is None
    assert recognize("rm -- file", root / "missing-cwd", root) is None
    assert recognize("rm -- file", type(root)("relative-cwd"), root) is None
    assert recognize("rm -- file", root, root / "missing-root") is None


@pytest.mark.parametrize("operand", ["NUL", "con.txt", "a.", "a ", "//server/a", r"\\?\C:\a"])
def test_powershell_device_network_and_normalized_path_forms_do_not_qualify(tmp_path, operand):
    assert recognize(f"Remove-Item -LiteralPath '{operand}'", tmp_path, tmp_path, "powershell") is None


def test_safe_powershell_literal_file_is_recognized(tmp_path):
    assert recognize("Remove-Item -LiteralPath 'data file.txt'", tmp_path, tmp_path, "powershell") == (
        tmp_path / "data file.txt",
    )


def test_reparse_points_and_inaccessible_ancestors_fail_closed(tmp_path, monkeypatch):
    original = subject.Path.lstat

    def fake_lstat(path):
        if path.name == "symbolic":
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        if path.name == "junction":
            return SimpleNamespace(st_mode=0, st_file_attributes=0x400)
        if path.name == "denied":
            raise PermissionError("unreadable")
        return original(path)

    monkeypatch.setattr(subject.Path, "lstat", fake_lstat)
    assert recognize("rm -- junction/file", tmp_path, tmp_path) is None
    assert recognize("rm -- denied/file", tmp_path, tmp_path) is None
    assert recognize("rm -- symbolic/file", tmp_path, tmp_path) is None


def test_symlinks_are_refused_even_if_the_target_is_inside_workspace(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "data").write_text("data")
    try:
        (root / "link").symlink_to(root / "data")
        (root / "outside").symlink_to(outside, target_is_directory=True)
        (tmp_path / "linked-root").symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host does not permit symlink creation: {exc}")
    assert recognize("rm -- link", root, root) is None
    assert recognize("rm -- outside/file", root, root) is None
    assert recognize("rm -- data", tmp_path / "linked-root", root) is None
    assert recognize("rm -- data", root, tmp_path / "linked-root") is None


def test_parser_unavailable_complex_or_multiple_commands_do_not_qualify(monkeypatch):
    for kind, commands in [("parse_unavailable", [1]), ("too_complex", [1]), ("simple", [1, 2])]:
        monkeypatch.setattr(
            subject,
            "parse_shell_for_permission",
            lambda _text, k=kind, c=commands: SimpleNamespace(
                kind=k,
                flags=SimpleNamespace(has_risky_structure=lambda: False),
                subcommands=c,
            ),
        )
        assert subject.literal_delete_operands("rm -- file", "posix") is None


@pytest.mark.skipif(os.name != "nt", reason="native NT junction boundary")
def test_actual_windows_junction_is_refused_without_symlink_privilege(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "junction"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:WORKSPACE_DELETE_TEST_LINK "
            "-Target $env:WORKSPACE_DELETE_TEST_TARGET | Out-Null",
        ],
        env={**os.environ, "WORKSPACE_DELETE_TEST_LINK": str(link), "WORKSPACE_DELETE_TEST_TARGET": str(outside)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert getattr(link.lstat(), "st_file_attributes", 0) & 0x400
    assert recognize("Remove-Item -LiteralPath 'junction/file.txt'", root, root, "powershell") is None
