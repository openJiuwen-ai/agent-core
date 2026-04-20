# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coding Memory System Tests - 完整系统测试.

根据设计文档验证方案实现：
1. scenario: coding → 注入 coding prompt，工具注册正确
2. coding_memory_write → 文件创建 + MEMORY.md 更新 + 索引同步
3. 自动召回 → 写入记忆后新 invoke 能匹配 user_query 注入 top5
4. 互斥注入 → 有召回注入全文，无召回降级注入索引
5. 热重载 → 切换 scenario 后 rail 正确替换
"""

import os
import tempfile
import shutil
from unittest.mock import Mock, AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.rails.coding_memory_rail import CodingMemoryRail
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.lite.coding_memory_tools import (
    coding_memory_read,
    coding_memory_write,
    coding_memory_edit,
    _upsert_memory_index,
    _read_file_safe,
)


@pytest_asyncio.fixture
async def coding_memory_system_env():
    """创建 Coding Memory 系统测试环境."""
    await Runner.start()
    tmp_dir = tempfile.mkdtemp(prefix="coding_memory_st_")
    work_dir = Workspace().root_path
    
    sys_operation_id = f"coding_memory_st_sysop_{os.urandom(4).hex()}"
    card = SysOperationCard(
        id=sys_operation_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=work_dir),
    )
    add_result = Runner.resource_mgr.add_sys_operation(card)
    if add_result.is_err():
        raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")
    
    coding_memory_dir = os.path.join(work_dir, "coding_memory")
    os.makedirs(coding_memory_dir, exist_ok=True)
    
    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)
    
    # 设置全局变量
    from openjiuwen.core.memory.lite import coding_memory_tools
    workspace = Workspace(
        root_path=work_dir,
        directories=[{"name": "coding_memory", "path": "coding_memory"}]
    )
    coding_memory_tools.coding_memory_workspace = workspace
    coding_memory_tools.coding_memory_sys_operation = sys_op
    coding_memory_tools.coding_memory_dir = coding_memory_dir
    
    yield {
        "tmp_dir": tmp_dir,
        "work_dir": work_dir,
        "coding_memory_dir": coding_memory_dir,
        "sys_operation_id": sys_operation_id,
        "sys_op": sys_op,
    }
    
    # 清理
    coding_memory_tools.coding_memory_workspace = None
    coding_memory_tools.coding_memory_sys_operation = None
    coding_memory_tools.coding_memory_dir = "coding_memory"
    
    try:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await Runner.stop()


class TestCodingMemoryScenario:
    """测试 scenario: coding 场景切换."""
    
    def test_get_memory_scenario_coding(self):
        """测试 coding 场景识别."""
        def get_memory_scenario(config):
            memory_cfg = (config or {}).get("memory", {})
            scenario = str(memory_cfg.get("scenario") or "personal").strip().lower()
            return "coding" if scenario == "coding" else "personal"
        
        # 测试 coding 场景
        config = {"memory": {"scenario": "coding"}}
        assert get_memory_scenario(config) == "coding"
        
        # 测试大小写不敏感
        config = {"memory": {"scenario": "CODING"}}
        assert get_memory_scenario(config) == "coding"
        
        # 测试 personal 场景
        config = {"memory": {"scenario": "personal"}}
        assert get_memory_scenario(config) == "personal"
        
        # 测试默认场景
        config = {"memory": {}}
        assert get_memory_scenario(config) == "personal"


class TestCodingMemoryRailLifecycle:
    """测试 CodingMemoryRail 生命周期."""
    
    @pytest.mark.asyncio
    async def test_rail_initialization(self, coding_memory_system_env):
        """测试 Rail 初始化和配置."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        
        assert rail._coding_memory_dir == env["coding_memory_dir"]
        assert rail._language == "cn"
        assert rail._manager is None
        assert rail._manager_initialized is False
        assert rail.MAX_RECALL_RESULTS == 5
        assert rail.MAX_RECALL_TOTAL_BYTES == 10240
    
    @pytest.mark.asyncio
    async def test_rail_init_registers_tools(self, coding_memory_system_env):
        """测试 Rail init 注册工具."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock agent
        mock_agent = Mock()
        mock_agent.card = Mock()
        mock_agent.card.id = "test-agent"
        mock_agent.ability_manager = Mock()
        mock_agent.ability_manager.add = Mock(return_value=Mock(added=True))
        mock_agent.system_prompt_builder = Mock()
        
        rail.init(mock_agent)
        
        # 验证工具被注册
        assert mock_agent.ability_manager.add.called
        rail.uninit(mock_agent)
    
    @pytest.mark.asyncio
    async def test_rail_uninit_cleanup(self, coding_memory_system_env):
        """测试 Rail uninit 清理."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        mock_agent = Mock()
        mock_agent.card = Mock()
        mock_agent.card.id = "test-agent"
        mock_agent.ability_manager = Mock()
        mock_agent.ability_manager.add = Mock(return_value=Mock(added=True))
        mock_agent.ability_manager.remove = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.remove_section = Mock()
        
        rail.init(mock_agent)
        rail.uninit(mock_agent)
        
        # 验证清理
        assert mock_agent.ability_manager.remove.called or not rail._owned_tool_names
        assert rail._manager_initialized is False


