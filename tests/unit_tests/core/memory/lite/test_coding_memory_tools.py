# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import tempfile
import shutil

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode
from openjiuwen.core.memory.lite import coding_memory_tools
from openjiuwen.core.memory.lite.coding_memory_tools import (
    coding_memory_read,
    coding_memory_write,
    coding_memory_edit,
    _read_file_safe,
    _read_head_async,
    _count_memory_files_async,
    _upsert_memory_index,
    _remove_from_memory_index,
    _validate_coding_memory_path,
)
from openjiuwen.core.memory.lite.frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
)


@pytest.fixture(autouse=True)
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest_asyncio.fixture(autouse=True)
async def memory_test_setup(temp_dir):
    temp_dir_value = temp_dir
    await Runner.start()
    card_id = "test_coding_memory_setup"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo", "ls", "dir", "cd", "pwd", "python", "python3", "cat", "mkdir"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    sys_op = Runner.resource_mgr.get_sys_operation(card_id)

    coding_memory_dir = os.path.join(temp_dir_value, "coding_memory")
    os.makedirs(coding_memory_dir, exist_ok=True)

    workspace = Workspace(
        root_path=temp_dir_value,
        directories=[
            {"name": "coding_memory", "path": "coding_memory"}
        ]
    )

    coding_memory_tools.coding_memory_workspace = workspace
    coding_memory_tools.coding_memory_sys_operation = sys_op
    coding_memory_tools.coding_memory_dir = coding_memory_dir

    yield sys_op, coding_memory_dir

    coding_memory_tools.coding_memory_workspace = None
    coding_memory_tools.coding_memory_sys_operation = None
    coding_memory_tools.coding_memory_dir = "coding_memory"
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_coding_memory_write_success(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup
    print(f"DEBUG: coding_memory_dir = {coding_memory_dir}")

    content = """---
name: test_memory
description: 测试记忆文件
type: project
---
这是记忆内容"""
    result = await coding_memory_write.invoke({"path": "test.md", "content": content})
    print(f"DEBUG: Write result = {result}")
    print(f"DEBUG: fullPath = {result.get('fullPath')}")
    assert result["success"] is True, f"Expected success=True, got: {result}"
    assert result["type"] == "project"


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_frontmatter(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = "这是没有 frontmatter 的内容"
    result = await coding_memory_write.invoke({"path": "test_invalid_fm.md", "content": content})
    assert result["success"] is False
    assert "frontmatter" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_path(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test
description: test
type: project
---
content"""
    result = await coding_memory_write.invoke({"path": "test.txt", "content": content})
    assert result["success"] is False
    assert ".md" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_write_type_user(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: user_memory
description: user类型记忆
type: user
---
用户内容"""
    result = await coding_memory_write.invoke({"path": "user_test.md", "content": content})
    assert result["success"] is True
    assert result["type"] == "user"


@pytest.mark.asyncio
async def test_coding_memory_write_type_feedback(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: feedback_memory
description: feedback类型记忆
type: feedback
---
反馈内容"""
    result = await coding_memory_write.invoke({"path": "feedback_test.md", "content": content})
    assert result["success"] is True
    assert result["type"] == "feedback"


@pytest.mark.asyncio
async def test_coding_memory_write_type_reference(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: reference_memory
description: reference类型记忆
type: reference
---
参考内容"""
    result = await coding_memory_write.invoke({"path": "reference_test.md", "content": content})
    assert result["success"] is True
    assert result["type"] == "reference"


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_type(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: invalid_type
description: 无效类型
type: knowledge
---
无效类型内容"""
    result = await coding_memory_write.invoke({"path": "invalid.md", "content": content})
    assert result["success"] is False
    assert "type must be one of" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_read_full_content(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test_read
description: 测试读取功能
type: project
---
这是测试内容"""
    await coding_memory_write.invoke({"path": "read_test.md", "content": content})

    result = await coding_memory_read.invoke({"path": "read_test.md"})
    assert result["success"] is True
    assert "这是测试内容" in result["content"]
    assert result["totalLines"] > 0


@pytest.mark.asyncio
async def test_coding_memory_read_with_offset_limit(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test_offset
description: 测试偏移
type: project
---
第一行
第二行
第三行
第四行
第五行"""
    write_result = await coding_memory_write.invoke({"path": "offset_test.md", "content": content})
    print(f"Write result: {write_result}")
    assert write_result["success"] is True, f"Write failed: {write_result}"

    read_result = await coding_memory_read.invoke({"path": "offset_test.md", "offset": 3, "limit": 2})
    print(f"Read result: {read_result}")
    print(f"Content: '{read_result.get('content')}'")
    print(f"totalLines: {read_result.get('totalLines')}")
    print(f"start_line: {read_result.get('start_line')}")
    print(f"end_line: {read_result.get('end_line')}")
    assert read_result["success"] is True


@pytest.mark.asyncio
async def test_coding_memory_read_nonexistent(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    result = await coding_memory_read.invoke({"path": "nonexistent.md"})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_coding_memory_edit_success(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test_edit
description: 测试编辑
type: project
---
原内容"""
    await coding_memory_write.invoke({"path": "edit_test.md", "content": content})

    result = await coding_memory_edit.invoke({
        "path": "edit_test.md",
        "old_text": "原内容",
        "new_text": "修改后内容"
    })
    assert result["success"] is True

    read_result = await coding_memory_read.invoke({"path": "edit_test.md"})
    assert "修改后内容" in read_result["content"]


@pytest.mark.asyncio
async def test_coding_memory_edit_old_text_not_found(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test_not_found
description: 测试找不到
type: project
---
实际内容"""
    await coding_memory_write.invoke({"path": "not_found_test.md", "content": content})

    result = await coding_memory_edit.invoke({
        "path": "not_found_test.md",
        "old_text": "不存在的文本",
        "new_text": "新文本"
    })
    assert result["success"] is False
    assert "old_text not found" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_edit_multiple_matches(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    content = """---
name: test_multi
description: 测试多次出现
type: project
---
相同文本
相同文本"""
    await coding_memory_write.invoke({"path": "multi_test.md", "content": content})

    result = await coding_memory_edit.invoke({
        "path": "multi_test.md",
        "old_text": "相同文本",
        "new_text": "替换文本"
    })
    assert result["success"] is False
    assert "appears" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_edit_empty_old_text(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    result = await coding_memory_edit.invoke({
        "path": "test.md",
        "old_text": "",
        "new_text": "new"
    })
    assert result["success"] is False
    assert "old_text cannot be empty" in result["error"]


@pytest.mark.asyncio
async def test_upsert_memory_index(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    print(f"DEBUG: before upsert, coding_memory_dir = {coding_memory_dir}")
    await _upsert_memory_index(coding_memory_dir, "test.md", {"name": "test", "description": "测试"})

    index_path = os.path.join(coding_memory_dir, "MEMORY.md")
    print(f"DEBUG: index_path = {index_path}")
    index_content = await _read_file_safe(index_path)
    print(f"DEBUG: index_content = '{index_content}'")
    assert "test.md" in index_content


@pytest.mark.asyncio
async def test_remove_from_memory_index(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    await _upsert_memory_index(coding_memory_dir, "to_remove.md", {"name": "remove", "description": "删除"})
    await _remove_from_memory_index(coding_memory_dir, "to_remove.md")

    index_path = os.path.join(coding_memory_dir, "MEMORY.md")
    index_content = await _read_file_safe(index_path)
    assert "to_remove.md" not in index_content


@pytest.mark.asyncio
async def test_read_head_async(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    file_path = os.path.join(coding_memory_dir, "head_test.md")
    content = """---
name: head_test
description: 测试读取头部
type: project
---
第一行
第二行
第三行
第四行
第五行"""
    await sys_op.fs().write_file(file_path, content=content, create_if_not_exist=True)

    result = await _read_head_async(file_path, max_lines=3)
    assert "head_test" in result


@pytest.mark.asyncio
async def test_count_memory_files_async(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    await sys_op.fs().write_file(
        os.path.join(coding_memory_dir, "file1.md"),
        content="---\nname: f1\ntype: project\n---\ncontent",
        create_if_not_exist=True
    )
    await sys_op.fs().write_file(
        os.path.join(coding_memory_dir, "file2.md"),
        content="---\nname: f2\ntype: user\n---\ncontent",
        create_if_not_exist=True
    )
    await sys_op.fs().write_file(
        os.path.join(coding_memory_dir, "MEMORY.md"),
        content="index",
        create_if_not_exist=True
    )

    count = await _count_memory_files_async(coding_memory_dir)
    assert count == 2


@pytest.mark.asyncio
async def test_validate_coding_memory_path_valid(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    is_valid, resolved = _validate_coding_memory_path("valid.md")
    assert is_valid is True
    assert resolved.endswith("valid.md")


@pytest.mark.asyncio
async def test_validate_coding_memory_path_invalid_ext(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    is_valid, resolved = _validate_coding_memory_path("invalid.txt")
    assert is_valid is False
    assert ".md" in resolved


@pytest.mark.asyncio
async def test_validate_coding_memory_path_traversal(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    is_valid, resolved = _validate_coding_memory_path("../escape.md")
    assert is_valid is False
    assert "traversal" in resolved


@pytest.mark.asyncio
async def test_validate_coding_memory_path_absolute(memory_test_setup):
    sys_op, coding_memory_dir = memory_test_setup

    is_valid, resolved = _validate_coding_memory_path("/absolute.md")
    assert is_valid is False


@pytest.mark.asyncio
async def test_parse_frontmatter_valid():
    content = """---
name: test_memory
description: 测试记忆
type: project
---
这是内容"""
    result = parse_frontmatter(content)
    assert result is not None
    assert result["name"] == "test_memory"
    assert result["description"] == "测试记忆"
    assert result["type"] == "project"


@pytest.mark.asyncio
async def test_parse_frontmatter_no_frontmatter():
    content = "这是没有frontmatter的内容"
    result = parse_frontmatter(content)
    assert result is None


@pytest.mark.asyncio
async def test_parse_frontmatter_incomplete():
    content = """---
name: test
---
这是内容"""
    result = parse_frontmatter(content)
    assert result is not None
    assert result["name"] == "test"


@pytest.mark.asyncio
async def test_validate_frontmatter_valid():
    fm = {"name": "test", "description": "测试", "type": "project"}
    is_valid, error = validate_frontmatter(fm)
    assert is_valid is True
    assert error == ""


@pytest.mark.asyncio
async def test_validate_frontmatter_missing_name():
    fm = {"description": "测试", "type": "project"}
    is_valid, error = validate_frontmatter(fm)
    assert is_valid is False
    assert "name" in error


@pytest.mark.asyncio
async def test_validate_frontmatter_missing_description():
    fm = {"name": "test", "type": "project"}
    is_valid, error = validate_frontmatter(fm)
    assert is_valid is False
    assert "description" in error


@pytest.mark.asyncio
async def test_validate_frontmatter_missing_type():
    fm = {"name": "test", "description": "测试"}
    is_valid, error = validate_frontmatter(fm)
    assert is_valid is False
    assert "type" in error


@pytest.mark.asyncio
async def test_validate_frontmatter_invalid_type():
    fm = {"name": "test", "description": "测试", "type": "invalid"}
    is_valid, error = validate_frontmatter(fm)
    assert is_valid is False
    assert "type must be one of" in error


@pytest.mark.asyncio
async def test_validate_frontmatter_all_valid_types():
    for mem_type in ["user", "feedback", "project", "reference"]:
        fm = {"name": "test", "description": "测试", "type": mem_type}
        is_valid, error = validate_frontmatter(fm)
        assert is_valid is True, f"type {mem_type} should be valid"