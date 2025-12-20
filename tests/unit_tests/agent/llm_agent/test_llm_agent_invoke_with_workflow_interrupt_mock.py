"""
使用 Mock 大模型测试 LLM Agent 与 Workflow 中断恢复功能

本测试用例是基于系统测试 test_react_agent_invoke_with_workflow_interrupt_agent_invoke 
创建的 Mock 版本，通过模拟大模型返回来提升测试速度。

## 测试场景

1. 用户输入 "今天天气查询"
2. ReAct Agent 调用 LLM，决定调用 questioner_weather_workflow
3. Workflow 中的 Questioner 组件提取字段时，发现缺少 location 信息
4. Questioner 组件触发交互请求，询问用户提供地点
5. 用户提供 "上海"
6. ReAct Agent 再次调用 LLM，决定恢复 workflow
7. Questioner 组件从 "上海" 中提取 location 字段
8. Workflow 完成，返回 "上海 | today"
9. ReAct Agent 第三次调用 LLM，返回最终答案给用户

## Mock 策略

- 使用 `MockLLMModel` 类继承 `BaseModelClient`，实现所有必要的方法
- 预定义 5 次 LLM 调用的返回值（按调用顺序）
- 通过 `patch` ModelFactory.get_model 来注入 mock 实例
- 所有组件（ReAct Agent 和 Questioner）共享同一个 mock LLM 实例

## 优势

相比系统测试，Mock 版本具有以下优势：
- **速度快**: 不需要真实的网络请求，测试时间从 55 秒降低到 2 秒
- **稳定性高**: 不受网络波动和 API 限流影响
- **可预测**: 大模型返回固定，测试结果可重现
- **无依赖**: 不需要真实的 API Key 和网络连接
"""
import os
import unittest
from datetime import datetime
from typing import List, Any, Union, AsyncIterator, Dict, Iterator
from unittest.mock import patch, AsyncMock, Mock

import pytest

from openjiuwen.agent.common.schema import PluginSchema, WorkflowSchema
from openjiuwen.agent.llm_agent import create_llm_agent_config, create_llm_agent, LLMAgent
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.resources_manager.workflow_manager import generate_workflow_key
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.utils.llm.base import BaseModelInfo, BaseModelClient
from openjiuwen.core.utils.llm.messages import AIMessage, UsageMetadata
from openjiuwen.core.utils.tool.schema import ToolCall
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata, WorkflowInputsSchema
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.component.questioner_comp import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.core.runner.runner import Runner, resource_mgr


def build_current_date():
    """构建当前日期字符串"""
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


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


