# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest

from openjiuwen.harness.tools.bash._semantics import (
    CommandKind,
    ExitCodeMeaning,
    classify_command,
    interpret_exit_code,
    is_read_only,
    is_silent,
)


# ── classify_command ──────────────────────────────────────────

class TestClassifyCommand:

    @pytest.mark.parametrize("cmd,expected", [
        ("grep -r foo .", CommandKind.SEARCH),
        ("rg pattern", CommandKind.SEARCH),
        ("find . -name '*.py'", CommandKind.SEARCH),
        ("/usr/bin/grep foo", CommandKind.SEARCH),
    ])
    def test_search(self, cmd: str, expected: CommandKind) -> None:
        assert classify_command(cmd) == expected

    @pytest.mark.parametrize("cmd,expected", [
        ("cat foo.txt", CommandKind.READ),
        ("head -20 file.log", CommandKind.READ),
        ("wc -l *.py", CommandKind.READ),
        ("jq .name data.json", CommandKind.READ),
    ])
    def test_read(self, cmd: str, expected: CommandKind) -> None:
        assert classify_command(cmd) == expected

    @pytest.mark.parametrize("cmd,expected", [
        ("ls -la", CommandKind.LIST),
        ("tree src/", CommandKind.LIST),
        ("du -sh .", CommandKind.LIST),
    ])
    def test_list(self, cmd: str, expected: CommandKind) -> None:
        assert classify_command(cmd) == expected

    @pytest.mark.parametrize("cmd,expected", [
        ("echo hello", CommandKind.NEUTRAL),
        ("printf '%s\\n' foo", CommandKind.NEUTRAL),
        ("true", CommandKind.NEUTRAL),
    ])
    def test_neutral(self, cmd: str, expected: CommandKind) -> None:
        assert classify_command(cmd) == expected

    @pytest.mark.parametrize("cmd,expected", [
        ("mkdir -p /tmp/foo", CommandKind.SILENT),
        ("mv a.txt b.txt", CommandKind.SILENT),
        ("chmod 755 script.sh", CommandKind.SILENT),
    ])
    def test_silent(self, cmd: str, expected: CommandKind) -> None:
        assert classify_command(cmd) == expected

    def test_pipeline_uses_last_segment(self) -> None:
        assert classify_command("cat foo | grep bar") == CommandKind.SEARCH
        assert classify_command("grep foo | wc -l") == CommandKind.READ

    def test_unknown_command(self) -> None:
        assert classify_command("docker build .") == CommandKind.OTHER

    def test_empty(self) -> None:
        assert classify_command("") == CommandKind.OTHER

    def test_env_prefix_stripped(self) -> None:
        assert classify_command("FOO=bar grep pattern") == CommandKind.SEARCH


# ── is_read_only ──────────────────────────────────────────────

class TestIsReadOnly:

    def test_pure_read_pipeline(self) -> None:
        assert is_read_only("cat foo.txt | grep bar | wc -l") is True

    def test_echo_is_neutral(self) -> None:
        assert is_read_only("echo hello | grep h") is True

    def test_write_command_breaks_readonly(self) -> None:
        assert is_read_only("cat foo.txt && rm foo.txt") is False

    def test_unknown_breaks_readonly(self) -> None:
        assert is_read_only("docker ps") is False

    def test_single_list(self) -> None:
        assert is_read_only("ls -la") is True

    def test_empty(self) -> None:
        assert is_read_only("") is False


# ── is_silent ─────────────────────────────────────────────────

class TestIsSilent:

    def test_mkdir(self) -> None:
        assert is_silent("mkdir -p /tmp/foo") is True

    def test_mv_and_cp(self) -> None:
        assert is_silent("mv a b && cp c d") is True

    def test_echo_is_neutral_not_silent(self) -> None:
        assert is_silent("echo hello") is True  # neutral segments are skipped

    def test_grep_not_silent(self) -> None:
        assert is_silent("grep foo bar") is False

    def test_empty(self) -> None:
        assert is_silent("") is False


# ── interpret_exit_code ───────────────────────────────────────

class TestInterpretExitCode:

    def test_zero_always_ok(self) -> None:
        m = interpret_exit_code("anything", 0)
        assert m.is_error is False
        assert m.message is None

    def test_grep_1_no_match(self) -> None:
        m = interpret_exit_code("grep foo bar.txt", 1)
        assert m.is_error is False
        assert m.message == "No matches found"

    def test_grep_2_is_error(self) -> None:
        m = interpret_exit_code("grep foo bar.txt", 2)
        assert m.is_error is True

    def test_diff_1_files_differ(self) -> None:
        m = interpret_exit_code("diff a.txt b.txt", 1)
        assert m.is_error is False
        assert m.message == "Files differ"

    def test_diff_2_is_error(self) -> None:
        m = interpret_exit_code("diff a.txt b.txt", 2)
        assert m.is_error is True

    def test_find_1_partial(self) -> None:
        m = interpret_exit_code("find / -name foo", 1)
        assert m.is_error is False

    def test_test_1_false(self) -> None:
        m = interpret_exit_code("test -f missing.txt", 1)
        assert m.is_error is False
        assert m.message == "Condition is false"

    def test_pipeline_uses_last_segment(self) -> None:
        m = interpret_exit_code("cat file | grep missing", 1)
        assert m.is_error is False
        assert m.message == "No matches found"

    def test_unknown_command_1_is_error(self) -> None:
        m = interpret_exit_code("python script.py", 1)
        assert m.is_error is True

    def test_rg_1_no_match(self) -> None:
        m = interpret_exit_code("rg pattern .", 1)
        assert m.is_error is False