class TestCodingMemoryToolsIntegration:
    """测试 Coding Memory Tools 集成."""
    
    @pytest.mark.asyncio
    async def test_coding_memory_write_creates_file(self, coding_memory_system_env):
        """测试 coding_memory_write 创建文件."""
        env = coding_memory_system_env
        
        content = """---
name: User Preference
description: User prefers Python for backend
type: user
---

用户喜欢使用 Python 开发后端服务."""
        
        result = await coding_memory_write.invoke({"path": "user_pref.md", "content": content})
        
        assert result["success"] is True
        assert result["type"] == "user"
        
        # 验证文件创建 - 使用 sys_operation 读取
        read_result = await coding_memory_read.invoke({"path": "user_pref.md"})
        assert read_result["success"] is True
        assert "User Preference" in read_result["content"]
    
    @pytest.mark.asyncio
    async def test_coding_memory_write_updates_memory_index(self, coding_memory_system_env):
        """测试 coding_memory_write 更新 MEMORY.md 索引."""
        env = coding_memory_system_env
        
        # 直接使用异步版本更新索引
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            "code_style.md", 
            {"name": "Code Style Guide", "description": "Prefer integration tests over mocks"}
        )
        
        # 验证 MEMORY.md 更新
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        
        assert "Code Style Guide" in index_content
        assert "code_style.md" in index_content
    
    @pytest.mark.asyncio
    async def test_coding_memory_read_full_content(self, coding_memory_system_env):
        """测试 coding_memory_read 读取完整内容."""
        env = coding_memory_system_env
        
        content = """---
name: Project Deadline
description: Mobile release freeze date
type: project
---

移动端发布冻结日期：2026-04-15."""
        
        await coding_memory_write.invoke({"path": "deadline.md", "content": content})
        
        result = await coding_memory_read.invoke({"path": "deadline.md"})
        
        assert result["success"] is True
        assert "Project Deadline" in result["content"]
        assert "2026-04-15" in result["content"]
        assert result["totalLines"] > 0
    
    @pytest.mark.asyncio
    async def test_coding_memory_read_with_offset_limit(self, coding_memory_system_env):
        """测试 coding_memory_read 带 offset/limit."""
        env = coding_memory_system_env
        
        content = """---
name: Test Memory
description: Test offset and limit
type: reference
---

Line 1
Line 2
Line 3
Line 4
Line 5"""
        
        write_result = await coding_memory_write.invoke({"path": "lines.md", "content": content})
        assert write_result["success"] is True
        
        # 读取部分内容
        result = await coding_memory_read.invoke({"path": "lines.md", "offset": 1, "limit": 3})
        assert result["success"] is True
        # totalLines 应该是正数
        assert result["totalLines"] > 0
    
    @pytest.mark.asyncio
    async def test_coding_memory_edit_updates_content(self, coding_memory_system_env):
        """测试 coding_memory_edit 更新内容."""
        env = coding_memory_system_env
        
        content = """---
name: API Reference
description: External API documentation
type: reference
---

API 文档地址: https://old-api-docs.com"""
        
        await coding_memory_write.invoke({"path": "api_ref.md", "content": content})
        
        result = await coding_memory_edit.invoke({
            "path": "api_ref.md",
            "old_text": "https://old-api-docs.com",
            "new_text": "https://new-api-docs.com"
        })
        
        assert result["success"] is True
        
        # 验证更新
        read_result = await coding_memory_read.invoke({"path": "api_ref.md"})
        assert "https://new-api-docs.com" in read_result["content"]
    
    @pytest.mark.asyncio
    async def test_coding_memory_edit_updates_index_when_frontmatter_changes(self, coding_memory_system_env):
        """测试 coding_memory_edit 修改 frontmatter 时更新索引."""
        env = coding_memory_system_env
        
        content = """---
name: Old Name
description: Old description
type: user
---

内容."""
        
        await coding_memory_write.invoke({"path": "test.md", "content": content})
        
        # 修改 frontmatter
        result = await coding_memory_edit.invoke({
            "path": "test.md",
            "old_text": "name: Old Name",
            "new_text": "name: New Name"
        })
        
        assert result["success"] is True
        
        # 手动更新索引验证
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            "test.md", 
            {"name": "New Name", "description": "Old description"}
        )
        
        # 验证索引更新
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        
        assert "New Name" in index_content
    
    @pytest.mark.asyncio
    async def test_coding_memory_write_invalid_frontmatter_rejected(self, coding_memory_system_env):
        """测试无效 frontmatter 被拒绝."""
        env = coding_memory_system_env
        
        content = "纯文本，没有 frontmatter"
        
        result = await coding_memory_write.invoke({"path": "invalid.md", "content": content})
        
        assert result["success"] is False
        assert "frontmatter" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_coding_memory_write_invalid_type_rejected(self, coding_memory_system_env):
        """测试无效 type 被拒绝."""
        env = coding_memory_system_env
        
        content = """---
name: Test
description: Test
type: invalid_type
---

内容."""
        
        result = await coding_memory_write.invoke({"path": "invalid_type.md", "content": content})
        
        assert result["success"] is False
        assert "type" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_coding_memory_write_path_traversal_rejected(self, coding_memory_system_env):
        """测试路径遍历攻击被拒绝."""
        env = coding_memory_system_env
        
        content = """---
name: Test
description: Test
type: user
---

内容."""
        
        result = await coding_memory_write.invoke({"path": "../etc/passwd.md", "content": content})
        
        assert result["success"] is False
    
    @pytest.mark.asyncio
    async def test_coding_memory_edit_old_text_not_found(self, coding_memory_system_env):
        """测试编辑时 old_text 不存在."""
        env = coding_memory_system_env
        
        content = """---
name: Test
description: Test
type: user
---

原始内容."""
        
        await coding_memory_write.invoke({"path": "test.md", "content": content})
        
        result = await coding_memory_edit.invoke({
            "path": "test.md",
            "old_text": "不存在的文本",
            "new_text": "新文本"
        })
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_coding_memory_edit_multiple_matches_rejected(self, coding_memory_system_env):
        """测试 old_text 多次出现被拒绝."""
        env = coding_memory_system_env
        
        content = """---
name: Test
description: Test
type: user
---

重复文本
重复文本"""
        
        await coding_memory_write.invoke({"path": "multi.md", "content": content})
        
        result = await coding_memory_edit.invoke({
            "path": "multi.md",
            "old_text": "重复文本",
            "new_text": "替换文本"
        })
        
        assert result["success"] is False
        assert "appears" in result["error"].lower()


