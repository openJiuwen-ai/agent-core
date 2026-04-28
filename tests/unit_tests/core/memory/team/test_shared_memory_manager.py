# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SharedMemoryManager."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

import pytest

from openjiuwen.core.memory.team.shared_memory import SharedMemoryManager

TEAM_MEMORY_FILENAME = "TEAM_MEMORY.md"
TEAM_MEMORY_MAX_READ_LINES = 200

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_read_team_summary_empty_file_does_not_exist(temp_dir):
    """Test read_team_summary returns empty string when file doesn't exist."""
    manager = SharedMemoryManager(team_memory_dir=temp_dir)

    result = await manager.read_team_summary()

    assert result == ""


@pytest.mark.asyncio
async def test_read_team_summary_with_content(temp_dir):
    """Test read_team_summary returns content when file exists."""
    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    test_content = "# Team Memory\n\n## Summary\nThis is a test team memory."
    os.makedirs(temp_dir, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(test_content)

    manager = SharedMemoryManager(team_memory_dir=temp_dir)

    result = await manager.read_team_summary()

    assert test_content in result


@pytest.mark.asyncio
async def test_read_team_summary_respects_max_lines(temp_dir):
    """Test read_team_summary respects TEAM_MEMORY_MAX_READ_LINES limit."""

    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    lines = [f"Line {i}\n" for i in range(TEAM_MEMORY_MAX_READ_LINES + 10)]
    test_content = "".join(lines)
    os.makedirs(temp_dir, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(test_content)

    manager = SharedMemoryManager(team_memory_dir=temp_dir)

    result = await manager.read_team_summary()

    result_lines = result.split("\n")
    assert len(result_lines) <= TEAM_MEMORY_MAX_READ_LINES


@pytest.mark.asyncio
async def test_write_team_summary_creates_file(temp_dir):
    """Test write_team_summary creates the file."""
    test_content = "# New Team Memory\n\nWritten content."

    manager = SharedMemoryManager(team_memory_dir=temp_dir)
    await manager.write_team_summary(test_content)

    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    assert os.path.exists(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert test_content in content


@pytest.mark.asyncio
async def test_write_team_summary_overwrites_existing(temp_dir):
    """Test write_team_summary overwrites existing content."""
    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    os.makedirs(temp_dir, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Original content")

    manager = SharedMemoryManager(team_memory_dir=temp_dir)
    await manager.write_team_summary("New content")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == "New content"


@pytest.mark.asyncio
async def test_append_entry_first_entry(temp_dir):
    """Test append_entry with no existing content."""
    manager = SharedMemoryManager(team_memory_dir=temp_dir)
    await manager.append_entry("First entry")

    result = await manager.read_team_summary()
    assert "First entry" in result


@pytest.mark.asyncio
async def test_append_entry_adds_separator(temp_dir):
    """Test append_entry adds separator between entries."""
    manager = SharedMemoryManager(team_memory_dir=temp_dir)
    await manager.append_entry("First entry")
    await manager.append_entry("Second entry")

    result = await manager.read_team_summary()
    assert "First entry" in result
    assert "Second entry" in result
    assert "---" in result


@pytest.mark.asyncio
async def test_ensure_dir_creates_directory(temp_dir):
    """Test ensure_dir creates the directory if it doesn't exist."""
    team_dir = os.path.join(temp_dir, "new_team_dir")
    manager = SharedMemoryManager(team_memory_dir=team_dir)

    assert not os.path.exists(team_dir)
    await manager.ensure_dir()
    assert os.path.exists(team_dir)


@pytest.mark.asyncio
async def test_write_to_nested_directory(temp_dir):
    """Test write_team_summary works with nested directory path."""
    nested_dir = os.path.join(temp_dir, "level1", "level2", "team_memory")
    test_content = "# Nested team memory"

    manager = SharedMemoryManager(team_memory_dir=nested_dir)
    await manager.write_team_summary(test_content)

    file_path = os.path.join(nested_dir, TEAM_MEMORY_FILENAME)
    assert os.path.exists(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert test_content in content


@pytest.mark.asyncio
async def test_read_empty_file_returns_empty_string(temp_dir):
    """Test read_team_summary returns empty string for empty file."""
    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    os.makedirs(temp_dir, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("")

    manager = SharedMemoryManager(team_memory_dir=temp_dir)

    result = await manager.read_team_summary()

    assert result == ""


@pytest.mark.asyncio
async def test_append_after_read(temp_dir):
    """Test append after reading preserves original content."""
    original_content = "# Original\n\nThis is original content."
    manager = SharedMemoryManager(team_memory_dir=temp_dir)
    await manager.write_team_summary(original_content)

    await manager.append_entry("New entry")

    result = await manager.read_team_summary()
    assert "Original" in result
    assert "New entry" in result


@pytest.mark.asyncio
async def test_concurrent_writes_yield_single_complete_payload(temp_dir):
    """Concurrent writes: final file equals one full payload (no truncated hybrid)."""
    manager = SharedMemoryManager(team_memory_dir=temp_dir, sys_operation=None)

    payloads = [f"FULL-{i}-" + ("x" * 400) for i in range(16)]

    await asyncio.gather(*[manager.write_team_summary(p) for p in payloads])

    file_path = os.path.join(temp_dir, TEAM_MEMORY_FILENAME)
    with open(file_path, "r", encoding="utf-8") as f:
        body = f.read()

    assert body in payloads


@pytest.mark.asyncio
async def test_write_team_summary_creates_nested_team_memory_dir(temp_dir):
    """Parent path exists but team-memory leaf does not: write creates directory then file."""
    parent = os.path.join(temp_dir, "exists")
    os.makedirs(parent, exist_ok=True)
    nested_team = os.path.join(parent, "nested", "team-memory")

    assert not os.path.exists(nested_team)

    manager = SharedMemoryManager(team_memory_dir=nested_team, sys_operation=None)
    await manager.write_team_summary("nested ok")

    assert os.path.isdir(nested_team)
    target = os.path.join(nested_team, TEAM_MEMORY_FILENAME)
    assert os.path.isfile(target)
    with open(target, "r", encoding="utf-8") as f:
        assert f.read() == "nested ok"
