# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
新版 ReActAgent 系统测试（使用真实大模型）

本测试用例使用真实的 SiliconFlow API 测试新版 ReActAgent，
验证在真实环境下的工具调用和对话能力。

## 测试场景

1. 纯对话场景（不调用工具）
2. 工具调用场景（调用加法工具计算）

## API 配置

- Provider: SiliconFlow
- Model: Qwen/Qwen3-32B

## 使用说明

- 工具先通过 Runner.resource_mgr.add_tool() 注册
- 再通过 agent.add_ability() 添加工具能力
- 最后调用 agent.invoke()，传入真实的 Session
"""
import os
import unittest

import pytest

from openjiuwen.core.single_agent.agents.react_agent import (
    ReActAgent,
    ReActAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.runner import Runner

# API 配置
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# 禁用 SSL 验证
os.environ.setdefault("LLM_SSL_VERIFY", "false")


class TestNewReActAgentReal(unittest.IsolatedAsyncioTestCase):
    """新版 ReActAgent 系统测试（使用真实大模型）"""

    async def asyncSetUp(self):
        """启动 Runner"""
        await Runner.start()

    async def asyncTearDown(self):
        """停止 Runner"""
        await Runner.stop()

    def _create_card(self, name: str, description: str) -> AgentCard:
        """创建 AgentCard"""
        return AgentCard(
            name=name,
            description=description
        )

    def _create_config(
        self,
        system_prompt: str = "你是一个智能助手。请简洁回答问题。"
    ) -> ReActAgentConfig:
        """创建 Agent 配置"""
        return (
            ReActAgentConfig()
            .configure_model_client(
                provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                model_name=MODEL_NAME,
                verify_ssl=False
            )
            .configure_prompt_template([
                {"role": "system", "content": system_prompt}
            ])
            .configure_max_iterations(5)
        )

    def _create_add_tool(self) -> LocalFunction:
        """创建加法工具"""
        return LocalFunction(
            card=ToolCard(
                name="add",
                description="加法运算，计算两个数的和",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"description": "第一个加数", "type": "number"},
                        "b": {"description": "第二个加数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )

    @unittest.skip("skip system test")
    @pytest.mark.asyncio
    async def test_pure_conversation_without_tools(self):
        """测试纯对话场景（不调用工具）

        场景：用户问一个简单问题，Agent 直接回答，不需要调用任何工具
        """
        # 1. 创建 Agent
        card = self._create_card(
            name="chat_agent",
            description="纯对话测试 Agent"
        )
        config = self._create_config()

        agent = ReActAgent(card=card)
        agent.configure(config)

        # 2. 创建真实的 Session
        session = create_agent_session(session_id="test_chat_session")

        # 3. 调用 Agent
        result = await agent.invoke(
            {"query": "你好，请用一句话介绍你自己"},
            session=session
        )

        # 4. 验证结果
        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')
        self.assertIn('output', result)
        self.assertGreater(len(result['output']), 0)

    @unittest.skip("skip system test")
    @pytest.mark.asyncio
    async def test_tool_call_with_add(self):
        """测试工具调用场景（调用加法工具）

        场景：用户请求计算，Agent 必须调用 add 工具完成计算
        """
        # 1. 创建工具
        add_tool = self._create_add_tool()

        # 2. 注册工具到 Runner.resource_mgr（在创建 Agent 之前）
        Runner.resource_mgr.add_tool(add_tool)

        # 3. 创建 Agent
        card = self._create_card(
            name="calc_agent",
            description="计算测试 Agent"
        )
        config = self._create_config(
            system_prompt=(
                "你是一个数学计算助手。"
                "你必须使用 add 工具来完成所有加法运算，禁止直接计算。"
                "即使是简单的加法，也必须调用 add 工具。"
            )
        )

        agent = ReActAgent(card=card)
        agent.configure(config)

        # 4. 添加工具到 Agent 的能力列表
        agent.add_ability(add_tool.card)

        # 5. 创建真实的 Session
        session = create_agent_session(session_id="test_calc_session")

        # 6. 调用 Agent，使用更明确的 query
        query = "使用 add 工具计算 123 + 456 的结果"
        result = await agent.invoke(
            {"query": query},
            session=session
        )

        # 7. 验证结果
        self.assertIsInstance(result, dict)
        self.assertEqual(result['result_type'], 'answer')
        self.assertIn('output', result)
        # 结果中应该包含 579（123 + 456 = 579）
        # 这个数字不太可能被 LLM 直接猜出来，必须调用工具
        self.assertIn('579', result['output'])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