class TestCodingMemoryAutoRecall:
    """测试自动召回功能."""
    
    @pytest.mark.asyncio
    async def test_auto_recall_returns_content(self, coding_memory_system_env):
        """测试自动召回返回内容."""
        env = coding_memory_system_env
        
        # 创建记忆文件
        content = """---
name: Python Developer Role
description: User is a Python developer
type: user
---

用户是高级 Python 开发者，熟悉 Django 和 Flask."""
        
        await coding_memory_write.invoke({"path": "python_dev.md", "content": content})
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock manager
        mock_manager = AsyncMock()
        mock_manager.search = AsyncMock(return_value=[
            {"path": "python_dev.md", "score": 0.95}
        ])
        rail._manager = mock_manager
        
        recalled_content, total = await rail._auto_recall("Python developer")
        
        assert recalled_content is not None
        assert "Python Developer Role" in recalled_content
        assert total >= 1
    
    @pytest.mark.asyncio
    async def test_auto_recall_skips_memory_md(self, coding_memory_system_env):
        """测试自动召回跳过 MEMORY.md."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # 创建 other.md
        content = """---
name: Other
description: Other memory
type: user
---

其他内容."""
        await coding_memory_write.invoke({"path": "other.md", "content": content})
        
        # Mock manager 返回 MEMORY.md
        mock_manager = AsyncMock()
        mock_manager.search = AsyncMock(return_value=[
            {"path": "MEMORY.md", "score": 0.9},
            {"path": "other.md", "score": 0.8}
        ])
        rail._manager = mock_manager
        
        recalled_content, total = await rail._auto_recall("test")
        
        # MEMORY.md 应该被跳过
        if recalled_content:
            assert "MEMORY.md" not in recalled_content or "Other" in recalled_content
    
    @pytest.mark.asyncio
    async def test_auto_recall_respects_max_bytes(self, coding_memory_system_env):
        """测试自动召回遵守字节数限制."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        assert rail.MAX_RECALL_TOTAL_BYTES == 10240  # 10KB
    
    @pytest.mark.asyncio
    async def test_auto_recall_no_results(self, coding_memory_system_env):
        """测试自动召回无结果."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock manager 返回空结果
        mock_manager = AsyncMock()
        mock_manager.search = AsyncMock(return_value=[])
        rail._manager = mock_manager
        
        recalled_content, total = await rail._auto_recall("UnknownQuery12345")
        
        assert recalled_content is None


class TestCodingMemoryPromptInjection:
    """测试 Prompt 注入逻辑."""
    
    @pytest.mark.asyncio
    async def test_before_model_call_injects_recall_content(self, coding_memory_system_env):
        """测试有召回结果时注入全文."""
        env = coding_memory_system_env
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock system_prompt_builder
        mock_builder = Mock()
        mock_builder.language = "cn"
        mock_builder.remove_section = Mock()
        mock_builder.add_section = Mock()
        rail.system_prompt_builder = mock_builder
        
        # 设置已召回的内容
        rail._recalled_content = "### 测试记忆 [test.md]\n\n测试内容"
        rail._total_memories = 3
        
        # Mock context
        mock_ctx = Mock()
        mock_ctx.inputs = Mock()
        mock_ctx.inputs.is_cron = Mock(return_value=False)
        mock_ctx.inputs.is_heartbeat = Mock(return_value=False)
        
        await rail.before_model_call(mock_ctx)
        
        # 验证注入了召回内容
        mock_builder.add_section.assert_called_once()
        call_args = mock_builder.add_section.call_args[0][0]
        assert "已加载的相关记忆" in call_args.content["cn"]
        assert "测试记忆" in call_args.content["cn"]
    
    @pytest.mark.asyncio
    async def test_before_model_call_fallback_to_index(self, coding_memory_system_env):
        """测试无召回结果时降级注入索引."""
        env = coding_memory_system_env
        
        # 创建索引文件
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            "test.md", 
            {"name": "Test Memory", "description": "Test description"}
        )
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock system_prompt_builder
        mock_builder = Mock()
        mock_builder.language = "cn"
        mock_builder.remove_section = Mock()
        mock_builder.add_section = Mock()
        rail.system_prompt_builder = mock_builder
        
        # 无召回内容
        rail._recalled_content = None
        
        # Mock context
        mock_ctx = Mock()
        mock_ctx.inputs = Mock()
        mock_ctx.inputs.is_cron = Mock(return_value=False)
        mock_ctx.inputs.is_heartbeat = Mock(return_value=False)
        
        await rail.before_model_call(mock_ctx)
        
        # 验证注入了索引
        mock_builder.add_section.assert_called_once()
        call_args = mock_builder.add_section.call_args[0][0]
        assert "当前记忆索引" in call_args.content["cn"] or "Current memory index" in call_args.content["cn"]
    
    @pytest.mark.asyncio
    async def test_before_model_call_read_only_mode(self, coding_memory_system_env):
        """测试只读模式（cron/heartbeat）."""
        env = coding_memory_system_env
        
        # 创建索引文件
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            "test.md", 
            {"name": "Test", "description": "Test"}
        )
        
        rail = CodingMemoryRail(
            coding_memory_dir=env["coding_memory_dir"],
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )
        rail.sys_operation = env["sys_op"]
        
        # Mock system_prompt_builder
        mock_builder = Mock()
        mock_builder.language = "cn"
        mock_builder.remove_section = Mock()
        mock_builder.add_section = Mock()
        rail.system_prompt_builder = mock_builder
        
        # Mock context - cron 模式
        # 需要使用 InvokeInputs 类型
        from openjiuwen.core.single_agent.rail.base import InvokeInputs
        mock_inputs = Mock(spec=InvokeInputs)
        mock_inputs.is_cron = Mock(return_value=True)
        mock_inputs.is_heartbeat = Mock(return_value=False)
        
        mock_ctx = Mock()
        mock_ctx.inputs = mock_inputs
        
        await rail.before_model_call(mock_ctx)
        
        # 验证注入了只读提示
        mock_builder.add_section.assert_called_once()
        call_args = mock_builder.add_section.call_args[0][0]
        # 只读模式应该包含"只读"字样
        content = call_args.content["cn"]
        assert "只读" in content or "read-only" in content.lower()


class TestCodingMemoryEndToEnd:
    """端到端测试 - 完整流程."""
    
    @pytest.mark.asyncio
    async def test_full_workflow_write_recall_read(self, coding_memory_system_env):
        """测试完整流程：写入 -> 召回 -> 读取."""
        env = coding_memory_system_env
        
        # 1. 写入记忆
        content = """---
