# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
新版 ReActAgent 单元测试（使用 Card + Config 模式）

本测试用例测试新版 ReActAgent，使用 AgentCard + ReActAgentConfig 的
Card + Config 设计模式。与老接口 (create_react_agent_config) 区分。

## 测试场景

1. AgentCard + ReActAgentConfig 创建和配置
2. configure() 链式调用
3. add_ability() 方法添加工具
4. 基本工具调用场景
5. 多轮工具调用场景
6. 纯对话场景（无工具调用）
7. max_iterations 限制场景
8. 错误处理场景

## Mock 策略

- 使用共享的 `MockLLMModel` 类
- 通过 `patch` ReActAgent._get_llm 来注入 mock 实例
- mock Session 和 ContextEngine 来隔离测试

## 与老接口测试的区别

- 老接口: test_react_agent_mock.py 使用 create_react_agent_config()
- 新接口: 本文件使用 AgentCard + ReActAgentConfig + configure()
"""
import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen.core.single_agent.agents.react_agent import (
    ReActAgent,
    ReActAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.context_engine import ContextEngineConfig

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


class TestNewReActAgentConfig(unittest.IsolatedAsyncioTestCase):
    """测试 ReActAgentConfig 配置类"""

    def test_config_default_values(self):
        """测试配置默认值"""
        config = ReActAgentConfig()
        self.assertEqual(config.mem_scope_id, "")
        self.assertEqual(config.model_name, "")
        self.assertEqual(config.model_provider, "openai")
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.api_base, "")
        self.assertEqual(config.prompt_template_name, "")
        self.assertEqual(config.prompt_template, [])
        self.assertEqual(config.context_engine_config, ContextEngineConfig(
            max_context_message_num=200, default_window_round_num=10
        ))
        self.assertEqual(config.max_iterations, 5)

    def test_config_chained_configuration(self):
        """测试链式调用配置"""
        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_model_provider(
                provider="openai",
                api_key="test_key",
                api_base="https://api.test.com"
            )
            .configure_prompt_template([
                {"role": "system", "content": "你是一个助手"}
            ])
            .configure_context_engine(
                max_context_message_num=100,
                default_window_round_num=20,
                enable_reload=True
            )
            .configure_max_iterations(10)
        )

        self.assertEqual(config.model_name, "gpt-4")
        self.assertEqual(config.model_provider, "openai")
        self.assertEqual(config.api_key, "test_key")
        self.assertEqual(config.api_base, "https://api.test.com")
        self.assertEqual(len(config.prompt_template), 1)
        self.assertEqual(config.context_engine_config, ContextEngineConfig(
            max_context_message_num=100,
            default_window_round_num=20,
            enable_reload=True
        ))
        self.assertEqual(config.max_iterations, 10)

    def test_configure_mem_scope(self):
        """测试内存范围配置"""
        config = ReActAgentConfig().configure_mem_scope("test_scope")
        self.assertEqual(config.mem_scope_id, "test_scope")

    def test_configure_prompt_name(self):
        """测试提示模板名称配置"""
        config = ReActAgentConfig().configure_prompt("test_prompt")
        self.assertEqual(config.prompt_template_name, "test_prompt")


class TestNewReActAgentCreation(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent 创建"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

    def test_agent_creation_with_card(self):
        """测试使用 AgentCard 创建 Agent"""
        card = AgentCard(
            name="test_agent",
            description="测试用 Agent"
        )

        agent = ReActAgent(card=card)

        self.assertEqual(agent.card.name, "test_agent")
        self.assertEqual(agent.card.description, "测试用 Agent")
        # AgentCard 从 BaseCard 继承，有自动生成的 id
        self.assertGreater(len(agent.card.id), 0)

    def test_agent_configure_method(self):
        """测试 Agent 的 configure 方法"""
        card = AgentCard(
            name="test_agent",
            description="测试用 Agent"
        )

        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_max_iterations(10)
        )

        agent = ReActAgent(card=card)
        result = agent.configure(config)

        # 验证 configure 返回 self（支持链式调用）
        self.assertIs(result, agent)
        self.assertEqual(agent.config.model_name, "gpt-4")
        self.assertEqual(agent.config.max_iterations, 10)


class TestNewReActAgentAbility(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent 能力管理"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        self.card = AgentCard(
            name="test_agent",
            description="测试用 Agent"
        )

    def _create_add_tool_card(self):
        """创建加法工具 Card"""
        return ToolCard(
            name="add",
            description="加法运算",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "第一个加数", "type": "number"},
                    "b": {"description": "第二个加数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    def _create_multiply_tool_card(self):
        """创建乘法工具 Card"""
        return ToolCard(
            name="multiply",
            description="乘法运算",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "第一个乘数", "type": "number"},
                    "b": {"description": "第二个乘数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    def test_add_ability_single(self):
        """测试添加单个能力"""
        agent = ReActAgent(card=self.card)
        tool_card = self._create_add_tool_card()

        agent.ability_manager.add(tool_card)

        abilities = agent.ability_manager.list()
        self.assertEqual(len(abilities), 1)
        self.assertEqual(abilities[0].name, "add")

    def test_add_ability_list(self):
        """测试添加多个能力"""
        agent = ReActAgent(card=self.card)
        add_card = self._create_add_tool_card()
        multiply_card = self._create_multiply_tool_card()

        agent.ability_manager.add([add_card, multiply_card])

        abilities = agent.ability_manager.list()
        self.assertEqual(len(abilities), 2)
        names = [a.name for a in abilities]
        self.assertIn("add", names)
        self.assertIn("multiply", names)

    def test_remove_ability(self):
        """测试移除能力"""
        agent = ReActAgent(card=self.card)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.ability_manager.add(self._create_multiply_tool_card())

        result = agent.ability_manager.remove("add")

        abilities = agent.ability_manager.list()
        self.assertEqual(len(abilities), 1)
        self.assertEqual(abilities[0].name, "multiply")

    def test_get_ability(self):
        """测试获取能力"""
        agent = ReActAgent(card=self.card)
        agent.ability_manager.add(self._create_add_tool_card())

        ability = agent.ability_manager.get("add")
        self.assertIsNotNone(ability)
        self.assertEqual(ability.name, "add")

        not_exist = agent.ability_manager.get("not_exist")
        self.assertIsNone(not_exist)

    async def test_list_tool_info(self):
        """测试获取 ToolInfo 列表"""
        agent = ReActAgent(card=self.card)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.ability_manager.add(self._create_multiply_tool_card())

        tool_infos = await agent.ability_manager.list_tool_info()
        self.assertEqual(len(tool_infos), 2)

        names = [t.name for t in tool_infos]
        self.assertIn("add", names)
        self.assertIn("multiply", names)


class TestNewReActAgentInvoke(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent invoke 方法"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        self.card = AgentCard(
            name="test_agent",
            description="数学计算助手"
        )
        self.config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_prompt_template([
                {"role": "system", "content": "你是一个数学计算助手"}
            ])
            .configure_max_iterations(5)
        )

    def _create_add_tool_card(self):
        """创建加法工具 Card"""
        return ToolCard(
            name="add",
            description="加法运算",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "第一个加数", "type": "number"},
                    "b": {"description": "第二个加数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    def _create_multiply_tool_card(self):
        """创建乘法工具 Card"""
        return ToolCard(
            name="multiply",
            description="乘法运算",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "第一个乘数", "type": "number"},
                    "b": {"description": "第二个乘数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    def _create_mock_session(self):
        """创建 mock session"""
        mock_session = MagicMock()
        mock_session.get_state.return_value = None
        mock_session.write_stream = AsyncMock()
        return mock_session


    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_tool')
    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_tool_infos')
    @pytest.mark.asyncio
    async def test_invoke_pure_conversation(self, mock_get_tool, mock_get_tool_infos):
        """测试纯对话场景（无工具调用）"""
        mock_llm = MockLLMModel()
        mock_get_tool.return_value = MagicMock(return_value=None)
        mock_get_tool_infos.return_value = MagicMock(return_value=[])
        mock_llm.set_responses([
            create_text_response("你好！我是数学计算助手，有什么可以帮助你的吗？"),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = self._create_mock_session()

        agent = ReActAgent(card=self.card)
        agent.configure(self.config)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"conversation_id": "test_session", "query": "你好"},
                session=mock_session
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')
        self.assertIn('你好', result['output'])
        self.assertEqual(mock_llm.call_count, 1)

    @pytest.mark.asyncio
    async def test_invoke_with_tool_call(self):
        """测试工具调用场景"""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("根据计算结果，1+2=3"),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = MagicMock()
        mock_session.get_state.return_value = None
        mock_session.write_stream = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.invoke = AsyncMock(return_value=3)

        agent = ReActAgent(card=self.card)
        agent.configure(self.config)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"conversation_id": "test_session", "query": "计算1+2"},
                session=mock_session
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')
        self.assertIn('3', result['output'])
        self.assertEqual(mock_llm.call_count, 2)

    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_tool')
    @pytest.mark.asyncio
    async def test_invoke_multi_turn_tool_calls(self, mock_get_tool):
        """测试多轮工具调用场景"""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_tool_call_response("multiply", '{"a": 3, "b": 3}'),
            create_text_response("计算结果：(1+2) * 3 = 9"),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = MagicMock()
        mock_session.get_state.return_value = None
        mock_session.write_stream = AsyncMock()

        def get_tool_side_effect(name):
            mock_tool = MagicMock()
            if name == "add":
                mock_tool.invoke = AsyncMock(return_value=3)
            elif name == "multiply":
                mock_tool.invoke = AsyncMock(return_value=9)
            return mock_tool

        mock_get_tool.return_value = MagicMock(side_effect=get_tool_side_effect)

        agent = ReActAgent(card=self.card)
        agent.configure(self.config)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.ability_manager.add(self._create_multiply_tool_card())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "计算 (1+2) * 3"},
                session=mock_session
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')
        self.assertIn('9', result['output'])
        self.assertEqual(mock_llm.call_count, 3)

    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_tool')
    @pytest.mark.asyncio
    async def test_invoke_max_iterations_reached(self, mock_get_tool):
        """测试达到最大迭代次数"""
        mock_llm = MockLLMModel()
        # 每次都返回工具调用，不返回最终答案
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_tool_call_response("add", '{"a": 3, "b": 4}'),
            create_tool_call_response("add", '{"a": 5, "b": 6}'),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = MagicMock()
        mock_session.get_state.return_value = None
        mock_session.write_stream = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.invoke = AsyncMock(return_value=3)
        mock_get_tool.return_value = MagicMock(return_value=mock_tool)

        # 设置 max_iterations 为 2
        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_max_iterations(2)
        )

        agent = ReActAgent(card=self.card)
        agent.configure(config)
        agent.ability_manager.add(self._create_add_tool_card())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "一直计算"},
                session=mock_session
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'error')
        self.assertIn('Max iterations', result['output'])

    @pytest.mark.asyncio
    async def test_invoke_with_string_input(self):
        """测试字符串输入格式"""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("这是对字符串输入的响应"),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = self._create_mock_session()

        agent = ReActAgent(card=self.card)
        agent.configure(self.config)
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                "这是一个字符串查询",
                session=mock_session
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')

    @pytest.mark.asyncio
    async def test_invoke_missing_query_raises_error(self):
        """测试缺少 query 字段抛出异常"""
        agent = ReActAgent(card=self.card)

        with pytest.raises(ValueError) as exc_info:
            await agent.invoke({"conversation_id": "test"})

        self.assertIn("query", str(exc_info.value))

    @pytest.mark.asyncio
    async def test_invoke_invalid_input_raises_error(self):
        """测试无效输入类型抛出异常"""
        agent = ReActAgent(card=self.card)

        with pytest.raises(ValueError) as exc_info:
            await agent.invoke(12345)

        self.assertIn("must be dict", str(exc_info.value))


class TestNewReActAgentStream(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent stream 方法"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        self.card = AgentCard(
            name="test_agent",
            description="流式测试 Agent"
        )
        self.config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_max_iterations(5)
        )

    def _create_mock_session(self):
        """创建 mock session，模拟真实 Session 的 write_stream/stream_iterator 行为"""
        import asyncio
        mock_session = MagicMock()
        mock_session.get_state.return_value = None
        mock_session.update_state.return_value = None
        mock_session.pre_run = AsyncMock()
        data_queue = asyncio.Queue()

        async def mock_write_stream(data):
            await data_queue.put(data)

        async def mock_post_run():
            await data_queue.put(None)  # 发送结束信号

        async def mock_stream_iterator():
            while True:
                data = await data_queue.get()
                if data is None:
                    break
                yield data

        mock_session.write_stream = mock_write_stream
        mock_session.post_run = mock_post_run
        mock_session.stream_iterator = mock_stream_iterator
        return mock_session

    @pytest.mark.asyncio
    async def test_stream_yields_final_result(self):
        """测试流式调用返回最终结果"""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("这是流式响应"),
        ])

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.add_messages = AsyncMock()
        mock_context.get_context_window = AsyncMock(return_value=MagicMock(
            get_messages=MagicMock(return_value=[]),
            get_tools=MagicMock(return_value=None)
        ))

        # 创建 mock context_engine
        mock_context_engine = MagicMock()
        mock_context_engine.save_contexts = AsyncMock()
        mock_context_engine.create_context = AsyncMock(return_value=mock_context)

        # 创建 mock session
        mock_session = self._create_mock_session()

        agent = ReActAgent(card=self.card)
        agent.configure(self.config)
        agent.context_engine = mock_context_engine

        results = []
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            async for result in agent.stream(
                {"query": "流式测试"},
                session=mock_session
            ):
                results.append(result)

        # 验证有结果返回
        self.assertGreater(len(results), 0)
        # 验证最终结果是 OutputSchema（新版本行为）
        final_result = results[-1]
        from openjiuwen.core.session.stream.base import OutputSchema
        self.assertIsInstance(final_result, OutputSchema)
        self.assertEqual(final_result.type, 'answer')
        self.assertEqual(final_result.payload['result_type'], 'answer')


class TestNewReActAgentGetToolInfo(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent get_tool_info 方法"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

    def test_get_tool_info_returns_agent_as_tool(self):
        """测试将 Agent 转换为 ToolInfo（作为子 Agent 使用）"""
        card = AgentCard(
            name="sub_agent",
            description="子 Agent 描述",
            input_params={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户输入信息",
                    }
                },
                "required": ["query"]
            }
        )

        agent = ReActAgent(card=card)

        tool_info = agent.card.tool_info()

        self.assertEqual(tool_info.name, "sub_agent")
        self.assertEqual(tool_info.description, "子 Agent 描述")
        self.assertIn("query", tool_info.parameters.get("properties", {}))


class TestNewReActAgentConfigUpdate(unittest.IsolatedAsyncioTestCase):
    """测试新版 ReActAgent 配置更新"""

    def setUp(self):
        """设置测试环境"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        self.card = AgentCard(
            name="test_agent",
            description="配置更新测试"
        )

    def test_configure_resets_llm_on_provider_change(self):
        """测试更改 provider 时重置 LLM"""
        with patch.object(
            ReActAgent,
            '_get_llm',
            wraps=lambda self: MagicMock()
        ) as mock_get_llm:
            agent = ReActAgent(card=self.card)

            # 设置初始配置
            initial_config = (
                ReActAgentConfig()
                .configure_model_provider("openai", "key1", "base1")
            )
            agent.configure(initial_config)

            # 更改 provider 配置
            new_config = (
                ReActAgentConfig()
                .configure_model_provider("azure", "key2", "base2")
            )
            agent.configure(new_config)

        # 验证配置已更新
        self.assertEqual(agent.config.model_provider, "azure")
        self.assertEqual(agent.config.api_key, "key2")
        self.assertEqual(agent.config.api_base, "base2")

    def test_configure_updates_context_engine_on_limit_change(self):
        """测试更改 上下文窗口对话轮次 时更新 context_engine"""
        agent = ReActAgent(card=self.card)
        old_context_engine = agent.context_engine

        # 更改 上下文窗口对话轮次
        new_config = ReActAgentConfig().configure_context_engine(default_window_round_num=20)
        agent.configure(new_config)

        # context_engine 应该被更新
        self.assertIsNot(agent.context_engine, old_context_engine)


