# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for PowerShell output truncation and large-output persistence."""

from __future__ import annotations

import getpass
import os
import stat
import tempfile
from pathlib import Path

import pytest

from openjiuwen.harness.tools.shell.powershell import _output
from openjiuwen.harness.tools.shell.powershell._output import (
    _output_dir,
    _output_dir_name,
    persist_large_output,
    truncate_output,
)

requires_posix_modes = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX permission bits are not enforced on Windows",
)


@pytest.fixture
def temp_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the process temporary directory at an isolated tmp_path."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # gettempdir() memoises its answer; clear it so the env var is re-read.
    monkeypatch.setattr(tempfile, "tempdir", None)
    return tmp_path


class TestTruncateOutput:

    def test_short_text_unchanged(self) -> None:
        text = "hello world"
        assert truncate_output(text, 1000) == text

    def test_no_limit_keeps_everything(self) -> None:
        text = "x" * 5000
        assert truncate_output(text, 0) == text

    def test_long_text_has_gap_marker(self) -> None:
        result = truncate_output("x" * 500, 250)
        assert "lines omitted" in result

    def test_head_and_tail_preserved(self) -> None:
        text = "\n".join(f"line-{i}" for i in range(100))
        result = truncate_output(text, 200)
        assert result.startswith("line-0")
        assert "line-99" in result


class TestPersistLargeOutput:
    """The persisted-output directory lives in a world-writable temp root.

    A fixed name there belongs to whichever OS account created it first,
    so the name has to be scoped to the current user and the directory
    has to be owner-only.
    """

    def test_directory_name_carries_the_uid(self, temp_root: Path) -> None:
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            pytest.skip("os.getuid is unavailable on this platform")
        assert _output_dir_name().endswith(f"-{getuid()}")

    def test_directory_name_falls_back_to_the_login_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Windows exposes no getuid; the login name stands in for it.
        monkeypatch.delattr(os, "getuid", raising=False)
        monkeypatch.setattr(getpass, "getuser", lambda: "Some User\\name")
        assert _output_dir_name().endswith("-Some_User_name")

    def test_persisted_path_is_inside_the_per_user_directory(self, temp_root: Path) -> None:
        path, size = persist_large_output("output body", "")
        parent = Path(path).parent
        assert parent == temp_root / _output_dir_name()
        assert parent.parent == temp_root
        assert size == len(b"output body")

    @requires_posix_modes
    def test_directory_is_owner_only(self, temp_root: Path) -> None:
        persist_large_output("output body", "")
        directory = temp_root / _output_dir_name()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    @requires_posix_modes
    def test_pre_existing_directory_is_tightened(self, temp_root: Path) -> None:
        directory = temp_root / _output_dir_name()
        directory.mkdir(parents=True)
        directory.chmod(0o755)
        persist_large_output("output body", "")
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    def test_identical_content_reuses_the_same_path(self, temp_root: Path) -> None:
        first, _ = persist_large_output("same body", "same error")
        second, _ = persist_large_output("same body", "same error")
        assert first == second
        # Content-addressed names are what lets a path survive a restart.
        assert Path(first).read_text(encoding="utf-8").startswith("same body")

    def test_differing_content_yields_distinct_paths(self, temp_root: Path) -> None:
        first, _ = persist_large_output("body one", "")
        second, _ = persist_large_output("body two", "")
        assert first != second

    def test_temp_dir_override_relocates_the_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # The directory is resolved per call, so the temp root is honoured
        # even though it is only known after import time.
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()

        monkeypatch.setenv("TMPDIR", str(first_root))
        monkeypatch.setattr(tempfile, "tempdir", None)
        first, _ = persist_large_output("relocated body", "")

        monkeypatch.setenv("TMPDIR", str(second_root))
        monkeypatch.setattr(tempfile, "tempdir", None)
        second, _ = persist_large_output("relocated body", "")

        assert Path(first).parent.parent == first_root
        assert Path(second).parent.parent == second_root
        assert Path(first).name == Path(second).name

    def test_recreates_the_directory_after_external_cleanup(self, temp_root: Path) -> None:
        first, _ = persist_large_output("output body", "")
        Path(first).unlink()
        (temp_root / _output_dir_name()).rmdir()
        second, _ = persist_large_output("output body", "")
        assert Path(second).exists()

    def test_chmod_failure_is_logged_and_not_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        temp_root: Path,
    ) -> None:
        # A temp root mounted without permission support must not break the tool.
        warnings: list[tuple] = []

        def fail_chmod(self: Path, mode: int, **kwargs: object) -> None:
            raise OSError("chmod is unsupported here")

        class RecordingLogger:
            @staticmethod
            def warning(*args: object) -> None:
                warnings.append(args)

        monkeypatch.setattr(Path, "chmod", fail_chmod)
        monkeypatch.setattr(_output, "sys_operation_logger", RecordingLogger)

        path, _ = persist_large_output("output body", "")

        assert Path(path).exists()
        assert len(warnings) == 1

    def test_output_dir_returns_an_existing_directory(self, temp_root: Path) -> None:
        directory = _output_dir()
        assert directory.is_dir()
        assert directory.name == _output_dir_name()