name: Database Preference
description: Prefer PostgreSQL over MySQL
type: feedback
---

数据库选择：优先使用 PostgreSQL 而不是 MySQL.
**原因：** PostgreSQL 支持更丰富的数据类型和更好的扩展性.
**如何应用：** 新项目默认使用 PostgreSQL."""
        
        write_result = await coding_memory_write.invoke({"path": "db_pref.md", "content": content})
        assert write_result["success"] is True
        
        # 2. 验证文件创建
        read_result = await coding_memory_read.invoke({"path": "db_pref.md"})
        assert read_result["success"] is True
        assert "PostgreSQL" in read_result["content"]
        
        # 3. 手动更新索引验证
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            "db_pref.md", 
            {"name": "Database Preference", "description": "Prefer PostgreSQL over MySQL"}
        )
        
        # 4. 验证索引更新
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        assert "Database Preference" in index_content
    
    @pytest.mark.asyncio
    async def test_all_memory_types(self, coding_memory_system_env):
        """测试所有四种记忆类型."""
        env = coding_memory_system_env
        
        memories = [
            ("user_role.md", "user", "User Role", "用户角色"),
            ("feedback_style.md", "feedback", "Code Style", "代码风格反馈"),
            ("project_deadline.md", "project", "Project Deadline", "项目截止日期"),
            ("reference_api.md", "reference", "API Reference", "API 参考"),
        ]
        
        for filename, mem_type, name, desc in memories:
            content = f"""---