class TestReActAgentWithWorkflowInterruptMock(unittest.IsolatedAsyncioTestCase):
    """测试 ReAct Agent 与 Workflow 中断恢复功能（使用 Mock LLM）"""
    
    async def asyncSetUp(self):
        """测试前准备"""
        await Runner.start()
        
    async def asyncTearDown(self):
        """测试后清理"""
        await Runner.stop()
    
    @staticmethod
    def _create_model():
        """创建模型配置"""
        return ModelConfig(
            model_provider="openai",
            model_info=BaseModelInfo(
                model="gpt-3.5-turbo",
                api_base="https://api.openai.com",
                api_key="mock_key",
                temperature=0.7,
                top_p=0.9,
                timeout=30
            )
        )
    
    @staticmethod
    def _create_prompt_template():
        """创建提示模板"""
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]
    
    @pytest.mark.asyncio
    async def test_react_agent_invoke_with_workflow_interrupt_mock(self):
        """测试 ReAct Agent 调用 Workflow 并处理中断（Mock 版本）
        
        测试场景：
        1. 用户输入"今天天气查询"
        2. ReAct Agent 调用 LLM，决定调用 questioner_weather_workflow
        3. Workflow 中的 Questioner 组件请求用户提供地点信息（中断）
        4. 用户提供"上海"
        5. ReAct Agent 通过InteractiveInput，决定恢复 workflow
        6. Workflow 完成，返回"上海 | today"
        7. ReAct Agent 第三次调用 LLM，返回最终答案
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        
        # ==================== 准备 Mock LLM ====================
        # 创建一个共享的 mock LLM 实例，所有组件都使用它
        mock_llm = MockLLMModel(api_key="mock_key", api_base="https://api.openai.com")
        
        # 定义所有 LLM 调用的返回值（按调用顺序）
        # 注意：恢复 workflow 时直接使用 InteractiveInput，不会再次调用 ReAct Agent 的 LLM
        all_llm_responses = [
            # 第1次调用：ReAct Agent 决定调用 workflow
            AIMessage(
                content='',
                tool_calls=[
                    ToolCall(
                        id='call_weather_001',
                        type='function',
                        name='questioner_weather_workflow',
                        arguments='{"query": "今天天气查询"}'
                    )
                ],
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='tool_calls',
                    prompt_tokens=150,
                    completion_tokens=25
                )
            ),
            # 第2次调用：Questioner 组件提取字段，location为null，触发交互
            AIMessage(
                content='{\n  "location": null,\n  "time": "today"\n}',
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='stop'
                )
            ),
            # 第3次调用：Questioner 组件恢复时，从"上海"中提取地点
            # （恢复 workflow 时直接使用 InteractiveInput，跳过 ReAct Agent 的 LLM 调用）
            AIMessage(
                content='{\n  "location": "上海",\n  "time": "today"\n}',
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='stop'
                )
            ),
            # 第4次调用：ReAct Agent workflow 完成后，返回最终答案
            AIMessage(
                content='我已经为您查询了上海的天气信息。根据返回的结果：上海 | today，这表明查询已成功完成。如果需要更详细的天气数据，请告诉我。',
                usage_metadata=UsageMetadata(
                    model_name='gpt-3.5-turbo',
                    finish_reason='stop'
                )
            ),
        ]
        
        mock_llm.set_responses(all_llm_responses)
        
        # ==================== 使用 Patch Mock LLM（在创建组件之前开始 patch）====================
        with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
            # 所有组件共享同一个 mock LLM 实例
            mock_get_model.return_value = mock_llm
            
            # ==================== 构建 Workflow ====================
            react_agent_prompt_template = self._create_prompt_template()
            
            questioner_workflow_config = WorkflowConfig(
                metadata=WorkflowMetadata(
                    name="questioner_weather_workflow",
                    id="questioner_weather_workflow",
                    version="1.0",
                    description="天气查询"
                ),
                workflow_inputs_schema=WorkflowInputsSchema(
                    type="object",
                    properties={
                        "query": {
                            "type": "string",
                            "description": "天气查询用户输入",
                            "required": True
                        }
                    },
                    required=['query']
                )
            )
            
            flow = Workflow(workflow_config=questioner_workflow_config)
            
            key_fields = [
                FieldInfo(field_name="location", description="地点", required=True),
                FieldInfo(field_name="time", description="时间", required=True, default_value="today")
            ]
            
            start_component = Start({
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            })
            end_component = End({"responseTemplate": "{{location}} | {{time}}"})
            
            model_config = self._create_model()
            questioner_config = QuestionerConfig(
                model=model_config,
                question_content="",
                extract_fields_from_response=True,
                field_names=key_fields,
                with_chat_history=False
            )
            questioner_component = QuestionerComponent(questioner_comp_config=questioner_config)
            
            flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
            flow.set_end_comp("e", end_component,
                              inputs_schema={"location": "${questioner.location}", "time": "${questioner.time}"})
            flow.add_workflow_comp("questioner", questioner_component, inputs_schema={"query": "${s.query}"})
            
            flow.add_connection("s", "questioner")
            flow.add_connection("questioner", "e")
            
            workflow_schema = WorkflowSchema(
                id=flow.config().metadata.id,
                name=flow.config().metadata.name,
                version=flow.config().metadata.version,
                description="追问器工作流",
                inputs={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "用户输入",
                            "required": True
                        }
                    }
                }
            )
            
            # ==================== 创建 ReAct Agent ====================
            react_agent_config = create_llm_agent_config(
                agent_id="react_agent_123",
                agent_version="0.0.1",
                description="AI助手",
                plugins=[],
                workflows=[workflow_schema],
                model=model_config,
                prompt_template=react_agent_prompt_template
            )
            
            react_agent: LLMAgent = create_llm_agent(
                agent_config=react_agent_config,
                workflows=[flow],
                tools=[]
            )
            
            # 绑定 workflow
            resource_mgr.workflow().add_workflow(
                generate_workflow_key(flow.config().metadata.id, flow.config().metadata.version), 
                flow
            )
            
            # ==================== 第一次调用：触发中断 ====================
            result = await Runner.run_agent(
                react_agent, 
                {"conversation_id": "12345", "query": "今天天气查询"}
            )
            
            print(f"LLMAgent 第一次输出结果：{result}")
            
            # 验证第一次调用返回交互请求
            self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
            self.assertGreater(len(result), 0, "交互请求列表不应为空")
            self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
            self.assertEqual(result[0].payload.id, 'questioner', "交互请求应该来自 questioner 组件")
            print(f"✅ 第一次调用校验通过：返回交互请求，提示：{result[0].payload.value}")
            
            # ==================== 第二次调用：恢复并完成 ====================
            if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
                interactive_input = InteractiveInput()
                interactive_input.update("questioner", "上海")
                
                result = await Runner.run_agent(
                    react_agent, 
                    {"conversation_id": "12345", "query": interactive_input}
                )
                
                print(f"LLMAgent 第二次输出结果：{result}")
                
                # 验证第二次调用返回最终答案
                self.assertIsInstance(result, dict, "第二次调用应该返回字典")
                self.assertEqual(result['result_type'], 'answer', "应该返回 answer 类型")
                self.assertIn('output', result, "结果应该包含 output 字段")
                print(f"✅ 第二次调用校验通过：工作流完成，返回最终答案：{result['output']}")
                
                # 验证答案内容
                self.assertIn('上海', result['output'], "答案应该包含地点信息")
                print(f"✅ 答案内容校验通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

