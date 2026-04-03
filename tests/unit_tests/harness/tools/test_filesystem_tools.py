# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import tempfile
import shutil

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.tools.filesystem import (
    ReadFileTool, WriteFileTool, EditFileTool, GlobTool, ListDirTool, GrepTool
)


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_filesystem_tools_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3",
                         "npm", "node", "git", "cat", "type", "mkdir", "md", "rm", "rd", "cp",
                         "copy", "mv", "move", "grep", "rg", "find", "curl", "wget", "ps", "df", "ping"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_file_read_write(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)

    file_path = os.path.join(temp_dir, "test.txt")
    content = "第一行\n第二行\n第三行"

    write_res = await write_tool.invoke({"file_path": file_path, "content": content})
    assert write_res.success is True
    assert write_res.data["bytes_written"] > 0
    assert os.path.exists(file_path)

    read_res = await read_tool.invoke({"file_path": file_path})
    assert read_res.success is True
    assert read_res.data["content"] == content
    assert read_res.data["line_count"] == 3

    read_partial = await read_tool.invoke({"file_path": file_path, "offset": 2, "limit": 1})
    assert read_partial.success is True
    assert "第二行" in read_partial.data["content"]


@pytest.mark.asyncio
async def test_edit_file(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "edit.txt")
    content = "Hello Google DeepMind Antigravity"
    await write_tool.invoke({"file_path": file_path, "content": content})

    edit_res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "o",
        "new_string": "0",
        "replace_all": False
    })
    assert edit_res.success is True
    assert edit_res.data["replacements"] == 1

    read_res = await sys_op.fs().read_file(file_path)
    assert "Hell0 Google" in read_res.data.content


@pytest.mark.asyncio
async def test_glob_and_ls(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    glob_tool = GlobTool(sys_op)
    ls_tool = ListDirTool(sys_op)

    os.makedirs(os.path.join(temp_dir, "subdir"), exist_ok=True)
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "a.py"), "content": "1"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "subdir", "b.py"), "content": "2"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "c.txt"), "content": "3"})

    glob_res = await glob_tool.invoke({"pattern": "**/*.py", "path": temp_dir})
    assert glob_res.success is True
    assert glob_res.data["count"] == 2

    ls_res = await ls_tool.invoke({"path": temp_dir, "show_hidden": False})
    assert ls_res.success is True
    assert "subdir" in ls_res.data["dirs"]
    assert "a.py" in ls_res.data["files"]


@pytest.mark.asyncio
async def test_grep_tool(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)
    file_path = os.path.join(temp_dir, "grep_test.txt")
    content = "Target Line 1\nOther Line\nTarget Line 2\n"
    await write_tool.invoke({"file_path": file_path, "content": content})

    grep_res = await grep_tool.invoke({
        "pattern": "Target",
        "path": temp_dir,
        "ignore_case": False
    })
    assert grep_res.success is True
    assert grep_res.data["count"] == 2
    assert "Other Line" not in grep_res.data["stdout"]