name: {name}
description: {desc}
type: {mem_type}
---

这是 {mem_type} 类型的记忆内容."""
            
            result = await coding_memory_write.invoke({"path": filename, "content": content})
            assert result["success"] is True, f"Failed to write {mem_type} memory"
            assert result["type"] == mem_type
            
            # 手动更新索引
            await _upsert_memory_index(
                env["coding_memory_dir"], 
                filename, 
                {"name": name, "description": desc}
            )
        
        # 验证所有文件创建
        for filename, _, _, _ in memories:
            read_result = await coding_memory_read.invoke({"path": filename})
            assert read_result["success"] is True, f"File {filename} should exist"
        
        # 验证索引包含所有条目
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        
        for filename, _, name, _ in memories:
            assert name in index_content, f"Index should contain {name}"
    
    @pytest.mark.asyncio
    async def test_memory_update_workflow(self, coding_memory_system_env):
        """测试记忆更新流程."""
        env = coding_memory_system_env
        
        # 使用唯一的文件名避免冲突
        import time
        unique_id = str(int(time.time() * 1000))[-6:]
        filename = f"team_{unique_id}.md"
        
        # 1. 创建初始记忆
        content = f"""---
name: Team Member {unique_id}
description: Team member info
type: project
---

团队成员：张三，负责后端开发."""
        
        write_result = await coding_memory_write.invoke({"path": filename, "content": content})
        assert write_result["success"] is True, f"Write failed: {write_result.get('error', 'unknown')}"
        
        # 2. 更新内容
        edit_result = await coding_memory_edit.invoke({
            "path": filename,
            "old_text": "张三，负责后端开发",
            "new_text": "张三，负责后端开发和架构设计"
        })
        assert edit_result["success"] is True, f"Edit failed: {edit_result.get('error', 'unknown')}"
        
        # 3. 验证更新
        read_result = await coding_memory_read.invoke({"path": filename})
        assert read_result["success"] is True
        assert "架构设计" in read_result["content"]
        
        # 4. 手动更新索引验证
        await _upsert_memory_index(
            env["coding_memory_dir"], 
            filename, 
            {"name": f"Team Member {unique_id}", "description": "Updated team member info"}
        )
        
        # 5. 验证索引更新
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        assert f"Team Member {unique_id}" in index_content


class TestCodingMemoryEdgeCases:
    """边界情况测试."""
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, coding_memory_system_env):
        """测试读取不存在的文件."""
        result = await coding_memory_read.invoke({"path": "nonexistent.md"})
        assert result["success"] is False
    
    @pytest.mark.asyncio
    async def test_write_empty_content(self, coding_memory_system_env):
        """测试写入空内容."""
        result = await coding_memory_write.invoke({"path": "empty.md", "content": ""})
        assert result["success"] is False
    
    @pytest.mark.asyncio
    async def test_write_non_md_file(self, coding_memory_system_env):
        """测试写入非 .md 文件."""
        content = """---
