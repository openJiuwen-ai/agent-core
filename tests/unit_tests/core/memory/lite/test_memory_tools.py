# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Functional tests for refactored ``memory_tools`` (delegate to ``memory_tool_ops``)."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.workspace.workspace import Workspace

from openjiuwen.core.memory.lite.memory_tools import (
    _validate_memory_path,
    bind_memory_runtime,
    clear_memory_runtime,
    edit_memory,
    get_decorated_tools,
    memory_get,
    memory_search,
    read_memory,
    write_memory,
)


@pytest.fixture(autouse=True)
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest_asyncio.fixture(autouse=True)
async def memory_tools_setup(temp_dir):
    """Runner + sys_operation + workspace(memory) + bind_memory_runtime."""
    temp_dir_value = temp_dir
    await Runner.start()
    card_id = "test_memory_tools_setup"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(
            shell_allowlist=[
                "echo",
                "ls",
                "dir",
                "cd",
                "pwd",
                "python",
                "python3",
                "cat",
                "mkdir",
            ],
        ),
    )
    Runner.resource_mgr.add_sys_operation(card)
    sys_op = Runner.resource_mgr.get_sys_operation(card_id)

    memory_dir = os.path.join(temp_dir_value, "memory")
    os.makedirs(memory_dir, exist_ok=True)

    workspace = Workspace(
        root_path=temp_dir_value,
        directories=[{"name": "memory", "path": "memory"}],
    )

    bind_memory_runtime(workspace, sys_op)

    yield sys_op, memory_dir

    clear_memory_runtime()
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_get_decorated_tools(memory_tools_setup):
    tools = get_decorated_tools()
    assert len(tools) == 5
    names = [t.card.name for t in tools]
    assert "memory_search" in names
    assert "memory_get" in names
    assert "write_memory" in names
    assert "edit_memory" in names
    assert "read_memory" in names


@pytest.mark.asyncio
async def test_write_memory_success(memory_tools_setup):
    _sys_op, _memory_dir = memory_tools_setup
    result = await write_memory.invoke(
        {"path": "notes/hello.md", "content": "# Hello\n\nbody", "append": False}
    )
    assert result.get("success") is True, result
    full = result.get("fullPath") or result.get("path")
    assert full and os.path.isfile(full)


@pytest.mark.asyncio
async def test_read_memory_success(memory_tools_setup):
    _sys_op, _memory_dir = memory_tools_setup
    await write_memory.invoke(
        {"path": "notes/readme.md", "content": "line1\nline2\nline3", "append": False}
    )
    result = await read_memory.invoke({"path": "notes/readme.md", "offset": 1, "limit": 2})
    assert result.get("success") is True, result
    assert "line1" in result.get("content", "")


@pytest.mark.asyncio
async def test_edit_memory_success(memory_tools_setup):
    _sys_op, _memory_dir = memory_tools_setup
    await write_memory.invoke(
        {"path": "notes/edit.md", "content": "alpha beta", "append": False}
    )
    result = await edit_memory.invoke(
        {"path": "notes/edit.md", "old_text": "beta", "new_text": "gamma"}
    )
    assert result.get("success") is True, result
    read_result = await read_memory.invoke({"path": "notes/edit.md"})
    assert "gamma" in read_result.get("content", "")


@pytest.mark.asyncio
async def test_validate_memory_path_invalid_traversal(memory_tools_setup):
    ok, msg = _validate_memory_path("../escape.md")
    assert ok is False
    assert "traversal" in msg.lower() or "Invalid" in msg


@pytest.mark.asyncio
async def test_memory_get_requires_manager_or_lazy_init(memory_tools_setup):
    await write_memory.invoke({"path": "indexed.md", "content": "x", "append": False})
    result = await memory_get.invoke({"path": "indexed.md"})
    assert "disabled" in result
    if result["disabled"]:
        assert "error" in result


@pytest.mark.asyncio
async def test_memory_search_returns_structured_result(memory_tools_setup):
    result = await memory_search.invoke({"query": "anything"})
    assert isinstance(result, dict)
    assert "results" in result or "disabled" in result


@pytest.mark.asyncio
async def test_clear_memory_runtime_isolates_state(memory_tools_setup):
    clear_memory_runtime()
    r = await memory_search.invoke({"query": "x"})
    assert r.get("disabled") is True
    assert "error" in r