class TestNewReActAgentToolTagIsolation(unittest.IsolatedAsyncioTestCase):
    """Test tool tag isolation for the new ReActAgent.
    Covers commit: fix(controller): use agent_id as tag to get tool

    Core logic: ReActAgent registers tools via Runner.resource_mgr.add_tool(tool, tag=card.id)
    and retrieves them via ability_manager.execute(..., tag=card.id), ensuring tools
    from different agents are isolated.
    """

    async def asyncSetUp(self):
        """Start Runner before each test"""
        from openjiuwen.core.runner import Runner
        await Runner.start()

    async def asyncTearDown(self):
        """Stop Runner after each test"""
        from openjiuwen.core.runner import Runner
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_react_agent_add_tool_with_agent_tag(self):
        """When ReActAgent registers a tool, the tag should be agent card.id, not GLOBAL"""
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.runner.resources_manager.base import GLOBAL
        from openjiuwen.core.foundation.tool import LocalFunction

        agent_card = AgentCard(id="react_agent_001", name="test_react")
        tool_card = ToolCard(
            id="add_tool",
            name="add",
            description="addition",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "addend"},
                    "b": {"type": "number", "description": "augend"}
                },
                "required": ["a", "b"]
            }
        )
        tool = LocalFunction(card=tool_card, func=lambda a, b: a + b)

        # Simulate ReActAgent registering tool with tag=agent_card.id
        Runner.resource_mgr.add_tool(tool, tag=agent_card.id)

        # Verify: tool is tagged with agent_card.id, not GLOBAL
        assert Runner.resource_mgr.resource_has_tag("add_tool", agent_card.id)
        assert not Runner.resource_mgr.resource_has_tag("add_tool", GLOBAL)

    @pytest.mark.asyncio
    async def test_react_agent_tools_isolated_between_agents(self):
        """Tools registered by two different ReActAgents are isolated via tag"""
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.foundation.tool import LocalFunction

        # Agent A registers its tool
        tool_a = LocalFunction(
            card=ToolCard(id="tool_a", name="tool_a", description="Agent A tool"),
            func=lambda: "a"
        )
        Runner.resource_mgr.add_tool(tool_a, tag="agent_A")

        # Agent B registers its tool
        tool_b = LocalFunction(
            card=ToolCard(id="tool_b", name="tool_b", description="Agent B tool"),
            func=lambda: "b"
        )
        Runner.resource_mgr.add_tool(tool_b, tag="agent_B")

        # Agent A can only see its own tool via tag query
        infos_a = await Runner.resource_mgr.get_tool_infos(tag="agent_A")
        names_a = [info.name for info in infos_a if info]
        assert "tool_a" in names_a
        assert "tool_b" not in names_a

        # Agent B can only see its own tool via tag query
        infos_b = await Runner.resource_mgr.get_tool_infos(tag="agent_B")
        names_b = [info.name for info in infos_b if info]
        assert "tool_b" in names_b
        assert "tool_a" not in names_b


