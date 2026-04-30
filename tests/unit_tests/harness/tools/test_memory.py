# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for harness memory tools."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.lite.memory_tool_ops import validate_memory_path
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.tools.memory import (
    EditMemoryTool,
    MemoryGetTool,
    MemorySearchTool,
    ReadMemoryTool,
    WriteMemoryTool,
    create_memory_tools,
)
from openjiuwen.harness.workspace.workspace import Workspace


@pytest_asyncio.fixture
async def ctx():
    tmp_dir = tempfile.mkdtemp()
    await Runner.start()
    card_id = "test_harness_memory"
    Runner.resource_mgr.add_sys_operation(
        SysOperationCard(
            id=card_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(),
        )
    )
    sys_op = Runner.resource_mgr.get_sys_operation(card_id)

    os.makedirs(os.path.join(tmp_dir, "memory"), exist_ok=True)
    workspace = Workspace(root_path=tmp_dir, directories=[{"name": "memory", "path": "memory"}])

    yield MemoryToolContext(workspace=workspace, sys_operation=sys_op, node_name="memory")

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def ctx_without_manager():
    return MemoryToolContext(workspace=None, sys_operation=None, node_name="memory")


@pytest.mark.asyncio
async def test_create_memory_tools_returns_5_tools(ctx):
    tools = create_memory_tools(ctx)
    assert len(tools) == 5
    names = {tool.card.name for tool in tools}
    assert names == {
        "memory_search",
        "memory_get",
        "write_memory",
        "edit_memory",
        "read_memory",
    }


@pytest.mark.asyncio
async def test_write_memory_success(ctx):
    result = await WriteMemoryTool(ctx).invoke(
        {"path": "notes/hello.md", "content": "# Hello\nbody", "append": False}
    )
    assert result.success is True, result

    memory_file = os.path.join(str(ctx.workspace.get_node_path("memory")), "hello.md")
    assert os.path.exists(memory_file)


@pytest.mark.asyncio
async def test_read_memory_success(ctx):
    await WriteMemoryTool(ctx).invoke(
        {"path": "notes/readme.md", "content": "line1\nline2\nline3", "append": False}
    )
    result = await ReadMemoryTool(ctx).invoke({"path": "notes/readme.md", "offset": 1, "limit": 2})
    assert result.success is True, result
    assert "line1" in result.data.get("content", "")


@pytest.mark.asyncio
async def test_edit_memory_success(ctx):
    await WriteMemoryTool(ctx).invoke(
        {"path": "notes/edit.md", "content": "alpha beta", "append": False}
    )
    result = await EditMemoryTool(ctx).invoke(
        {"path": "notes/edit.md", "old_text": "beta", "new_text": "gamma"}
    )
    assert result.success is True, result

    read_result = await ReadMemoryTool(ctx).invoke({"path": "notes/edit.md"})
    assert read_result.success is True, read_result
    assert "gamma" in read_result.data.get("content", "")


@pytest.mark.asyncio
async def test_validate_memory_path_invalid_traversal(ctx):
    ok, msg = validate_memory_path("../escape.md", ctx.workspace)
    assert ok is False
    assert "traversal" in msg.lower() or "invalid path" in msg.lower()


@pytest.mark.asyncio
async def test_memory_get_disabled_when_no_manager(ctx_without_manager):
    result = await MemoryGetTool(ctx_without_manager).invoke({"path": "indexed.md"})
    assert result.success is False, result
    assert result.data.get("disabled") is True


@pytest.mark.asyncio
async def test_memory_search_returns_structured_result(ctx):
    result = await MemorySearchTool(ctx).invoke({"query": "anything"})
    assert isinstance(result.data, dict)
    assert "results" in result.data or "disabled" in result.data