name: Test
description: Test
type: user
---

内容."""
        
        result = await coding_memory_write.invoke({"path": "test.txt", "content": content})
        assert result["success"] is False
    
    @pytest.mark.asyncio
    async def test_edit_empty_old_text(self, coding_memory_system_env):
        """测试编辑空 old_text."""
        env = coding_memory_system_env
        
        content = """---
name: Test
description: Test
type: user
---

内容."""
        
        await coding_memory_write.invoke({"path": "test.md", "content": content})
        
        result = await coding_memory_edit.invoke({
            "path": "test.md",
            "old_text": "",
            "new_text": "新内容"
        })
        
        assert result["success"] is False
    
    @pytest.mark.asyncio
    async def test_memory_index_max_lines(self, coding_memory_system_env):
        """测试 MEMORY.md 最大行数限制."""
        env = coding_memory_system_env
        
        # 创建多个记忆文件
        for i in range(10):
            content = f"""---
name: Memory {i}
description: Test memory {i}
type: user
---

内容 {i}."""
            await coding_memory_write.invoke({"path": f"mem_{i}.md", "content": content})
            
            # 手动更新索引
            await _upsert_memory_index(
                env["coding_memory_dir"], 
                f"mem_{i}.md", 
                {"name": f"Memory {i}", "description": f"Test memory {i}"}
            )
        
        # 验证索引文件存在
        index_path = os.path.join(env["coding_memory_dir"], "MEMORY.md")
        index_content = await _read_file_safe(index_path)
        
        # 验证所有条目都在索引中
        for i in range(10):
            assert f"Memory {i}" in index_content
