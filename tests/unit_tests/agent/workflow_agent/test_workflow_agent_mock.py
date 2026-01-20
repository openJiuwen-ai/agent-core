# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
使用 Mock 大模型测试 Workflow Agent 功能

本测试用例通过模拟大模型返回来提升测试速度和稳定性。

## 测试场景

1. 基本 workflow 执行：简单的 start -> node -> end 流程
2. workflow 中断：Questioner 组件触发中断
3. workflow 中断恢复：使用 InteractiveInput 恢复中断的 workflow
4. 多 workflow 切换：Agent 包含多个 workflow，根据意图切换

## Mock 策略

- 使用共享的 `MockLLMModel` 类
- 对于不需要 LLM 的简单 workflow，使用 mock 节点
- 对于需要 LLM 的 Questioner 组件，mock LLM 返回字段提取结果
"""
import os
import unittest
from typing import List
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.application.workflow_agent import (
    WorkflowAgent
)
from openjiuwen.core.workflow import WorkflowCard, Workflow, Start, End
from openjiuwen.core.workflow import (
    QuestionerComponent,
    QuestionerConfig,
    FieldInfo
)
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_json_response,
    create_text_response,
)
from tests.unit_tests.core.workflow.mock_nodes import (
    MockStartNode,
    MockEndNode,
    Node1,
)


class TestWorkflowAgentMock(unittest.IsolatedAsyncioTestCase):
    """测试 Workflow Agent 功能（使用 Mock LLM）"""

    async def asyncSetUp(self):
        """测试前准备"""
        await Runner.start()

    async def asyncTearDown(self):
        """测试后清理"""
        await Runner.stop()

    @staticmethod
    def _create_model_config() -> ModelConfig:
        """创建模型配置"""
        return ModelConfig(
            model_provider="OpenAI",
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
    def _create_model_request_config() -> ModelRequestConfig:
        """创建模型配置"""
        return ModelRequestConfig(
            model="gpt-3.5-turbo",
            temperature=0.7,
            top_p=0.9
        )

    @staticmethod
    def _create_model_client_config() -> ModelClientConfig:
        """创建模型配置"""
        return ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-fake",
            api_base="https://api.openai.com/v1",
            timeout=30,
            max_retries=3,
            verify_ssl=False
        )

    @staticmethod
    def _build_simple_workflow(
        workflow_id: str,
        workflow_name: str,
        version: str = "1.0"
    ) -> Workflow:
        """构建简单的 workflow: start -> node -> end"""
        workflow_card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=workflow_name,
            description=f"Simple workflow: {workflow_name}",
        )
        flow = Workflow(card=workflow_card)

        flow.set_start_comp(
            "start",
            MockStartNode("start"),
            inputs_schema={"query": "${query}"}
        )
        flow.add_workflow_comp(
            "node_a",
            Node1("node_a"),
            inputs_schema={"output": "${start.query}"}
        )
        flow.set_end_comp(
            "end",
            MockEndNode("end"),
            inputs_schema={"result": "${node_a.output}"}
        )

        flow.add_connection("start", "node_a")
        flow.add_connection("node_a", "end")

        return flow

    def _build_questioner_workflow(
        self,
        workflow_id: str,
        workflow_name: str,
        field_name: str,
        field_desc: str,
        version: str = "1.0"
    ) -> Workflow:
        """构建带 Questioner 中断组件的 workflow"""
        workflow_card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=workflow_name,
            description=f"Questioner workflow: {workflow_name}",
        )
        flow = Workflow(card=workflow_card)

        # Start 组件
        start = Start()

        # Questioner 组件（会触发中断）
        key_fields = [
            FieldInfo(
                field_name=field_name,
                description=field_desc,
                required=True
            ),
        ]
        questioner_config = QuestionerConfig(
            model_config=self._create_model_request_config(),
            model_client_config=self._create_model_client_config(),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({
            "responseTemplate": f"{workflow_name}完成: {{{{{field_name}}}}}"
        })

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end",
            end,
            inputs_schema={field_name: f"${{questioner.{field_name}}}"}
        )

        # 连接拓扑
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    @pytest.mark.asyncio
    async def test_workflow_agent_basic_execution(self):
        """测试 Workflow Agent 基本执行

        测试场景：
        1. 创建简单的 workflow (start -> node -> end)
        2. 调用 WorkflowAgent.invoke()
        3. 验证 workflow 正常完成
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # 构建 workflow
        workflow = self._build_simple_workflow(
            workflow_id="simple_workflow",
            workflow_name="简单工作流"
        )

        # 创建 workflow schema
        workflow_schema = WorkflowSchema(
            id="simple_workflow",
            version="1.0",
            name="简单工作流",
            description="简单工作流测试",
            inputs={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户输入"}
                }
            }
        )

        # 创建 WorkflowAgent
        workflow_config = WorkflowAgentConfig(
            workflows=[workflow_schema],
            controller_type=ControllerType.WorkflowController
        )
        agent = WorkflowAgent(workflow_config)
        agent.bind_workflows([workflow])

        # 执行
        result = await agent.invoke({"query": "hello"})

        # 验证
        self.assertEqual(result['result_type'], 'answer', "应该返回 answer 类型")
        self.assertEqual(
            result['output'].state.name, 'COMPLETED', "workflow 应该完成"
        )

    @pytest.mark.asyncio
    async def test_workflow_agent_with_interrupt(self):
        """测试 Workflow Agent 中断

        测试场景：
        1. 创建带 Questioner 的 workflow
        2. Questioner 提取字段时发现缺少必要信息
        3. 触发中断，返回交互请求
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        # Questioner 提取字段，location 为 null，触发中断
        mock_llm.set_responses([
            create_json_response({"location": None}),
        ])

        with patch(
            'openjiuwen.core.foundation.llm.model.Model.invoke',
            side_effect=mock_llm.invoke
        ), patch(
            'openjiuwen.core.foundation.llm.model.Model.stream',
            side_effect=mock_llm.stream
        ), patch(
            "openjiuwen.core.memory.long_term_memory."
            "LongTermMemory.set_scope_config",
            return_value=MagicMock()
        ):
            # 构建 workflow
            workflow = self._build_questioner_workflow(
                workflow_id="location_workflow",
                workflow_name="地点查询",
                field_name="location",
                field_desc="地点名称"
            )

            # 创建 workflow schema
            workflow_schema = WorkflowSchema(
                id="location_workflow",
                version="1.0",
                name="地点查询",
                description="地点查询工作流",
                inputs={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "用户输入"}
                    }
                }
            )

            # 创建 WorkflowAgent
            workflow_config = WorkflowAgentConfig(
                workflows=[workflow_schema],
                controller_type=ControllerType.WorkflowController
            )
            agent = WorkflowAgent(workflow_config)
            agent.bind_workflows([workflow])

            # 执行 - 应该触发中断
            result = await agent.invoke(
                {"conversation_id": "test_interrupt", "query": "查询天气"}
            )

            # 验证中断
            self.assertIsInstance(result, list, "应该返回交互请求列表")
            self.assertEqual(
                result[0].type, '__interaction__', "应该返回交互类型"
            )

    @pytest.mark.asyncio
    async def test_workflow_agent_interrupt_resume(self):
        """测试 Workflow Agent 中断恢复

        测试场景：
        1. 第一次调用触发中断
        2. 使用 InteractiveInput 提供缺失信息
        3. workflow 恢复并完成
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # 第一次：提取字段，location 为 null，触发中断
            create_json_response({"location": None}),
            # 第二次：恢复后提取字段，location 有值
            create_json_response({"location": "上海"}),
        ])

        with patch(
            'openjiuwen.core.foundation.llm.model.Model.invoke',
            side_effect=mock_llm.invoke
        ), patch(
            'openjiuwen.core.foundation.llm.model.Model.stream',
            side_effect=mock_llm.stream
        ), patch(
            "openjiuwen.core.memory.long_term_memory."
            "LongTermMemory.set_scope_config",
            return_value=MagicMock()
        ):
            # 构建 workflow
            workflow = self._build_questioner_workflow(
                workflow_id="location_workflow_resume",
                workflow_name="地点查询",
                field_name="location",
                field_desc="地点名称"
            )

            # 创建 workflow schema
            workflow_schema = WorkflowSchema(
                id="location_workflow_resume",
                version="1.0",
                name="地点查询",
                description="地点查询工作流",
                inputs={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "用户输入"}
                    }
                }
            )

            # 创建 WorkflowAgent
            workflow_config = WorkflowAgentConfig(
                workflows=[workflow_schema],
                controller_type=ControllerType.WorkflowController
            )
            agent = WorkflowAgent(workflow_config)
            agent.bind_workflows([workflow])

            # 第一次调用 - 触发中断
            result1 = await agent.invoke(
                {"conversation_id": "test_resume", "query": "查询天气"}
            )

            self.assertIsInstance(result1, list, "第一次应该返回交互请求列表")
            self.assertEqual(
                result1[0].type, '__interaction__', "应该返回交互类型"
            )

            # 第二次调用 - 使用 InteractiveInput 恢复
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "上海")

            result2 = await agent.invoke(
                {"conversation_id": "test_resume", "query": interactive_input}
            )

            # 验证完成
            self.assertIsInstance(result2, dict, "第二次应该返回字典")
            self.assertEqual(
                result2['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertEqual(
                result2['output'].state.name, 'COMPLETED', "workflow 应该完成"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
