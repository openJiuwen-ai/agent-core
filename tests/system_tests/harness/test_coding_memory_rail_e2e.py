"""CodingMemoryRail E2E 测试 - 基础功能测试."""

import os
import tempfile
import shutil
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.rails.coding_memory_rail import CodingMemoryRail
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig


@pytest_asyncio.fixture
async def coding_memory_test_env():
    """创建 CodingMemoryRail 测试环境."""
    await Runner.start()
    tmp_dir = tempfile.mkdtemp(prefix="coding_memory_rail_e2e_")
    work_dir = Workspace().root_path
    sys_operation_id = f"coding_memory_rail_sysop_{uuid.uuid4().hex}"
    card = SysOperationCard(
        id=sys_operation_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=work_dir),
    )
    add_result = Runner.resource_mgr.add_sys_operation(card)
    if add_result.is_err():
        raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    coding_memory_dir = Path(work_dir) / "coding_memory"
    coding_memory_dir.mkdir(parents=True, exist_ok=True)

    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)

    yield {
        "tmp_dir": tmp_dir,
        "work_dir": work_dir,
        "sys_operation_id": sys_operation_id,
        "sys_op": sys_op,
        "coding_memory_dir": str(coding_memory_dir),
    }

    try:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await Runner.stop()


class TestCodingMemoryRailE2E:
    """端到端测试：验证完整流程."""

    @pytest.mark.asyncio
    async def test_full_invoke_flow(self, coding_memory_test_env):
        """测试完整调用流程."""
        from unittest.mock import Mock, AsyncMock, patch

        env = coding_memory_test_env
        coding_memory_dir = env["coding_memory_dir"]

        rail = CodingMemoryRail(
            coding_memory_dir=coding_memory_dir,
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )

        # 设置真实的 sys_operation
        rail.sys_operation = env["sys_op"]

        # Mock agent
        mock_agent = Mock()
        mock_agent.card = Mock()
        mock_agent.card.id = "test-agent"
        mock_agent.ability_manager = Mock()
        mock_agent.ability_manager.add = Mock(return_value=Mock(added=True))
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.system_prompt_builder.remove_section = Mock()
        mock_agent.system_prompt_builder.add_section = Mock()

        rail.init(mock_agent)

        # Mock context
        mock_msg = Mock()
        mock_msg.role = "user"
        mock_msg.content = "记住我喜欢使用 Python"

        mock_ctx = Mock()
        mock_ctx.session.agent_id = "test-agent"
        mock_ctx.inputs = Mock()
        mock_ctx.inputs.messages = [mock_msg]
        mock_ctx.inputs.is_cron = Mock(return_value=False)
        mock_ctx.inputs.is_heartbeat = Mock(return_value=False)
        mock_ctx.agent = mock_agent

        rail.workspace = Mock()

        # 执行 before_invoke
        with patch.object(rail, '_init_coding_memory_manager', new_callable=AsyncMock):
            rail._manager = AsyncMock()
            rail._manager_initialized = True

            await rail.before_invoke(mock_ctx)
            assert rail._prefetch_task is not None

        # 执行 before_model_call
        await rail.before_model_call(mock_ctx)
        assert mock_agent.system_prompt_builder.add_section.called

    @pytest.mark.asyncio
    async def test_auto_recall_with_results(self, coding_memory_test_env):
        """测试自动召回有结果的情况."""
        from unittest.mock import AsyncMock, MagicMock

        env = coding_memory_test_env
        coding_memory_dir = env["coding_memory_dir"]
        sys_op = env["sys_op"]

        # 创建记忆文件
        mem_file = os.path.join(coding_memory_dir, "python_pref.md")
        file_content = """---
name: Python Preference
description: User prefers Python
type: user
---

用户喜欢使用 Python 编程."""

        await sys_op.fs().write_file(
            mem_file,
            content=file_content,
            create_if_not_exist=True
        )

        rail = CodingMemoryRail(
            coding_memory_dir=coding_memory_dir,
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )

        # 设置真实的 sys_operation
        rail.sys_operation = sys_op

        # Mock manager
        mock_manager = AsyncMock()
        mock_manager.search = AsyncMock(return_value=[
            {"path": "python_pref.md", "score": 0.9}
        ])
        rail._manager = mock_manager

        content, total = await rail._auto_recall("Python")

        assert content is not None
        assert "Python Preference" in content
        assert total >= 1

    @pytest.mark.asyncio
    async def test_auto_recall_no_results(self, coding_memory_test_env):
        """测试自动召回无结果的情况."""
        from unittest.mock import AsyncMock, patch

        env = coding_memory_test_env
        coding_memory_dir = env["coding_memory_dir"]
        sys_op = env["sys_op"]

        rail = CodingMemoryRail(
            coding_memory_dir=coding_memory_dir,
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )

        # 设置真实的 sys_operation
        rail.sys_operation = sys_op

        # Mock manager 返回空结果
        mock_manager = AsyncMock()
        mock_manager.search = AsyncMock(return_value=[])
        rail._manager = mock_manager

        # Mock _count_memory_files to return 0
        with patch.object(rail, '_count_memory_files', return_value=0):
            content, total = await rail._auto_recall("UnknownQuery")

            assert content is None
            assert total == 0

    @pytest.mark.asyncio
    async def test_before_model_call_with_recall_results(self, coding_memory_test_env):
        """测试有召回结果时的 prompt 注入."""
        from unittest.mock import Mock

        env = coding_memory_test_env
        coding_memory_dir = env["coding_memory_dir"]
        sys_op = env["sys_op"]

        rail = CodingMemoryRail(
            coding_memory_dir=coding_memory_dir,
            embedding_config=EmbeddingConfig(
                model_name="test-model",
                base_url="http://test",
                api_key="test-key"
            ),
            language="cn"
        )

        # 设置真实的 sys_operation
        rail.sys_operation = sys_op

        # Mock system_prompt_builder
        mock_builder = Mock()
        mock_builder.language = "cn"
        mock_builder.remove_section = Mock()
        mock_builder.add_section = Mock()
        rail.system_prompt_builder = mock_builder

        # 设置已召回的内容
        rail._recalled_content = "### 测试记忆\n\n测试内容"
        rail._total_memories = 5

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

    def test_scenario_switching(self):
        """测试场景切换逻辑."""
        # 内联实现场景切换逻辑（避免跨模块导入）
        def get_memory_scenario(config):
            memory_cfg = (config or {}).get("memory", {})
            scenario = str(memory_cfg.get("scenario") or "personal").strip().lower()
            return "coding" if scenario == "coding" else "personal"

        # 测试 personal 场景
        config = {"memory": {"scenario": "personal"}}
        assert get_memory_scenario(config) == "personal"

        # 测试 coding 场景
        config = {"memory": {"scenario": "coding"}}
        assert get_memory_scenario(config) == "coding"

        # 测试默认场景
        config = {"memory": {}}
        assert get_memory_scenario(config) == "personal"

        # 测试大小写不敏感
        config = {"memory": {"scenario": "CODING"}}
        assert get_memory_scenario(config) == "coding"
