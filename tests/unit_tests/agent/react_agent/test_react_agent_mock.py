# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
使用 Mock 大模型测试 ReAct Agent 功能

本测试用例通过模拟大模型返回来提升测试速度和稳定性。
直接调用 agent.invoke() 方法，不依赖 Runner。

## 测试场景

1. 基本工具调用：用户输入 "计算1+2"，Agent 调用 add 工具
2. 多轮工具调用：连续调用多个工具完成复杂任务
3. 纯对话：无工具调用的简单对话

## Mock 策略

- 使用共享的 `MockLLMModel` 类
- 预定义 LLM 调用的返回值（按调用顺序）
- 通过 `patch` ModelFactory.get_model 来注入 mock 实例

## 优势

- **速度快**: 不需要真实的网络请求
- **稳定性高**: 不受网络波动和 API 限流影响
- **可预测**: 大模型返回固定，测试结果可重现
- **无依赖**: 不需要真实的 API Key 和网络连接
"""
import os
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent import create_react_agent_config, ReActAgent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


class TestReActAgentMock(unittest.IsolatedAsyncioTestCase):
    """测试 ReAct Agent 功能（使用 Mock LLM，不使用 Runner）"""

    @staticmethod
    def _create_model():
        """创建模型配置"""
        return ModelConfig(
            model_provider="openai",
            model_info=BaseModelInfo(
                model="gpt-3.5-turbo",
                api_base="mock_url",
                api_key="mock_key",
                temperature=0.7,
                top_p=0.9,
                timeout=30
            )
        )

    @staticmethod
    def _create_add_tool():
        """创建加法工具"""
        return LocalFunction(
            card=ToolCard(
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
            ),
            func=lambda a, b: a + b,
        )

    @staticmethod
    def _create_multiply_tool():
        """创建乘法工具"""
        return LocalFunction(
            card=ToolCard(
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
            ),
            func=lambda a, b: a * b,
        )

    @staticmethod
    def _create_prompt_template():
        """创建提示模板"""
        return [
            dict(
                role="system",
                content="你是一个数学计算助手，在适当的时候调用工具来完成计算任务。"
            )
        ]

    @pytest.mark.asyncio
    async def test_react_agent_invoke_with_mock_llm(self):
        """测试 ReAct Agent 使用 Mock LLM 调用工具

        测试场景：
        1. 用户输入 "计算1+2"
        2. ReAct Agent 调用 LLM，决定调用 add 工具
        3. add 工具执行，返回 3
        4. ReAct Agent 再次调用 LLM，返回最终答案
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("根据计算结果，1+2=3"),
        ])

        with patch(
            "openjiuwen.core.foundation.llm.model_utils.model_factory."
            "ModelFactory.get_model"
        ) as mock_get_model:
            mock_get_model.return_value = mock_llm

            add_tool = self._create_add_tool()

            react_agent_config = create_react_agent_config(
                agent_id="react_agent_mock_test",
                agent_version="0.0.1",
                description="数学计算助手",
                model=self._create_model(),
                prompt_template=self._create_prompt_template()
            )

            react_agent = ReActAgent(react_agent_config)
            react_agent.add_tools([add_tool])

            result = await react_agent.invoke(
                {"conversation_id": "test_session", "query": "计算1+2"}
            )

            self.assertIsInstance(result, dict, "应该返回字典")
            self.assertEqual(
                result['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertIn('output', result, "结果应该包含 output 字段")
            self.assertIn('3', result['output'], "答案应该包含计算结果3")
            self.assertEqual(mock_llm.call_count, 2, "Mock LLM 应该被调用2次")

    @pytest.mark.asyncio
    async def test_react_agent_multi_turn_tool_calls(self):
        """测试 ReAct Agent 多轮工具调用

        测试场景：
        1. 用户输入 "计算 (1+2) * 3"
        2. Agent 先调用 add(1, 2) 得到 3
        3. Agent 再调用 multiply(3, 3) 得到 9
        4. Agent 返回最终答案
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_tool_call_response("multiply", '{"a": 3, "b": 3}'),
            create_text_response("计算结果：(1+2) * 3 = 9"),
        ])

        with patch(
            "openjiuwen.core.foundation.llm.model_utils.model_factory."
            "ModelFactory.get_model"
        ) as mock_get_model:
            mock_get_model.return_value = mock_llm

            add_tool = self._create_add_tool()
            multiply_tool = self._create_multiply_tool()

            react_agent_config = create_react_agent_config(
                agent_id="react_agent_multi_turn",
                agent_version="0.0.1",
                description="数学计算助手",
                model=self._create_model(),
                prompt_template=self._create_prompt_template()
            )

            react_agent = ReActAgent(react_agent_config)
            react_agent.add_tools([add_tool, multiply_tool])

            result = await react_agent.invoke(
                {"conversation_id": "test_multi_turn", "query": "计算 (1+2) * 3"}
            )

            self.assertIsInstance(result, dict, "应该返回字典")
            self.assertEqual(
                result['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertIn('9', result['output'], "答案应该包含计算结果9")
            self.assertEqual(mock_llm.call_count, 3, "Mock LLM 应该被调用3次")

    @pytest.mark.asyncio
    async def test_react_agent_pure_conversation(self):
        """测试 ReAct Agent 纯对话（无工具调用）

        测试场景：
        1. 用户输入 "你好"
        2. Agent 直接返回问候语，不调用任何工具
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("你好！我是数学计算助手，有什么可以帮助你的吗？"),
        ])

        with patch(
            "openjiuwen.core.foundation.llm.model_utils.model_factory."
            "ModelFactory.get_model"
        ) as mock_get_model:
            mock_get_model.return_value = mock_llm

            add_tool = self._create_add_tool()

            react_agent_config = create_react_agent_config(
                agent_id="react_agent_conversation",
                agent_version="0.0.1",
                description="数学计算助手",
                model=self._create_model(),
                prompt_template=self._create_prompt_template()
            )

            react_agent = ReActAgent(react_agent_config)
            react_agent.add_tools([add_tool])

            result = await react_agent.invoke(
                {"conversation_id": "test_conversation", "query": "你好"}
            )

            self.assertIsInstance(result, dict, "应该返回字典")
            self.assertEqual(
                result['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertIn('你好', result['output'], "答案应该包含问候语")
            self.assertEqual(mock_llm.call_count, 1, "Mock LLM 应该只被调用1次")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
