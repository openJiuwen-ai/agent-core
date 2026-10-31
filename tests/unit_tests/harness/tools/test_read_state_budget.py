# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for read_file's edit-state bookkeeping.

Problem 7f3740bccefc: the first read of a large text file also performed a
full-file read to count lines, and that read iterated the file line by line
through the thread pool. A 13 MB / ~300k-line file took ~22s idle and was
pushed past the 300s tool timeout under event-loop congestion, then retried
from scratch on the next call because the count never got cached.

These tests pin the fix:
* the full read is a single pass, not one executor hop per line;
* the count is bounded in time and never blocks the read result;
* a degraded (unknown) total is cached instead of retried on every read;
* reaching EOF pins the exact total, so an unknown state is recoverable and
  a fully-read file stays writable;
* unknown total still keeps the conservative overwrite refusal.
"""

import asyncio
import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.local.fs_operation import FsOperation
from openjiuwen.harness.tools import ReadFileTool, WriteFileTool
from openjiuwen.harness.tools.filesystem import _FILE_READ_REGISTRY


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_read_state_budget_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo", "dir"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_read_full_reads_once_and_preserves_lines(temp_dir):
    path = os.path.join(temp_dir, "lines.txt")
    content = "a\nb\r\nc\n\nd"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)

    lines = [line async for line in FsOperation._read_full(path, "utf-8")]

    # aiofiles opens text mode with universal newlines, so CRLF is normalised
    # exactly as the previous line-by-line iteration did; only the I/O pattern
    # changed.
    expected = content.replace("\r\n", "\n")
    assert "".join(lines) == expected
    assert lines == expected.splitlines(True)


@pytest.mark.asyncio
async def test_fs_full_read_returns_whole_content(sys_op, temp_dir):
    path = os.path.join(temp_dir, "whole.txt")
    content = "one\ntwo\nthree"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    res = await sys_op.fs().read_file(path)

    assert res.code == 0
    assert res.data.content == content


@pytest.mark.asyncio
async def test_large_file_skips_eager_count(monkeypatch, sys_op, temp_dir):
    tool = ReadFileTool(sys_op)
    path = os.path.join(temp_dir, "huge.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("x" * 128 + "\nsecond")
    _FILE_READ_REGISTRY.pop(path, None)
    monkeypatch.setattr(ReadFileTool, "EAGER_COUNT_MAX_BYTES", 8)

    async def _must_not_read(_self, _path):
        raise AssertionError("eager full-text count must be skipped")

    monkeypatch.setattr(ReadFileTool, "_read_raw_text_for_edit_state", _must_not_read)

    res = await tool.invoke({"file_path": path, "offset": 0, "limit": 1})

    assert res.success is True
    state = _FILE_READ_REGISTRY[path]
    assert state.total_lines_known is False


@pytest.mark.asyncio
async def test_count_timeout_degrades_and_is_cached(monkeypatch, sys_op, temp_dir):
    tool = ReadFileTool(sys_op)
    path = os.path.join(temp_dir, "slow.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"line{i}" for i in range(10)))
    _FILE_READ_REGISTRY.pop(path, None)
    calls = {"n": 0}

    async def _slow(_self, _path):
        calls["n"] += 1
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr(ReadFileTool, "_read_raw_text_for_edit_state", _slow)
    monkeypatch.setattr(ReadFileTool, "STATE_INIT_TIMEOUT", 0.02)

    first = await tool.invoke({"file_path": path, "offset": 0, "limit": 2})
    second = await tool.invoke({"file_path": path, "offset": 0, "limit": 3})

    assert first.success is True
    assert second.success is True
    assert _FILE_READ_REGISTRY[path].total_lines_known is False
    assert calls["n"] == 1, "degraded count must be cached, not retried per read"


@pytest.mark.asyncio
async def test_eof_read_recovers_unknown_total_and_unblocks_write(
        monkeypatch, sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    write_tool = WriteFileTool(sys_op)
    path = os.path.join(temp_dir, "recover.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("a\nb\nc")
    _FILE_READ_REGISTRY.pop(path, None)
    monkeypatch.setattr(ReadFileTool, "EAGER_COUNT_MAX_BYTES", 1)

    await read_tool.invoke({"file_path": path, "offset": 0, "limit": 1})
    assert _FILE_READ_REGISTRY[path].total_lines_known is False

    # Reading to EOF pins the exact total without any counting pass.
    await read_tool.invoke({"file_path": path})
    state = _FILE_READ_REGISTRY[path]
    assert state.total_lines_known is True
    assert state.total_lines == 3

    res = await write_tool.invoke({"file_path": path, "content": "x\ny\nz"})
    assert res.success is True


@pytest.mark.asyncio
async def test_read_past_eof_recovers_when_pages_end_on_limit(
        monkeypatch, sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    write_tool = WriteFileTool(sys_op)
    path = os.path.join(temp_dir, "aligned.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"line{i}" for i in range(6)))
    _FILE_READ_REGISTRY.pop(path, None)
    monkeypatch.setattr(ReadFileTool, "EAGER_COUNT_MAX_BYTES", 1)

    # Two full pages: neither is short, so EOF is not observable yet.
    await read_tool.invoke({"file_path": path, "offset": 0, "limit": 3})
    await read_tool.invoke({"file_path": path, "offset": 3, "limit": 3})
    assert _FILE_READ_REGISTRY[path].total_lines_known is False

    # An empty page at offset 6 proves every earlier line exists; 6 is a safe
    # upper bound and the coverage union already reaches it.
    await read_tool.invoke({"file_path": path, "offset": 6, "limit": 3})
    state = _FILE_READ_REGISTRY[path]
    assert state.total_lines_known is True
    assert state.total_lines == 6

    res = await write_tool.invoke({"file_path": path, "content": "done"})
    assert res.success is True


@pytest.mark.asyncio
async def test_unknown_total_still_blocks_overwrite(monkeypatch, sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    write_tool = WriteFileTool(sys_op)
    path = os.path.join(temp_dir, "blocked.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("a\nb\nc")
    _FILE_READ_REGISTRY.pop(path, None)
    monkeypatch.setattr(ReadFileTool, "EAGER_COUNT_MAX_BYTES", 1)

    await read_tool.invoke({"file_path": path, "offset": 0, "limit": 1})
    assert _FILE_READ_REGISTRY[path].total_lines_known is False

    res = await write_tool.invoke({"file_path": path, "content": "overwrite"})

    assert res.success is False
    assert "fully read" in res.error
    assert "not known" in res.error


@pytest.mark.asyncio
async def test_empty_file_total_is_known_zero(monkeypatch, sys_op, temp_dir):
    tool = ReadFileTool(sys_op)
    path = os.path.join(temp_dir, "empty.txt")
    with open(path, "w", encoding="utf-8"):
        pass
    _FILE_READ_REGISTRY.pop(path, None)
    monkeypatch.setattr(ReadFileTool, "EAGER_COUNT_MAX_BYTES", 8)

    res = await tool.invoke({"file_path": path})

    assert res.success is True
    state = _FILE_READ_REGISTRY[path]
    assert state.total_lines_known is True
    assert state.total_lines == 0


@pytest.mark.asyncio
async def test_edit_state_read_skips_cross_process_lock(monkeypatch, sys_op, temp_dir):
    tool = ReadFileTool(sys_op)
    path = os.path.join(temp_dir, "lock.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("a\nb")

    seen = {}
    original = FsOperation.read_file

    async def _spy(self, file_path, **kwargs):
        seen.update(kwargs)
        return await original(self, file_path, **kwargs)

    monkeypatch.setattr(FsOperation, "read_file", _spy)

    state = await tool._read_raw_text_for_edit_state(path)

    assert state is not None
    assert state.line_count == 2
    assert seen.get("only_read") is True
