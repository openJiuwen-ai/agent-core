"""
使用 Mock 大模型测试 ReAct Agent 功能（不使用 Runner）

本测试用例通过模拟大模型返回来提升测试速度和稳定性。
直接调用 agent.invoke() 方法，不依赖 Runner。

## 测试场景

1. 用户输入 "计算1+2"
2. ReAct Agent 调用 LLM，决定调用 add 工具
3. add 工具执行，返回 3
4. ReAct Agent 再次调用 LLM，返回最终答案

## Mock 策略

- 使用 `MockLLMModel` 类继承 `BaseChatModel`，实现所有必要的方法
- 预定义 2 次 LLM 调用的返回值（按调用顺序）
- 通过 `patch` ModelFactory.get_model 来注入 mock 实例
- 直接调用 agent.invoke()，不使用 Runner

## 优势

相比系统测试，Mock 版本具有以下优势：
- **速度快**: 不需要真实的网络请求
- **稳定性高**: 不受网络波动和 API 限流影响
- **可预测**: 大模型返回固定，测试结果可重现
- **无依赖**: 不需要真实的 API Key、网络连接和 Runner
"""
import os
import unittest
from typing import List, Any, Dict, Iterator, AsyncIterator
from unittest.mock import patch

import pytest

from openjiuwen.agent.react_agent.react_agent import create_react_agent_config, ReActAgent
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.base import BaseModelInfo, BaseModelClient
from openjiuwen.core.utils.llm.messages import AIMessage, UsageMetadata
from openjiuwen.core.utils.tool.schema import ToolCall
from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param


class MockLLMModel(BaseModelClient):
    """Mock 大模型，返回预定义的响应"""
    
    def __init__(self, api_key: str, api_base: str, **kwargs):
        super().__init__(api_key=api_key, api_base=api_base)
        self.call_count = 0
        self.responses = []
        
    def set_responses(self, responses: List[AIMessage]):
        """设置预定义的响应序列"""
        self.responses = responses
        self.call_count = 0
    
    def _get_next_response(self) -> AIMessage:
        """获取下一个响应"""
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        else:
            # 默认响应
            return AIMessage(content="这是默认响应")
    
    def _invoke(
        self, 
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> AIMessage:
        """同步调用"""
        return self._get_next_response()
        
    async def _ainvoke(
        self, 
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> AIMessage:
        """异步调用"""
        return self._get_next_response()
    
    def _stream(
        self,
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> Iterator[Any]:
        """流式返回"""
        result = self._get_next_response()
        yield result
    
    async def _astream(
        self,
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> AsyncIterator[Any]:
        """异步流式返回"""
        result = self._get_next_response()
        yield result


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
    def _create_function_tool():
        """创建 LocalFunction 工具 - 加法"""
        add_plugin = LocalFunction(
            name="add",
            description="加法运算",
            params=[
                Param(name="a", description="第一个加数", type="number", required=True),
                Param(name="b", description="第二个加数", type="number", required=True),
            ],
            func=lambda a, b: a + b
        )
        return add_plugin
    
    @staticmethod
    def _create_prompt_template():
        """创建提示模板"""
        system_prompt = "你是一个数学计算助手，在适当的时候调用工具来完成计算任务。"
        return [
            dict(role="system", content=system_prompt)
        ]
    
    @pytest.mark.asyncio
    async def test_react_agent_invoke_with_mock_llm(self):
        """测试 ReAct Agent 使用 Mock LLM 调用工具（不使用 Runner）
        
        测试场景：
        1. 用户输入 "计算1+2"
        2. ReAct Agent 调用 LLM，决定调用 add 工具
        3. add 工具执行，返回 3
        4. ReAct Agent 再次调用 LLM，返回最终答案
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        
        # ==================== 准备 Mock LLM ====================
        mock_llm = MockLLMModel(api_key="mock_key", api_base="mock_url")
        
        # 定义所有 LLM 调用的返回值（按调用顺序）
        all_llm_responses = [
            # 第1次调用：ReAct Agent 决定调用 add 工具
            AIMessage(
                content='',
                tool_calls=[
                    ToolCall(
                        id='mock_tool_call_1',
                        type='function',
                        name='add',
                        arguments='{"a": 1, "b": 2}'
                    )
                ],
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='tool_calls'
                )
            ),
            # 第2次调用：ReAct Agent 返回最终答案
            AIMessage(
                content='根据计算结果，1+2=3',
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='stop'
                )
            ),
        ]
        
        mock_llm.set_responses(all_llm_responses)
        
        # ==================== 使用 Patch Mock LLM ====================
        with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
            mock_get_model.return_value = mock_llm
            
            # ==================== 创建工具 ====================
            add_tool = self._create_function_tool()
            
            # ==================== 创建 ReAct Agent ====================
            react_agent_config = create_react_agent_config(
                agent_id="react_agent_mock_test",
                agent_version="0.0.1",
                description="数学计算助手",
                model=self._create_model(),
                prompt_template=self._create_prompt_template()
            )
            
            react_agent: ReActAgent = ReActAgent(react_agent_config)
            react_agent.add_tools([add_tool])
            
            # ==================== 直接调用 agent.invoke() ====================
            result = await react_agent.invoke(
                {"conversation_id": "test_session", "query": "计算1+2"}
            )
            
            print(f"ReActAgent 输出结果：{result}")
            
            # ==================== 验证结果 ====================
            self.assertIsInstance(result, dict, "应该返回字典")
            self.assertEqual(result['result_type'], 'answer', "应该返回 answer 类型")
            self.assertIn('output', result, "结果应该包含 output 字段")
            print(f"✅ 测试通过：返回最终答案：{result['output']}")
            
            # 验证答案内容
            self.assertIn('3', result['output'], "答案应该包含计算结果3")
            print(f"✅ 答案内容校验通过")
            
            # 验证 Mock LLM 被调用了 2 次
            self.assertEqual(mock_llm.call_count, 2, "Mock LLM 应该被调用2次")
            print(f"✅ Mock LLM 调用次数校验通过：{mock_llm.call_count} 次")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