class TestAbilityManagerFixes(unittest.IsolatedAsyncioTestCase):
    """测试 AbilityManager 的 bug 修复和逻辑优化"""

    def setUp(self):
        """设置测试环境"""
        from openjiuwen.core.single_agent import AbilityManager
        self.ability_manager = AbilityManager()

    def test_remove_batch_returns_complete_list(self):
        """测试批量删除时返回完整列表（修复循环内 return 的 bug）"""
        # 添加多个工具
        tool1 = ToolCard(name="tool1", description="工具1")
        tool2 = ToolCard(name="tool2", description="工具2")
        tool3 = ToolCard(name="tool3", description="工具3")

        self.ability_manager.add([tool1, tool2, tool3])

        # 批量删除多个工具
        result = self.ability_manager.remove(["tool1", "tool2"])

        # 验证返回的是完整列表（不是第一次循环就返回）
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, "tool1")
        self.assertEqual(result[1].name, "tool2")

        # 验证剩余工具
        remaining = self.ability_manager.list()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].name, "tool3")

    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_mcp_tool_infos')
    async def test_list_tool_info_adds_mcp_tools_to_tools_dict(self, mock_get_mcp_tool_infos):
        """测试 MCP 工具被添加到 _tools（用于映射 tool_name 到完整 ID）"""
        # pylint: disable=protected-access
        from openjiuwen.core.foundation.tool import McpServerConfig
        from openjiuwen.core.foundation.tool.schema import ToolInfo

        # Mock MCP 工具返回
        mock_mcp_tool = ToolInfo(
            name="mcp_tool",
            description="MCP 工具",
            parameters={}
        )
        mock_get_mcp_tool_infos.return_value = [mock_mcp_tool]

        # 添加 MCP 服务器配置
        mcp_config = McpServerConfig(
            server_name="test_mcp",
            server_id="mcp_001",
            server_path="/test/path"
        )
        self.ability_manager.add(mcp_config)

        # 获取 tool_infos
        tool_infos = await self.ability_manager.list_tool_info()

        # 验证 MCP 工具在返回列表中
        self.assertEqual(len(tool_infos), 1)
        self.assertEqual(tool_infos[0].name, "mcp_tool")

        # 验证 MCP 工具被添加到 _tools 字典（用于映射）
        self.assertEqual(len(self.ability_manager._tools), 1)
        self.assertIn("mcp_tool", self.ability_manager._tools)
        # 验证 ID 格式为 {server_id}.{server_name}.{tool_name}
        self.assertEqual(self.ability_manager._tools["mcp_tool"].id, "mcp_001.test_mcp.mcp_tool")

    @patch('openjiuwen.core.runner.Runner.resource_mgr.get_mcp_tool_infos')
    async def test_remove_mcp_server_also_removes_mcp_tools(self, mock_get_mcp_tool_infos):
        """测试删除 MCP 服务器时同时删除对应的 MCP 工具"""
        # pylint: disable=protected-access
        from openjiuwen.core.foundation.tool import McpServerConfig
        from openjiuwen.core.foundation.tool.schema import ToolInfo

        # Mock MCP 工具返回
        mock_mcp_tool1 = ToolInfo(name="tool1", description="工具1", parameters={})
        mock_mcp_tool2 = ToolInfo(name="tool2", description="工具2", parameters={})
        mock_get_mcp_tool_infos.return_value = [mock_mcp_tool1, mock_mcp_tool2]

        # 添加 MCP 服务器配置
        mcp_config = McpServerConfig(
            server_name="test_mcp",
            server_id="mcp_001",
            server_path="/test/path"
        )
        self.ability_manager.add(mcp_config)

        # 获取 tool_infos（触发 MCP 工具添加到 _tools）
        await self.ability_manager.list_tool_info()

        # 验证 MCP 工具被添加到 _tools
        self.assertEqual(len(self.ability_manager._tools), 2)
        self.assertIn("tool1", self.ability_manager._tools)
        self.assertIn("tool2", self.ability_manager._tools)

        # 删除 MCP 服务器
        result = self.ability_manager.remove("test_mcp")

        # 验证 MCP 服务器被删除
        self.assertIsNotNone(result)
        self.assertEqual(result.server_name, "test_mcp")

        # 验证对应的 MCP 工具也被删除
        self.assertEqual(len(self.ability_manager._tools), 0)
        self.assertNotIn("tool1", self.ability_manager._tools)
        self.assertNotIn("tool2", self.ability_manager._tools)


