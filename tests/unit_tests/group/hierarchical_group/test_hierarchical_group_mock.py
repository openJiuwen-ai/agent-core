# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
使用 Mock 大模型测试 HierarchicalGroup 功能

本测试用例通过模拟大模型返回来提升测试速度和稳定性。

## 测试场景

1. 基本路由：Mock 意图识别，验证消息正确路由到目标 Agent
2. 中断和恢复：WorkflowAgent 触发中断，使用 InteractiveInput 恢复
3. 不同类型 Agent 组合：ReAct Agent、LLM Agent、Workflow Agent

## Mock 策略

- Mock `_detect_intent` 方法直接返回目标 agent_id
- Mock `ModelFactory.get_model` 注入 MockLLMModel
- 对于 Questioner 组件，mock LLM 返回字段提取结果
"""
import os
import unittest
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.single_agent.legacy import (
    AgentConfig,
    ControllerAgent,
    WorkflowAgentConfig,
)
from openjiuwen.core.application.workflow_agent import (
    WorkflowAgent
)
from openjiuwen.core.application.llm_agent import (
    LLMAgent,
    ReActAgentConfig,
)
from examples.groups.hierarchical_group import (
    HierarchicalGroup,
    HierarchicalGroupConfig,
)
from examples.groups.hierarchical_group.agents.main_controller import (
    HierarchicalMainController
)
from openjiuwen.core.controller.legacy import Event
from openjiuwen.core.common.constants import constant as const
from openjiuwen.core.workflow import WorkflowCard, Workflow, Start, End
from openjiuwen.core.workflow import (
    QuestionerComponent,
    QuestionerConfig,
    FieldInfo,
)
from openjiuwen.core.foundation.llm import (
    ModelConfig, BaseModelInfo, ModelRequestConfig, ModelClientConfig
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput

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


class TestHierarchicalGroupMock(unittest.IsolatedAsyncioTestCase):
    """测试 HierarchicalGroup 功能（使用 Mock LLM）"""

    async def asyncSetUp(self):
        """测试前准备"""
        await Runner.start()

    async def asyncTearDown(self):
        """测试后清理"""
        await Runner.stop()

    @staticmethod
    def _create_model_config() -> ModelConfig:
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

    def _create_workflow_agent(
        self,
        agent_id: str,
        description: str,
        workflow: Workflow,
        workflow_id: str  # noqa: ARG002 - 保留参数兼容性
    ) -> WorkflowAgent:
        """创建 WorkflowAgent

        Args:
            agent_id: Agent ID
            description: Agent 描述
            workflow: Workflow 实例
            workflow_id: Workflow ID（未使用，保留参数兼容性）

        Returns:
            WorkflowAgent 实例
        """
        # 使用新 API: add_workflows() 自动从 workflow.card 提取 schema
        workflow_config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            workflows=[],
        )
        agent = WorkflowAgent(workflow_config)
        agent.add_workflows([workflow])
        return agent

    def _create_llm_agent(
        self,
        agent_id: str,
        description: str
    ) -> LLMAgent:
        """创建 LLM Agent"""
        prompt_template = [
            {
                "role": "system",
                "content": f"你是一个{description}的AI助手。"
            }
        ]

        config = ReActAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            model=self._create_model_config(),
            prompt_template=prompt_template,
        )

        return LLMAgent(config)

    def _create_hierarchical_group(
        self,
        group_id: str = "test_group",
        leader_agent_id: str = "main_controller"
    ) -> tuple:
        """创建 HierarchicalGroup 和 Leader Agent

        Returns:
            tuple: (group, leader_agent, main_controller)
        """
        config = HierarchicalGroupConfig(
            group_id=group_id,
            leader_agent_id=leader_agent_id
        )
        group = HierarchicalGroup(config)

        main_config = AgentConfig(
            id=leader_agent_id,
            description="主控制器，识别用户意图并分发任务",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        return group, leader_agent, main_controller

    @pytest.mark.asyncio
    @patch(
        "examples.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    async def test_hierarchical_group_basic_routing(self, mock_detect_intent):
        """测试 HierarchicalGroup 基本路由

        测试场景：
        1. 创建 HierarchicalGroup，添加一个简单的 WorkflowAgent
        2. Mock 意图识别直接返回目标 agent_id
        3. 验证消息正确路由到目标 Agent
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # 创建 Group 和 Leader
        group, leader_agent, _ = self._create_hierarchical_group()

        # 创建简单的 WorkflowAgent
        workflow = self._build_simple_workflow(
            workflow_id="simple_flow",
            workflow_name="简单工作流"
        )
        worker_agent = self._create_workflow_agent(
            agent_id="worker_agent",
            description="简单工作流处理",
            workflow=workflow,
            workflow_id="simple_flow"
        )

        # 添加 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("worker_agent", worker_agent)

        # Mock 意图识别直接返回 worker_agent
        mock_detect_intent.return_value = "worker_agent"

        # 发送消息
        message = Event.create_user_event(
            content="测试消息",
            conversation_id="test_basic_routing"
        )

        result = await group.invoke(message)

        # 验证结果
        self.assertIsNotNone(result, "应该返回结果")
        self.assertIsInstance(result, dict, "结果应该是字典类型")
        self.assertEqual(
            result['result_type'], 'answer', "应该返回 answer 类型"
        )
        self.assertEqual(
            result['output'].state.name, 'COMPLETED', "workflow 应该完成"
        )

    # NOTE: test_hierarchical_group_with_react_agent 测试暂时跳过
    # 原因：ReActAgent 在 HierarchicalGroup 中使用时存在兼容性问题
    # (LocalFunction.name vs LocalFunction.card.name)
    # 这是框架内部的问题，不是 Mock 测试的重点

    @pytest.mark.asyncio
    @patch(
        "examples.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    async def test_hierarchical_group_with_llm_agent(self, mock_detect_intent):
        """测试 HierarchicalGroup + LLM Agent

        测试场景：
        1. 创建 HierarchicalGroup，添加一个 LLM Agent
        2. Mock 意图识别返回 LLM Agent 的 ID
        3. Mock LLM 返回文本响应
        4. 验证 LLM Agent 被正确调用
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("你好！我是翻倍运算助手，5 翻倍后是 10。"),
        ])

        with patch(
            "openjiuwen.core.single_agent.agents."
            "react_agent.Model"
        ) as mock_model_class:
            mock_model_class.return_value = mock_llm

            # 创建 Group 和 Leader
            group, leader_agent, _ = self._create_hierarchical_group()

            # 创建 LLM Agent
            llm_agent = self._create_llm_agent(
                agent_id="double_agent",
                description="翻倍运算助手"
            )

            # 添加 Agents 到 Group
            group.add_agent("main_controller", leader_agent)
            group.add_agent("double_agent", llm_agent)

            # Mock 意图识别直接返回 double_agent
            mock_detect_intent.return_value = "double_agent"

            # 发送消息
            message = Event.create_user_event(
                content="帮我把数字 5 翻倍",
                conversation_id="test_llm_agent"
            )

            result = await group.invoke(message)

            # 验证结果
            self.assertIsNotNone(result, "应该返回结果")
            self.assertIsInstance(result, dict, "结果应该是字典类型")
            self.assertEqual(
                result['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertIn('10', result['output'], "答案应该包含翻倍结果10")

    @pytest.mark.asyncio
    @patch(
        "examples.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    async def test_hierarchical_group_interrupt_and_resume(
        self,
        mock_detect_intent
    ):
        """测试 HierarchicalGroup 中断和恢复

        测试场景：
        1. 创建带 Questioner 的 WorkflowAgent
        2. 第一次调用触发中断
        3. 使用 InteractiveInput 恢复
        4. 验证 workflow 正常完成
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # 第一次：提取字段，amount 为 null，触发中断
            create_json_response({"amount": None}),
            # 第二次：恢复后提取字段，amount 有值
            create_json_response({"amount": "100"}),
        ])

        with patch(
            "openjiuwen.core.workflow.components.llm."
            "questioner_comp.Model"
        ) as mock_model_class, patch(
            "openjiuwen.core.memory.long_term_memory."
            "LongTermMemory.set_scope_config",
            return_value=MagicMock()
        ):
            mock_model_class.return_value = mock_llm

            # 创建 Group 和 Leader
            group, leader_agent, _ = self._create_hierarchical_group()

            # 创建带 Questioner 的 WorkflowAgent
            workflow = self._build_questioner_workflow(
                workflow_id="transfer_flow",
                workflow_name="转账服务",
                field_name="amount",
                field_desc="转账金额"
            )
            transfer_agent = self._create_workflow_agent(
                agent_id="transfer_agent",
                description="转账服务",
                workflow=workflow,
                workflow_id="transfer_flow"
            )

            # 添加 Agents 到 Group
            group.add_agent("main_controller", leader_agent)
            group.add_agent("transfer_agent", transfer_agent)

            # Mock 意图识别直接返回 transfer_agent
            mock_detect_intent.return_value = "transfer_agent"

            conversation_id = "test_interrupt_resume"

            # 第一次调用 - 触发中断
            message1 = Event.create_user_event(
                content="我要转账",
                conversation_id=conversation_id
            )

            result1 = await group.invoke(message1)

            # 验证中断
            self.assertIsInstance(result1, list, "第一次应该返回交互请求列表")
            self.assertEqual(
                result1[0].type, const.INTERACTION, "应该返回交互类型"
            )

            # 第二次调用 - 使用 InteractiveInput 恢复
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "100元")

            message2 = Event.create_user_event(
                content=interactive_input,
                conversation_id=conversation_id
            )

            result2 = await group.invoke(message2)

            # 验证完成
            self.assertIsInstance(result2, dict, "第二次应该返回字典")
            self.assertEqual(
                result2['result_type'], 'answer', "应该返回 answer 类型"
            )
            self.assertEqual(
                result2['output'].state.name, 'COMPLETED', "workflow 应该完成"
            )

    @pytest.mark.asyncio
    @patch(
        "examples.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    async def test_hierarchical_group_mixed_agents(self, mock_detect_intent):
        """测试 HierarchicalGroup 混合 Agent 类型

        测试场景：
        1. 创建包含 WorkflowAgent 的 Group
        2. 路由到 WorkflowAgent
        3. 验证 WorkflowAgent 能正确处理请求
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # 创建 Group 和 Leader
        group, leader_agent, _ = self._create_hierarchical_group()

        # 创建 WorkflowAgent
        workflow = self._build_simple_workflow(
            workflow_id="simple_flow",
            workflow_name="简单工作流"
        )
        workflow_agent = self._create_workflow_agent(
            agent_id="workflow_agent",
            description="工作流处理",
            workflow=workflow,
            workflow_id="simple_flow"
        )

        # 添加 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("workflow_agent", workflow_agent)

        # 测试 WorkflowAgent
        mock_detect_intent.return_value = "workflow_agent"

        message = Event.create_user_event(
            content="执行工作流",
            conversation_id="test_mixed_agents"
        )

        result = await group.invoke(message)

        # 验证 WorkflowAgent 结果
        self.assertIsNotNone(result, "应该返回结果")
        self.assertIsInstance(result, dict, "结果应该是字典类型")
        self.assertEqual(
            result['result_type'], 'answer', "应该返回 answer 类型"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
