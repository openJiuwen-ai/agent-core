"""Unit tests for harness coding memory tools."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.tools.coding_memory import (
    CodingMemoryEditTool,
    CodingMemoryReadTool,
    CodingMemoryWriteTool,
    create_coding_memory_tools,
)
from openjiuwen.harness.workspace.workspace import Workspace


@pytest_asyncio.fixture
async def ctx():
    tmp_dir = tempfile.mkdtemp()
    await Runner.start()
    card_id = "test_harness_coding_memory"
    Runner.resource_mgr.add_sys_operation(
        SysOperationCard(
            id=card_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(),
        )
    )
    sys_op = Runner.resource_mgr.get_sys_operation(card_id)

    os.makedirs(os.path.join(tmp_dir, "coding_memory"), exist_ok=True)
    workspace = Workspace(root_path=tmp_dir, directories=[{"name": "coding_memory", "path": "coding_memory"}])
    yield CodingMemoryToolContext(workspace=workspace, sys_operation=sys_op, node_name="coding_memory")

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_create_coding_memory_tools_returns_3_tools_and_fills_context(ctx):
    assert ctx.settings is None
    ctx.coding_memory_dir = ""
    tools = create_coding_memory_tools(ctx)
    assert len(tools) == 3
    assert {tool.card.name for tool in tools} == {
        "coding_memory_read",
        "coding_memory_write",
        "coding_memory_edit",
    }
    assert ctx.node_name == "coding_memory"
    assert ctx.settings is not None
    assert ctx.coding_memory_dir == str(ctx.workspace.get_node_path("coding_memory"))


@pytest.mark.asyncio
async def test_coding_memory_write_requires_frontmatter(ctx):
    result = await CodingMemoryWriteTool(ctx).invoke({"path": "notes.md", "content": "plain text"})
    assert result.success is False
    assert "frontmatter" in (result.error or result.data.get("error", "")).lower()


@pytest.mark.asyncio
async def test_coding_memory_write_read_edit_success(ctx):
    content = """---
name: user_pref
description: user preference for tests
type: user
---
always run targeted tests first
"""
    write_result = await CodingMemoryWriteTool(ctx).invoke({"path": "notes/user_pref.md", "content": content})
    assert write_result.success is True, write_result

    memory_file = os.path.join(str(ctx.workspace.get_node_path("coding_memory")), "user_pref.md")
    assert os.path.exists(memory_file)

    read_result = await CodingMemoryReadTool(ctx).invoke({"path": "notes/user_pref.md"})
    assert read_result.success is True, read_result
    assert "always run targeted tests first" in read_result.data.get("content", "")

    edit_result = await CodingMemoryEditTool(ctx).invoke(
        {
            "path": "notes/user_pref.md",
            "old_text": "always run targeted tests first",
            "new_text": "always run focused unit tests first",
        }
    )
    assert edit_result.success is True, edit_result

    verify_result = await CodingMemoryReadTool(ctx).invoke({"path": "notes/user_pref.md"})
    assert verify_result.success is True, verify_result
    assert "always run focused unit tests first" in verify_result.data.get("content", "")
