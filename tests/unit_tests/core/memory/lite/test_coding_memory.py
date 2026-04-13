"""Coding Memory 单元测试 - 基础功能测试."""

import os
import tempfile
import shutil

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.core.memory.lite import coding_memory_tools
from openjiuwen.core.memory.lite.coding_memory_tools import (
    _validate_coding_memory_path,
    _upsert_memory_index,
    _remove_from_memory_index,
    _count_memory_files_async,
    _read_file_safe,
)
from openjiuwen.core.memory.lite.frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
    VALID_TYPES,
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
    async def test_validate_path_success(self, memory_test_setup):
        """测试合法路径."""
        is_valid, resolved = _validate_coding_memory_path("user_role.md")
        assert is_valid is True
        assert resolved.endswith("user_role.md")

    @pytest.mark.asyncio
    async def test_validate_path_traversal(self, memory_test_setup):
        """测试路径遍历攻击."""
        is_valid, err = _validate_coding_memory_path("../etc/passwd.md")
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_path_absolute(self, memory_test_setup):
        """测试绝对路径."""
        is_valid, err = _validate_coding_memory_path("/etc/passwd.md")
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_path_not_md(self, memory_test_setup):
        """测试非 .md 文件."""
        is_valid, err = _validate_coding_memory_path("user_role.txt")
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_set_and_get_coding_memory_dir(self, memory_test_setup):
        """测试设置和获取 coding memory 目录."""
        new_dir = tempfile.mkdtemp()
        coding_memory_tools.coding_memory_dir = new_dir
        assert coding_memory_tools.coding_memory_dir == new_dir
        shutil.rmtree(new_dir, ignore_errors=True)


class TestMemoryIndex:
    """MEMORY.md 索引管理测试."""

    @pytest.mark.asyncio
    async def test_upsert_new_entry(self, memory_test_setup):
        """测试插入新条目."""
        sys_op, coding_memory_dir = memory_test_setup
        fm = {
            "name": "Developer Role",
            "description": "Senior Python developer",
            "type": "user"
        }
        await _upsert_memory_index(coding_memory_dir, "user_role.md", fm)

        index_path = os.path.join(coding_memory_dir, "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        assert "Developer Role" in index_content
        assert "user_role.md" in index_content

    @pytest.mark.asyncio
    async def test_upsert_update_existing(self, memory_test_setup):
        """测试更新已有条目."""
        sys_op, coding_memory_dir = memory_test_setup
        fm1 = {"name": "Old Name", "description": "Old desc", "type": "user"}
        await _upsert_memory_index(coding_memory_dir, "user_role.md", fm1)

        fm2 = {"name": "New Name", "description": "New desc", "type": "user"}
        await _upsert_memory_index(coding_memory_dir, "user_role.md", fm2)

        index_path = os.path.join(coding_memory_dir, "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        assert "New Name" in index_content
        assert "Old Name" not in index_content

    @pytest.mark.asyncio
    async def test_remove_from_index(self, memory_test_setup):
        """测试从索引删除."""
        sys_op, coding_memory_dir = memory_test_setup
        fm = {"name": "To Delete", "description": "Will be deleted", "type": "user"}
        await _upsert_memory_index(coding_memory_dir, "to_delete.md", fm)

        await _remove_from_memory_index(coding_memory_dir, "to_delete.md")

        index_path = os.path.join(coding_memory_dir, "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        assert "To Delete" not in index_content

    @pytest.mark.asyncio
    async def test_count_memory_files(self, memory_test_setup):
        """测试统计记忆文件数."""
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


class TestFileHelpers:
    """文件辅助函数测试."""

    @pytest.mark.asyncio
    async def test_read_file_safe_success(self, memory_test_setup):
        """测试安全读取存在的文件."""
        sys_op, coding_memory_dir = memory_test_setup
        file_path = os.path.join(coding_memory_dir, "test.txt")
        await sys_op.fs().write_file(file_path, content="测试内容", create_if_not_exist=True)

        content = await _read_file_safe(file_path)
        assert "测试内容" in content

    @pytest.mark.asyncio
    async def test_read_file_safe_not_found(self):
        """测试安全读取不存在的文件."""
        prev = coding_memory_tools.coding_memory_sys_operation
        try:
            coding_memory_tools.coding_memory_sys_operation = None
            content = await _read_file_safe("/nonexistent/path/file.txt")
            assert content == ""
        finally:
            coding_memory_tools.coding_memory_sys_operation = prev
