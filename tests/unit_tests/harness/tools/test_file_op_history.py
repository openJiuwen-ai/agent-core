# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for _append_op_history, _parse_rm_targets, _detect_and_record_deletions,
and _record_rm_targets_before_deletion in openjiuwen.harness.tools.filesystem."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.tools.filesystem import (
    MAX_HISTORY_PER_FILE,
    _append_op_history,
    _detect_and_record_deletions,
    _parse_ps_remove_targets,
    _parse_rm_targets,
    _record_rm_targets_before_deletion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------

class TestAppendOpHistoryBasic(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_creates_history_file(self):
        """History file is created on first call."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/bar.py", "write", None, "content")
            assert os.path.exists(history_path)

    @pytest.mark.asyncio
    async def test_entry_fields(self):
        """Entry contains action, timestamp, old_content, new_content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/bar.py", "write", None, "hello")
            entry = _load(history_path)["/foo/bar.py"][0]
            assert entry["action"] == "write"
            assert entry["old_content"] is None
            assert entry["new_content"] == "hello"
            assert "timestamp" in entry

    @pytest.mark.asyncio
    async def test_old_content_none_for_create(self):
        """New file write stores old_content as null."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/new.py", "write", None, "body")
            assert _load(history_path)["/foo/new.py"][0]["old_content"] is None

    @pytest.mark.asyncio
    async def test_edit_preserves_old_and_new(self):
        """Edit action stores both old and new content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/bar.py", "edit", "old", "new")
            entry = _load(history_path)["/foo/bar.py"][0]
            assert entry["action"] == "edit"
            assert entry["old_content"] == "old"
            assert entry["new_content"] == "new"

    @pytest.mark.asyncio
    async def test_multiple_entries_appended_in_order(self):
        """Multiple calls append entries in order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/bar.py", "write", None, "v1")
            await _append_op_history(history_path, "/foo/bar.py", "edit", "v1", "v2")
            await _append_op_history(history_path, "/foo/bar.py", "edit", "v2", "v3")
            entries = _load(history_path)["/foo/bar.py"]
            assert len(entries) == 3
            assert [e["new_content"] for e in entries] == ["v1", "v2", "v3"]

    @pytest.mark.asyncio
    async def test_multiple_files_tracked_separately(self):
        """Different file paths are stored under separate keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/a.py", "write", None, "a")
            await _append_op_history(history_path, "/foo/b.py", "write", None, "b")
            data = _load(history_path)
            assert data["/foo/a.py"][0]["new_content"] == "a"
            assert data["/foo/b.py"][0]["new_content"] == "b"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestAppendOpHistoryPersistence(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_appends_to_existing_history(self):
        """Existing history file is read and new entry is appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/bar.py", "write", None, "v1")
            await _append_op_history(history_path, "/foo/bar.py", "edit", "v1", "v2")
            assert len(_load(history_path)["/foo/bar.py"]) == 2

    @pytest.mark.asyncio
    async def test_existing_other_file_entries_preserved(self):
        """Other file entries are not lost when a new entry is added."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await _append_op_history(history_path, "/foo/a.py", "write", None, "a")
            await _append_op_history(history_path, "/foo/b.py", "write", None, "b")
            await _append_op_history(history_path, "/foo/a.py", "edit", "a", "a2")
            data = _load(history_path)
            assert len(data["/foo/a.py"]) == 2
            assert len(data["/foo/b.py"]) == 1


# ---------------------------------------------------------------------------
# Max entries cap
# ---------------------------------------------------------------------------

class TestAppendOpHistoryMaxEntries(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_entries_capped_at_max(self):
        """Entries per file are capped at MAX_HISTORY_PER_FILE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            for i in range(MAX_HISTORY_PER_FILE + 10):
                await _append_op_history(history_path, "/foo/bar.py", "edit", str(i), str(i + 1))
            assert len(_load(history_path)["/foo/bar.py"]) == MAX_HISTORY_PER_FILE

    @pytest.mark.asyncio
    async def test_oldest_entries_dropped_when_capped(self):
        """When cap is exceeded, oldest entries are dropped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            for i in range(MAX_HISTORY_PER_FILE + 5):
                await _append_op_history(history_path, "/foo/bar.py", "edit", str(i), str(i + 1))
            entries = _load(history_path)["/foo/bar.py"]
            assert entries[0]["old_content"] == str(5)
            assert entries[-1]["old_content"] == str(MAX_HISTORY_PER_FILE + 4)


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestAppendOpHistoryExceptions(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_invalid_history_path_does_not_raise(self):
        """Unwritable history path is silently logged, not raised."""
        bad_path = "/nonexistent_root/no_permission/.agent_history/file_ops.json"
        await _append_op_history(bad_path, "/foo/bar.py", "write", None, "content")

    @pytest.mark.asyncio
    async def test_corrupted_json_does_not_raise(self):
        """Corrupted existing history file is silently handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            os.makedirs(os.path.dirname(history_path), exist_ok=True)
            with open(history_path, "w") as f:
                f.write("not valid json{{{")
            await _append_op_history(history_path, "/foo/bar.py", "write", None, "content")


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestAppendOpHistoryConcurrency(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_concurrent_coroutines_do_not_corrupt(self):
        """Concurrent coroutines produce consistent JSON without corruption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "file_ops_test.json")
            await asyncio.gather(*(
                _append_op_history(history_path, "/foo/bar.py", "edit", str(i), str(i + 1))
                for i in range(20)
            ))
            data = _load(history_path)
            assert isinstance(data, dict)
            assert "/foo/bar.py" in data
            assert len(data["/foo/bar.py"]) <= MAX_HISTORY_PER_FILE


# ---------------------------------------------------------------------------
# _parse_rm_targets
# ---------------------------------------------------------------------------

class TestParseRmTargets(unittest.TestCase):

    def test_simple_file(self):
        assert _parse_rm_targets("rm foo.py") == ["foo.py"]

    def test_force_flag_single_file(self):
        assert _parse_rm_targets("rm -f foo.py") == ["foo.py"]

    def test_multiple_explicit_files(self):
        assert _parse_rm_targets("rm -f a.py b.py") == ["a.py", "b.py"]

    def test_recursive_flag_returns_empty(self):
        assert _parse_rm_targets("rm -rf dir/") == []

    def test_uppercase_recursive_flag_returns_empty(self):
        assert _parse_rm_targets("rm -R dir/") == []

    def test_glob_pattern_skipped(self):
        assert _parse_rm_targets("rm *.py") == []

    def test_compound_command_with_semicolon_returns_empty(self):
        assert _parse_rm_targets("rm foo.py; echo done") == []

    def test_compound_command_with_pipe_returns_empty(self):
        assert _parse_rm_targets("rm foo.py | cat") == []

    def test_subcommand_returns_empty(self):
        assert _parse_rm_targets("rm $(find . -name '*.py')") == []

    def test_non_rm_command_returns_empty(self):
        assert _parse_rm_targets("ls -la") == []

    def test_empty_command_returns_empty(self):
        assert _parse_rm_targets("") == []

    def test_mixed_glob_and_explicit(self):
        result = _parse_rm_targets("rm *.py explicit.py")
        assert result == ["explicit.py"]


# ---------------------------------------------------------------------------
# _detect_and_record_deletions
# ---------------------------------------------------------------------------

class TestDetectAndRecordDeletions(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_records_delete_for_missing_file(self):
        """A file in history that no longer exists gets a delete entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            target = os.path.join(tmpdir, "foo.py")

            # Simulate a prior write: file existed, content recorded
            with open(target, "w") as f:
                f.write("old content")
            await _append_op_history(history_path, target, "write", None, "old content")

            # Delete the actual file, then run detection
            os.remove(target)
            await _detect_and_record_deletions(history_path)

            data = _load(history_path)
            entries = data[target]
            assert entries[-1]["action"] == "delete"
            assert entries[-1]["old_content"] == "old content"
            assert entries[-1]["new_content"] is None

    @pytest.mark.asyncio
    async def test_does_not_double_record_delete(self):
        """Calling detection twice does not add a second delete entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            target = os.path.join(tmpdir, "foo.py")

            with open(target, "w") as f:
                f.write("content")
            await _append_op_history(history_path, target, "write", None, "content")
            os.remove(target)

            await _detect_and_record_deletions(history_path)
            await _detect_and_record_deletions(history_path)

            entries = _load(history_path)[target]
            delete_entries = [e for e in entries if e["action"] == "delete"]
            assert len(delete_entries) == 1

    @pytest.mark.asyncio
    async def test_still_existing_file_not_marked_deleted(self):
        """A file still on disk is not given a delete entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            target = os.path.join(tmpdir, "foo.py")

            with open(target, "w") as f:
                f.write("content")
            await _append_op_history(history_path, target, "write", None, "content")

            # Do NOT delete the file
            await _detect_and_record_deletions(history_path)

            entries = _load(history_path)[target]
            assert all(e["action"] != "delete" for e in entries)

    @pytest.mark.asyncio
    async def test_no_history_file_is_noop(self):
        """Missing history file does not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            await _detect_and_record_deletions(history_path)  # must not raise

    @pytest.mark.asyncio
    async def test_delete_old_content_taken_from_last_entry_new_content(self):
        """delete.old_content equals the new_content of the last write/edit entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            target = os.path.join(tmpdir, "foo.py")

            with open(target, "w") as f:
                f.write("v2")
            await _append_op_history(history_path, target, "write", None, "v1")
            await _append_op_history(history_path, target, "edit", "v1", "v2")
            os.remove(target)
            await _detect_and_record_deletions(history_path)

            last = _load(history_path)[target][-1]
            assert last["action"] == "delete"
            assert last["old_content"] == "v2"


# ---------------------------------------------------------------------------
# _record_rm_targets_before_deletion
# ---------------------------------------------------------------------------

def _make_mock_operation(file_content: str):
    """Build a minimal mock SysOperation whose fs().read_file() returns file_content."""
    from openjiuwen.core.common.exception.codes import StatusCode

    mock_data = MagicMock()
    mock_data.content = file_content

    mock_res = MagicMock()
    mock_res.code = StatusCode.SUCCESS.code
    mock_res.data = mock_data

    mock_fs = MagicMock()
    mock_fs.read_file = AsyncMock(return_value=mock_res)

    mock_op = MagicMock()
    mock_op.fs = MagicMock(return_value=mock_fs)
    return mock_op


class TestRecordRmTargetsBeforeDeletion(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_records_content_for_existing_file(self):
        """Existing file is read and recorded as delete before rm runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            target = os.path.join(tmpdir, "foo.py")
            with open(target, "w") as f:
                f.write("file content")

            mock_op = _make_mock_operation("file content")
            await _record_rm_targets_before_deletion(history_path, [target], mock_op)

            data = _load(history_path)
            assert target in data
            entry = data[target][0]
            assert entry["action"] == "delete"
            assert entry["old_content"] == "file content"
            assert entry["new_content"] is None

    @pytest.mark.asyncio
    async def test_skips_nonexistent_file(self):
        """Paths that do not exist on disk are silently skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            mock_op = _make_mock_operation("")
            await _record_rm_targets_before_deletion(
                history_path, ["/no/such/file.py"], mock_op
            )
            assert not os.path.exists(history_path)

    @pytest.mark.asyncio
    async def test_skips_directory(self):
        """Directory paths are silently skipped (rm -rf handled by post-detection)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            mock_op = _make_mock_operation("")
            await _record_rm_targets_before_deletion(history_path, [tmpdir], mock_op)
            assert not os.path.exists(history_path)

    @pytest.mark.asyncio
    async def test_multiple_targets_all_recorded(self):
        """Multiple explicit targets are each recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, ".agent_history", "ops.json")
            paths = []
            for name in ("a.py", "b.py"):
                p = os.path.join(tmpdir, name)
                with open(p, "w") as f:
                    f.write(f"content of {name}")
                paths.append(p)

            mock_op = _make_mock_operation("content")
            mock_op.fs().read_file = AsyncMock(
                side_effect=[
                    _make_mock_operation("content of a.py").fs().read_file.return_value,
                    _make_mock_operation("content of b.py").fs().read_file.return_value,
                ]
            )
            await _record_rm_targets_before_deletion(history_path, paths, mock_op)

            data = _load(history_path)
            assert paths[0] in data
            assert paths[1] in data


# ---------------------------------------------------------------------------
# _parse_ps_remove_targets
# ---------------------------------------------------------------------------

class TestParsePsRemoveTargets(unittest.TestCase):

    def test_simple_remove_item(self):
        assert _parse_ps_remove_targets("Remove-Item foo.py") == ["foo.py"]

    def test_alias_rm(self):
        assert _parse_ps_remove_targets("rm foo.py") == ["foo.py"]

    def test_alias_del(self):
        assert _parse_ps_remove_targets("del foo.py") == ["foo.py"]

    def test_alias_ri(self):
        assert _parse_ps_remove_targets("ri foo.py") == ["foo.py"]

    def test_alias_erase(self):
        assert _parse_ps_remove_targets("erase foo.py") == ["foo.py"]

    def test_force_flag(self):
        assert _parse_ps_remove_targets("Remove-Item -Force foo.py") == ["foo.py"]

    def test_path_flag(self):
        assert _parse_ps_remove_targets("Remove-Item -Path foo.py") == ["foo.py"]

    def test_literalpath_flag(self):
        assert _parse_ps_remove_targets("Remove-Item -LiteralPath foo.py") == ["foo.py"]

    def test_multiple_explicit_files(self):
        result = _parse_ps_remove_targets("Remove-Item a.py b.py")
        assert result == ["a.py", "b.py"]

    def test_windows_absolute_path(self):
        result = _parse_ps_remove_targets("Remove-Item C:/work/foo.py")
        assert result == ["C:/work/foo.py"]

    def test_recurse_returns_empty(self):
        assert _parse_ps_remove_targets("Remove-Item -Recurse dir") == []

    def test_wildcard_returns_empty(self):
        assert _parse_ps_remove_targets("Remove-Item *.py") == []

    def test_compound_with_semicolon_returns_empty(self):
        assert _parse_ps_remove_targets("Remove-Item foo.py; echo done") == []

    def test_compound_with_pipe_returns_empty(self):
        assert _parse_ps_remove_targets("Remove-Item foo.py | Out-Null") == []

    def test_non_remove_command_returns_empty(self):
        assert _parse_ps_remove_targets("Get-Item foo.py") == []

    def test_empty_command_returns_empty(self):
        assert _parse_ps_remove_targets("") == []

    def test_error_action_flag_stripped(self):
        result = _parse_ps_remove_targets("Remove-Item -ErrorAction SilentlyContinue foo.py")
        assert result == ["foo.py"]

    def test_case_insensitive_command(self):
        assert _parse_ps_remove_targets("remove-item foo.py") == ["foo.py"]

    def test_glob_in_path_flag_returns_empty(self):
        assert _parse_ps_remove_targets("Remove-Item -Path *.py") == []
