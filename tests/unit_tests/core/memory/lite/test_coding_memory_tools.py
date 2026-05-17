# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os

import pytest

from openjiuwen.core.memory.lite.coding_memory_tool_ops import (
    _read_file_safe,
    _upsert_memory_index,
    coding_memory_edit_with_context,
    coding_memory_read_with_context,
    coding_memory_write_with_context,
    validate_coding_memory_path,
)
from openjiuwen.core.memory.lite.frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
)


@pytest.mark.asyncio
async def test_coding_memory_write_success(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test_memory
description: 测试记忆文件
type: project
---
这是记忆内容"""
    result = await coding_memory_write_with_context(ctx, "test.md", content)
    assert result["success"] is True, f"Expected success=True, got: {result}"
    assert result["type"] == "project"


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_frontmatter(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = "这是没有 frontmatter 的内容"
    result = await coding_memory_write_with_context(ctx, "test_invalid_fm.md", content)
    assert result["success"] is False
    assert "frontmatter" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_path(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test
description: test
type: project
---
content"""
    result = await coding_memory_write_with_context(ctx, "test.txt", content)
    assert result["success"] is False
    assert ".md" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_write_type_user(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: user_memory
description: user类型记忆
type: user
---
用户内容"""
    result = await coding_memory_write_with_context(ctx, "user_test.md", content)
    assert result["success"] is True
    assert result["type"] == "user"


@pytest.mark.asyncio
async def test_coding_memory_write_type_feedback(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: feedback_memory
description: feedback类型记忆
type: feedback
---
反馈内容"""
    result = await coding_memory_write_with_context(ctx, "feedback_test.md", content)
    assert result["success"] is True
    assert result["type"] == "feedback"


@pytest.mark.asyncio
async def test_coding_memory_write_type_reference(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: reference_memory
description: reference类型记忆
type: reference
---
参考内容"""
    result = await coding_memory_write_with_context(ctx, "reference_test.md", content)
    assert result["success"] is True
    assert result["type"] == "reference"


@pytest.mark.asyncio
async def test_coding_memory_write_invalid_type(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: invalid_type
description: 无效类型
type: knowledge
---
无效类型内容"""
    result = await coding_memory_write_with_context(ctx, "invalid.md", content)
    assert result["success"] is False
    assert "type must be one of" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_read_full_content(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test_read
description: 测试读取功能
type: project
---
这是测试内容"""
    await coding_memory_write_with_context(ctx, "read_test.md", content)

    result = await coding_memory_read_with_context(ctx, "read_test.md")
    assert result["success"] is True
    assert "这是测试内容" in result["content"]
    assert result["totalLines"] > 0


@pytest.mark.asyncio
async def test_coding_memory_read_with_offset_limit(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

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
    write_result = await coding_memory_write_with_context(ctx, "offset_test.md", content)
    assert write_result["success"] is True, f"Write failed: {write_result}"

    read_result = await coding_memory_read_with_context(ctx, "offset_test.md", offset=3, limit=2)
    assert read_result["success"] is True


@pytest.mark.asyncio
async def test_coding_memory_read_nonexistent(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    result = await coding_memory_read_with_context(ctx, "nonexistent.md")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_coding_memory_edit_success(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test_edit
description: 测试编辑
type: project
---
原内容"""
    await coding_memory_write_with_context(ctx, "edit_test.md", content)

    result = await coding_memory_edit_with_context(ctx, "edit_test.md", "原内容", "修改后内容")
    assert result["success"] is True

    read_result = await coding_memory_read_with_context(ctx, "edit_test.md")
    assert "修改后内容" in read_result["content"]


@pytest.mark.asyncio
async def test_coding_memory_edit_old_text_not_found(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test_not_found
description: 测试找不到
type: project
---
实际内容"""
    await coding_memory_write_with_context(ctx, "not_found_test.md", content)

    result = await coding_memory_edit_with_context(ctx, "not_found_test.md", "不存在的文本", "新文本")
    assert result["success"] is False
    assert "old_text not found" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_edit_multiple_matches(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    content = """---
name: test_multi
description: 测试多次出现
type: project
---
相同文本
相同文本"""
    await coding_memory_write_with_context(ctx, "multi_test.md", content)

    result = await coding_memory_edit_with_context(ctx, "multi_test.md", "相同文本", "替换文本")
    assert result["success"] is False
    assert "appears" in result["error"]


@pytest.mark.asyncio
async def test_coding_memory_edit_empty_old_text(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    result = await coding_memory_edit_with_context(ctx, "test.md", "", "new")
    assert result["success"] is False
    assert "old_text cannot be empty" in result["error"]


@pytest.mark.asyncio
async def test_upsert_memory_index(coding_memory_ctx):
    ctx, _sys_op, coding_memory_dir = coding_memory_ctx

    await _upsert_memory_index(ctx, coding_memory_dir, "test.md", {"name": "test", "description": "测试"})

    index_path = os.path.join(coding_memory_dir, "MEMORY.md")
    index_content = await _read_file_safe(ctx, index_path)
    assert "test.md" in index_content


@pytest.mark.asyncio
async def test_validate_coding_memory_path_valid(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    is_valid, resolved = validate_coding_memory_path("valid.md", ctx.workspace)
    assert is_valid is True
    assert resolved.endswith("valid.md")


@pytest.mark.asyncio
async def test_validate_coding_memory_path_invalid_ext(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    is_valid, resolved = validate_coding_memory_path("invalid.txt", ctx.workspace)
    assert is_valid is False
    assert ".md" in resolved


@pytest.mark.asyncio
async def test_validate_coding_memory_path_traversal(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    is_valid, resolved = validate_coding_memory_path("../escape.md", ctx.workspace)
    assert is_valid is False
    assert "traversal" in resolved


@pytest.mark.asyncio
async def test_validate_coding_memory_path_absolute(coding_memory_ctx):
    ctx, _sys_op, _cm_dir = coding_memory_ctx

    is_valid, resolved = validate_coding_memory_path("/absolute.md", ctx.workspace)
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
