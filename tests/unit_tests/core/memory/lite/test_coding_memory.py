"""Unit tests for Coding Memory — basic functionality."""

import os

import pytest

from openjiuwen.core.memory.lite.coding_memory_tool_ops import (
    _read_file_safe,
    _upsert_memory_index,
    validate_coding_memory_path,
)
from openjiuwen.core.memory.lite.frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
    VALID_TYPES,
)


class TestFrontmatter:
    """Frontmatter 解析和校验测试."""

    def test_parse_frontmatter_success(self):
        """测试正常解析 frontmatter."""
        content = """---
name: Developer Role
description: Senior Python developer
type: user
---

用户是高级 Python 开发者."""
        result = parse_frontmatter(content)
        assert result is not None
        assert result["name"] == "Developer Role"
        assert result["description"] == "Senior Python developer"
        assert result["type"] == "user"

    def test_parse_frontmatter_no_frontmatter(self):
        """测试无 frontmatter 的情况."""
        content = "纯文本内容，没有 frontmatter"
        result = parse_frontmatter(content)
        assert result is None

    def test_validate_frontmatter_success(self):
        """测试校验通过."""
        fm = {
            "name": "Test Memory",
            "description": "A test memory",
            "type": "user"
        }
        valid, err = validate_frontmatter(fm)
        assert valid is True
        assert err == ""

    def test_validate_frontmatter_missing_field(self):
        """测试缺少必填字段."""
        fm = {
            "name": "Test Memory",
            "type": "user"
        }
        valid, err = validate_frontmatter(fm)
        assert valid is False
        assert "description" in err

    def test_validate_frontmatter_invalid_type(self):
        """测试无效的 type."""
        fm = {
            "name": "Test Memory",
            "description": "A test memory",
            "type": "invalid_type"
        }
        valid, err = validate_frontmatter(fm)
        assert valid is False
        assert "type" in err

    def test_valid_types_constant(self):
        """测试 VALID_TYPES 包含所有 4 种类型."""
        assert "user" in VALID_TYPES
        assert "feedback" in VALID_TYPES
        assert "project" in VALID_TYPES
        assert "reference" in VALID_TYPES
        assert len(VALID_TYPES) == 4


class TestPathValidation:
    """路径校验测试."""

    @pytest.mark.asyncio
    async def test_validate_path_success(self, coding_memory_ctx):
        """测试合法路径."""
        ctx, _so, _d = coding_memory_ctx
        is_valid, resolved = validate_coding_memory_path("user_role.md", ctx.workspace)
        assert is_valid is True
        assert resolved.endswith("user_role.md")

    @pytest.mark.asyncio
    async def test_validate_path_traversal(self, coding_memory_ctx):
        """测试路径遍历攻击."""
        ctx, _so, _d = coding_memory_ctx
        is_valid, _err = validate_coding_memory_path("../etc/passwd.md", ctx.workspace)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_path_absolute(self, coding_memory_ctx):
        """测试绝对路径."""
        ctx, _so, _d = coding_memory_ctx
        is_valid, _err = validate_coding_memory_path("/etc/passwd.md", ctx.workspace)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_path_not_md(self, coding_memory_ctx):
        """测试非 .md 文件."""
        ctx, _so, _d = coding_memory_ctx
        is_valid, _err = validate_coding_memory_path("user_role.txt", ctx.workspace)
        assert is_valid is False


class TestMemoryIndex:
    """MEMORY.md 索引管理测试."""

    @pytest.mark.asyncio
    async def test_upsert_new_entry(self, coding_memory_ctx):
        """测试插入新条目."""
        ctx, _sys_op, coding_memory_dir = coding_memory_ctx
        fm = {
            "name": "Developer Role",
            "description": "Senior Python developer",
            "type": "user"
        }
        await _upsert_memory_index(ctx, coding_memory_dir, "user_role.md", fm)

        index_path = os.path.join(coding_memory_dir, "MEMORY.md")
        index_content = await _read_file_safe(ctx, index_path)
        assert "Developer Role" in index_content
        assert "user_role.md" in index_content

    @pytest.mark.asyncio
    async def test_upsert_update_existing(self, coding_memory_ctx):
        """测试更新已有条目."""
        ctx, _sys_op, coding_memory_dir = coding_memory_ctx
        fm1 = {"name": "Old Name", "description": "Old desc", "type": "user"}
        await _upsert_memory_index(ctx, coding_memory_dir, "user_role.md", fm1)

        fm2 = {"name": "New Name", "description": "New desc", "type": "user"}
        await _upsert_memory_index(ctx, coding_memory_dir, "user_role.md", fm2)

        index_path = os.path.join(coding_memory_dir, "MEMORY.md")
        index_content = await _read_file_safe(ctx, index_path)
        assert "New Name" in index_content
        assert "Old Name" not in index_content


class TestFileHelpers:
    """文件辅助函数测试."""

    @pytest.mark.asyncio
    async def test_read_file_safe_success(self, coding_memory_ctx):
        """测试安全读取存在的文件."""
        ctx, sys_op, coding_memory_dir = coding_memory_ctx
        file_path = os.path.join(coding_memory_dir, "test.txt")
        await sys_op.fs().write_file(file_path, content="测试内容", create_if_not_exist=True)

        content = await _read_file_safe(ctx, file_path)
        assert "测试内容" in content
