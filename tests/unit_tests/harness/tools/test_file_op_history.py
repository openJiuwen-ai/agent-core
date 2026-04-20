# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for _append_op_history in openjiuwen.harness.tools.filesystem."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

import pytest

from openjiuwen.harness.tools.filesystem import MAX_HISTORY_PER_FILE, _append_op_history


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