class TestAbilityManagerAgentCardInputParams(unittest.IsolatedAsyncioTestCase):
    """测试 AbilityManager 处理 AgentCard input_params 的 bug 修复 (Issue #518)"""

    def setUp(self):
        """设置测试环境"""
        from openjiuwen.core.single_agent import AbilityManager
        self.ability_manager = AbilityManager()

    async def test_agent_card_with_json_schema_dict(self):
        """测试 AgentCard 的 input_params 为 JSON Schema dict 时能正确转换"""
        from pydantic import BaseModel

        # 创建一个带有 JSON Schema dict 的 AgentCard
        agent_card = AgentCard(
            name="sub_agent",
            description="子 Agent",
            input_params={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户输入信息"
                    },
                    "context": {
                        "type": "string",
                        "description": "上下文信息"
                    }
                },
                "required": ["query"]
            }
        )

        self.ability_manager.add(agent_card)

        # 获取 tool_infos，应该不会抛出 AttributeError
        tool_infos = await self.ability_manager.list_tool_info()

        # 验证结果
        self.assertEqual(len(tool_infos), 1)
        self.assertEqual(tool_infos[0].name, "sub_agent")
        self.assertEqual(tool_infos[0].description, "子 Agent")

        # 验证 parameters 正确
        params = tool_infos[0].parameters
        self.assertIn("type", params)
        self.assertEqual(params["type"], "object")
        self.assertIn("properties", params)
        self.assertIn("query", params["properties"])
        self.assertIn("context", params["properties"])
        self.assertIn("required", params)
        self.assertIn("query", params["required"])

    async def test_agent_card_with_basemodel_type(self):
        """测试 AgentCard 的 input_params 为 BaseModel 类型时能正确转换"""
        from pydantic import BaseModel, Field

        # 定义一个 Pydantic BaseModel
        class AgentInputParams(BaseModel):
            query: str = Field(description="用户输入信息")
            context: str = Field(default="", description="上下文信息")

        # 创建一个带有 BaseModel 类型的 AgentCard
        agent_card = AgentCard(
            name="sub_agent",
            description="子 Agent",
            input_params=AgentInputParams
        )

        self.ability_manager.add(agent_card)

        # 获取 tool_infos，应该不会抛出异常
        tool_infos = await self.ability_manager.list_tool_info()

        # 验证结果
        self.assertEqual(len(tool_infos), 1)
        self.assertEqual(tool_infos[0].name, "sub_agent")
        self.assertEqual(tool_infos[0].description, "子 Agent")

        # 验证 parameters 正确（应该是 model_json_schema() 的结果）
        params = tool_infos[0].parameters
        self.assertIn("type", params)
        self.assertEqual(params["type"], "object")
        self.assertIn("properties", params)
        self.assertIn("query", params["properties"])
        self.assertIn("context", params["properties"])

    async def test_agent_card_with_none_input_params(self):
        """测试 AgentCard 的 input_params 为 None 时能正确处理"""
        # 创建一个 input_params 为 None 的 AgentCard
        agent_card = AgentCard(
            name="sub_agent",
            description="子 Agent",
            input_params=None
        )

        self.ability_manager.add(agent_card)

        # 获取 tool_infos，应该不会抛出异常
        tool_infos = await self.ability_manager.list_tool_info()

        # 验证结果
        self.assertEqual(len(tool_infos), 1)
        self.assertEqual(tool_infos[0].name, "sub_agent")
        self.assertEqual(tool_infos[0].description, "子 Agent")

        # 验证 parameters 为默认 JSON Schema
        params = tool_infos[0].parameters
        expected_params = {"type": "object", "properties": {}, "required": []}
        self.assertEqual(params, expected_params)

    async def test_multiple_agent_cards_with_different_input_params(self):
        """测试多个 AgentCard 混合不同类型的 input_params"""
        from pydantic import BaseModel, Field

        # 定义一个 Pydantic BaseModel
        class AgentInputParams(BaseModel):
            query: str = Field(description="查询内容")

        # 创建三个不同类型的 AgentCard
        agent_card1 = AgentCard(
            name="agent1",
            description="Agent with JSON Schema",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询"}
                },
                "required": ["query"]
            }
        )

        agent_card2 = AgentCard(
            name="agent2",
            description="Agent with BaseModel",
            input_params=AgentInputParams
        )

        agent_card3 = AgentCard(
            name="agent3",
            description="Agent with None",
            input_params=None
        )

        self.ability_manager.add([agent_card1, agent_card2, agent_card3])

        # 获取 tool_infos，应该不会抛出异常
        tool_infos = await self.ability_manager.list_tool_info()

        # 验证结果
        self.assertEqual(len(tool_infos), 3)

        # 验证每个 Agent 都被正确转换
        names = [t.name for t in tool_infos]
        self.assertIn("agent1", names)
        self.assertIn("agent2", names)
        self.assertIn("agent3", names)

        # 验证各自的 parameters
        for tool_info in tool_infos:
            if tool_info.name == "agent1":
                self.assertIn("properties", tool_info.parameters)
                self.assertIn("query", tool_info.parameters["properties"])
            elif tool_info.name == "agent2":
                self.assertIn("properties", tool_info.parameters)
                self.assertIn("query", tool_info.parameters["properties"])
            elif tool_info.name == "agent3":
                expected_params = {"type": "object", "properties": {}, "required": []}
                self.assertEqual(tool_info.parameters, expected_params)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
