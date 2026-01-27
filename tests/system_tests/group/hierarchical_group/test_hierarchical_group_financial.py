#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
HierarchicalGroup 金融场景测试 - 使用 HierarchicalMainController + WorkflowAgent

场景：
- 1个主 single_agent（使用 HierarchicalMainController 进行意图识别和任务分发）
- 3个子 workflow single_agent：转账、查余额、理财
- 每个 workflow 都有 QuestionerComponent 中断节点
"""
import asyncio
import os
import unittest
from unittest.mock import patch

from openjiuwen.core.single_agent.legacy import (
    AgentConfig,
    ControllerAgent,
    WorkflowAgentConfig,
)
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from examples.groups.hierarchical_group import (
    HierarchicalGroup,
    HierarchicalGroupConfig
)
from examples.groups.hierarchical_group.agents.main_controller import HierarchicalMainController
from openjiuwen.core.controller.legacy import Event
from openjiuwen.core.common.constants import constant as const
from openjiuwen.core.workflow import WorkflowComponent, WorkflowCard
from openjiuwen.core.foundation.llm import (
    ModelConfig,
    BaseModelInfo,
    ModelClientConfig,
    ModelRequestConfig
)
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import (
    FieldInfo,
    QuestionerComponent,
    QuestionerConfig
)
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session import Session
from openjiuwen.core.workflow import Workflow

# 模型配置
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


class TestHierarchicalGroupFinancial(unittest.IsolatedAsyncioTestCase):
    """金融场景测试 - HierarchicalGroup + HierarchicalMainController + WorkflowAgent"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_model_config() -> ModelConfig:
        """创建模型配置"""
        return ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=120,
            ),
        )

    @staticmethod
    def _create_start_component():
        """创建 Start 组件"""
        return Start()

    def _build_financial_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str,
            field_name: str,
            field_desc: str
    ) -> Workflow:
        """
        构建金融业务工作流（带中断节点）

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            workflow_desc: 工作流描述
            field_name: 提问字段名
            field_desc: 提问字段描述

        Returns:
            Workflow: 包含 start -> questioner -> end 的工作流
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
                description=workflow_desc,
        )
        flow = Workflow(card=card)

        # 创建组件
        start = self._create_start_component()

        # 创建提问器（中断节点）
        key_fields = [
            FieldInfo(
                field_name=field_name,
                description=field_desc,
                required=True
            ),
        ]
        model_config = self._create_model_config()
        # client_provider 需要使用正确的大小写格式 (OpenAI, SiliconFlow)
        provider = model_config.model_provider
        if provider and provider.lower() == 'openai':
            provider = 'OpenAI'
        elif provider and provider.lower() == 'siliconflow':
            provider = 'SiliconFlow'
        questioner_config = QuestionerConfig(
            model_client_config=ModelClientConfig(
                client_provider=provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=model_config.model_info.temperature,
                top_p=model_config.model_info.top_p,
            ),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({"responseTemplate": f"{workflow_name}完成: {{{{{field_name}}}}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "questioner", questioner, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end", end, inputs_schema={field_name: f"${{questioner.{field_name}}}"}
        )

        # 连接拓扑: start -> questioner -> end
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    def _create_workflow_agent(
            self,
            agent_id: str,
            description: str,
            workflow: Workflow
    ) -> WorkflowAgent:
        """创建 WorkflowAgent"""
        config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    def _build_questioner_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str,
            questioner_type: str = "default"
    ) -> Workflow:
        """
        构建带多字段问询组件的工作流

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            workflow_desc: 工作流描述
            questioner_type: 问询类型 (cash_access/weather/default)

        Returns:
            Workflow: 包含 start -> questioner -> end 的工作流
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
                description=workflow_desc,
            )
        flow = Workflow(card=card)

        # 创建组件
        start = self._create_start_component()

        # 根据类型配置不同的字段
        if questioner_type == "cash_access":
            key_fields = [
                FieldInfo(field_name="bank", description="银行名称", required=True),
                FieldInfo(field_name="action", description="操作类型（存钱/取钱）", required=True),
                FieldInfo(field_name="amount", description="金额（数字）", required=True),
            ]
            response_template = "存取钱完成: bank={{bank}}, action={{action}}, amount={{amount}}"
            end_inputs = {
                "bank": "${questioner.bank}",
                "action": "${questioner.action}",
                "amount": "${questioner.amount}"
            }
        elif questioner_type == "weather":
            key_fields = [
                FieldInfo(field_name="location", description="城市名称", required=True),
                FieldInfo(field_name="date", description="日期", required=True),
                FieldInfo(field_name="weather", description="天气状况", required=True),
                FieldInfo(field_name="temperature", description="温度", required=True),
            ]
            response_template = "天气查询完成: location={{location}}, date={{date}}, weather={{weather}}, temperature={{temperature}}"
            end_inputs = {
                "location": "${questioner.location}",
                "date": "${questioner.date}",
                "weather": "${questioner.weather}",
                "temperature": "${questioner.temperature}"
            }
        else:
            key_fields = [
                FieldInfo(field_name="data", description="数据", required=True),
            ]
            response_template = "完成: {{data}}"
            end_inputs = {"data": "${questioner.data}"}

        # 创建 Questioner 组件
        model_config = self._create_model_config()
        # client_provider 需要使用正确的大小写格式 (OpenAI, SiliconFlow)
        provider = model_config.model_provider
        if provider and provider.lower() == 'openai':
            provider = 'OpenAI'
        elif provider and provider.lower() == 'siliconflow':
            provider = 'SiliconFlow'
        questioner_config = QuestionerConfig(
            model_client_config=ModelClientConfig(
                client_provider=provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=model_config.model_info.temperature,
                top_p=model_config.model_info.top_p,
            ),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({"responseTemplate": response_template})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "questioner", questioner, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp("end", end, inputs_schema=end_inputs)

        # 连接拓扑: start -> questioner -> end
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    def _create_llm_agent(self, agent_id: str, description: str, with_tools: bool = False):
        """创建 LLM Agent

        Args:
            agent_id: Agent ID
            description: Agent 描述
            with_tools: 是否添加工具

        Returns:
            LLMAgent 实例
        """
        from openjiuwen.core.application.llm_agent import LLMAgent
        from openjiuwen.core.application.llm_agent import ReActAgentConfig
        from openjiuwen.core.foundation.tool.function.function import LocalFunction, ToolCard

        model_config = self._create_model_config()
        prompt_template = [
            {"role": "system", "content": f"你是一个{description}的AI助手。根据用户输入进行翻倍运算并输出结果。"}
        ]

        config = ReActAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            model=model_config,
            prompt_template=prompt_template,
        )

        agent = LLMAgent(config)

        # 可选：添加工具
        if with_tools:
            multiply_tool = LocalFunction(
                card=ToolCard(
                    name="multiply",
                    description="将两个数字相乘",
                    input_params={
                        "type": "object",
                        "properties": {
                            "a": {"description": "第一个数", "type": "number"},
                            "b": {"description": "第二个数", "type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                ),
                func=lambda a, b: a * b,
            )
            agent.add_tools([multiply_tool])

        return agent

    def _create_react_agent(self, agent_id: str, description: str):
        """创建 ReAct Agent

        Args:
            agent_id: Agent ID
            description: Agent 描述

        Returns:
            ReActAgent 实例
        """
        from openjiuwen.core.single_agent.legacy import LegacyReActAgent
        from openjiuwen.core.application.llm_agent import ReActAgentConfig
        from openjiuwen.core.foundation.tool.function.function import LocalFunction, ToolCard

        model_config = self._create_model_config()
        prompt_template = [
            {"role": "system", "content": f"你是一个{description}的AI助手。使用提供的工具完成用户任务。"}
        ]

        config = ReActAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            model=model_config,
            prompt_template=prompt_template,
        )

        agent = LegacyReActAgent(config)

        # 添加求和工具
        sum_tool = LocalFunction(
            card=ToolCard(
                name="sum",
                description="两数求和",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"description": "第一个数", "type": "number"},
                        "b": {"description": "第二个数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )
        agent.add_tools([sum_tool])

        return agent

    @unittest.skip("skip system test")
    async def test_financial_workflow_with_interrupt_invoke(self):
        """
        金融场景完整用例：HierarchicalGroup + 工作流中断恢复

        测试流程：
        1. 创建 HierarchicalGroup，主 single_agent 使用 HierarchicalMainController
        2. 添加 3 个金融 WorkflowAgent（每个都有中断节点）
        3. 发送转账请求 -> 路由到转账 single_agent -> 触发中断（询问金额）
        4. 提供金额 -> 恢复工作流 -> 完成
        """
        print("\n=== 金融场景 HierarchicalGroup 测试 ===")

        # 1. 创建金融业务工作流
        transfer_workflow = self._build_financial_workflow(
            workflow_id="transfer_flow",
            workflow_name="转账服务",
            workflow_desc="处理用户转账请求，支持转账到指定账户",
            field_name="amount",
            field_desc="转账金额（数字）"
        )

        balance_workflow = self._build_financial_workflow(
            workflow_id="balance_flow",
            workflow_name="余额查询",
            workflow_desc="查询用户账户余额信息",
            field_name="account",
            field_desc="账户号码"
        )

        invest_workflow = self._build_financial_workflow(
            workflow_id="invest_flow",
            workflow_name="理财服务",
            workflow_desc="提供理财产品推荐和购买服务",
            field_name="product",
            field_desc="理财产品名称"
        )

        # 2. 创建 WorkflowAgent
        transfer_agent = self._create_workflow_agent(
            agent_id="transfer_agent",
            description="转账服务，处理用户的转账请求",
            workflow=transfer_workflow
        )

        balance_agent = self._create_workflow_agent(
            agent_id="balance_agent",
            description="余额查询服务，查询用户账户余额",
            workflow=balance_workflow
        )

        invest_agent = self._create_workflow_agent(
            agent_id="invest_agent",
            description="理财服务，提供理财产品推荐和购买",
            workflow=invest_workflow
        )

        # 3. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group",
            leader_agent_id="main_controller"
        )
        hierarchical_group = HierarchicalGroup(config)

        # 4. 创建主 single_agent（HierarchicalMainController）
        main_config = AgentConfig(
            id="main_controller",
            description="金融服务主控制器，识别用户意图并分发任务"
        )
        main_controller = HierarchicalMainController()
        main_agent = ControllerAgent(main_config, controller=main_controller)

        # 5. 添加所有 single_agent 到 group
        hierarchical_group.add_agent("main_controller", main_agent)
        hierarchical_group.add_agent("transfer_agent", transfer_agent)
        hierarchical_group.add_agent("balance_agent", balance_agent)
        hierarchical_group.add_agent("invest_agent", invest_agent)

        conversation_id = "financial_test_001"

        # ========== 步骤1: 发送转账请求 -> 中断 ==========
        # 不指定 receiver_id，让消息自动路由到 leader
        # Leader (HierarchicalMainController) 会通过 LLM 意图识别找到目标 single_agent
        print("\n【步骤1】发送转账请求")
        message1 = Event.create_user_event(
            content="我要转账",
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，消息会自动路由到 leader，由 leader 做意图识别

        try:
            result1 = await asyncio.wait_for(
                hierarchical_group.invoke(message1),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤1 超时！")
            raise

        print(f"步骤1 结果类型: {type(result1)}")

        # 校验：应该触发中断
        self.assertIsInstance(result1, list, "步骤1应该返回交互请求列表")
        self.assertTrue(len(result1) > 0, "步骤1应该有交互请求")
        self.assertEqual(
            result1[0].type, const.INTERACTION, "步骤1应该返回交互类型"
        )
        print(f"✅ 步骤1成功：转账工作流触发中断，询问金额")

        # ========== 步骤2: 提供金额 -> 恢复 -> 完成 ==========
        # 不指定 receiver_id，leader 会自动检测到有中断的 single_agent 并恢复
        print("\n【步骤2】提供转账金额")
        message2 = Event.create_user_event(
            content="100元",
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，leader 会通过 _get_last_interrupted_agent 恢复到中断的 single_agent

        try:
            result2 = await asyncio.wait_for(
                hierarchical_group.invoke(message2),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤2 超时！")
            raise

        print(f"步骤2 结果: {result2}")

        # 校验：工作流应该完成
        self.assertIsInstance(result2, dict, "步骤2应该返回字典")
        self.assertEqual(
            result2['result_type'], 'answer', "步骤2应该返回answer类型"
        )
        self.assertEqual(
            result2['output'].state.value, 'COMPLETED', "步骤2工作流应该完成"
        )
        response_content = result2['output'].result.get('response', '')
        print(f"✅ 步骤2成功：转账工作流完成，返回: {response_content}")

        print("\n🎉 金融场景测试完成！")

    @unittest.skip("skip system test")
    async def test_financial_workflow_with_interrupt_stream(self):
        """
        金融场景 Stream 用例：HierarchicalGroup.stream + 工作流中断恢复

        测试流程：
        1. 创建 HierarchicalGroup，主 single_agent 使用 HierarchicalMainController
        2. 添加 3 个金融 WorkflowAgent（每个都有中断节点）
        3. 使用 stream() 发送转账请求 -> 触发中断
        4. 使用 stream() 提供金额 -> 恢复工作流 -> 完成
        """
        print("\n=== 金融场景 HierarchicalGroup Stream 测试 ===")

        # 1. 创建金融业务工作流
        transfer_workflow = self._build_financial_workflow(
            workflow_id="transfer_flow_stream",
            workflow_name="转账服务",
            workflow_desc="处理用户转账请求，支持转账到指定账户",
            field_name="amount",
            field_desc="转账金额（数字）"
        )

        balance_workflow = self._build_financial_workflow(
            workflow_id="balance_flow_stream",
            workflow_name="余额查询",
            workflow_desc="查询用户账户余额信息",
            field_name="account",
            field_desc="账户号码"
        )

        invest_workflow = self._build_financial_workflow(
            workflow_id="invest_flow_stream",
            workflow_name="理财服务",
            workflow_desc="提供理财产品推荐和购买服务",
            field_name="product",
            field_desc="理财产品名称"
        )

        # 2. 创建 WorkflowAgent
        transfer_agent = self._create_workflow_agent(
            agent_id="transfer_agent",
            description="转账服务，处理用户的转账请求",
            workflow=transfer_workflow
        )

        balance_agent = self._create_workflow_agent(
            agent_id="balance_agent",
            description="余额查询服务，查询用户账户余额",
            workflow=balance_workflow
        )

        invest_agent = self._create_workflow_agent(
            agent_id="invest_agent",
            description="理财服务，提供理财产品推荐和购买",
            workflow=invest_workflow
        )

        # 3. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group_stream",
            leader_agent_id="main_controller"
        )
        hierarchical_group = HierarchicalGroup(config)

        # 4. 创建主 single_agent（HierarchicalMainController）
        main_config = AgentConfig(
            id="main_controller",
            description="金融服务主控制器，识别用户意图并分发任务"
        )
        main_controller = HierarchicalMainController()
        main_agent = ControllerAgent(main_config, controller=main_controller)

        # 5. 添加所有 single_agent 到 group
        hierarchical_group.add_agent("main_controller", main_agent)
        hierarchical_group.add_agent("transfer_agent", transfer_agent)
        hierarchical_group.add_agent("balance_agent", balance_agent)
        hierarchical_group.add_agent("invest_agent", invest_agent)

        conversation_id = "financial_stream_test_001"

        # ========== 步骤1: 使用 stream 发送转账请求 -> 中断 ==========
        # 不指定 receiver_id，让消息自动路由到 leader
        print("\n【步骤1】使用 stream 发送转账请求")
        message1 = Event.create_user_event(
            content="我要转账",
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，消息会自动路由到 leader，由 leader 做意图识别

        # 收集流式输出
        chunks1 = []
        try:
            async def collect_stream1():
                async for chunk in hierarchical_group.stream(message1):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks1.append(chunk)

            await asyncio.wait_for(collect_stream1(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤1 超时！")
            raise

        print(f"步骤1 收到 {len(chunks1)} 个 chunks")

        # 校验：应该触发中断
        self.assertTrue(len(chunks1) > 0, "步骤1应该有流式输出")
        final_chunk1 = chunks1[-1]
        # 交互请求会直接透传 INTERACTION 类型
        self.assertEqual(
            final_chunk1.type, const.INTERACTION, "步骤1应该返回交互类型"
        )
        print(f"✅ 步骤1成功：转账工作流触发中断，询问金额")

        # ========== 步骤2: 使用 stream 提供金额 -> 恢复 -> 完成 ==========
        # 不指定 receiver_id，leader 会自动检测到有中断的 single_agent 并恢复
        print("\n【步骤2】使用 stream 提供转账金额")
        message2 = Event.create_user_event(
            content="200元",
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，leader 会通过 _get_last_interrupted_agent 恢复到中断的 single_agent

        # 收集流式输出
        chunks2 = []
        try:
            async def collect_stream2():
                async for chunk in hierarchical_group.stream(message2):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks2.append(chunk)

            await asyncio.wait_for(collect_stream2(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤2 超时！")
            raise

        print(f"步骤2 收到 {len(chunks2)} 个 chunks")

        # 校验：工作流应该完成
        self.assertTrue(len(chunks2) > 0, "步骤2应该有流式输出")

        # 找到最终结果 chunk（workflow_final 类型）
        final_chunk2 = None
        for chunk in chunks2:
            if chunk.type == 'workflow_final':
                final_chunk2 = chunk
                break

        self.assertIsNotNone(final_chunk2, "步骤2应该有 workflow_final chunk")

        payload2 = final_chunk2.payload
        response_content = payload2.get('response', '')
        self.assertIn('200', response_content, "步骤2应该包含转账金额")
        print(f"✅ 步骤2成功：转账工作流完成，返回: {response_content}")

        print("\n🎉 金融场景 Stream 测试完成！")

    @unittest.skip("skip system test")
    async def test_multi_agent_jump_and_recovery_stream(self):
        """
        多子Agent跳转恢复测试：HierarchicalGroup.stream + 多Agent中断跳转恢复

        场景：
        1. query1 "我要转账" -> 路由到 transfer_agent -> 中断（询问金额）
        2. query2 "我想理财" -> 路由到 invest_agent -> 中断（询问产品）
        3. query3 "100元" -> 恢复 transfer_agent -> 完成转账
        4. query4 "稳健型产品" -> 恢复 invest_agent -> 完成理财

        验证：
        - 多个子Agent可以同时处于中断状态
        - 系统能正确识别用户意图并恢复对应的Agent
        - 每个Agent的中断状态独立维护
        """
        print("\n=== 多子Agent跳转恢复测试 ===")

        # 1. 创建金融业务工作流
        transfer_workflow = self._build_financial_workflow(
            workflow_id="transfer_flow_multi",
            workflow_name="转账服务",
            workflow_desc="处理用户转账请求，支持转账到指定账户",
            field_name="amount",
            field_desc="转账金额（数字）"
        )

        balance_workflow = self._build_financial_workflow(
            workflow_id="balance_flow_multi",
            workflow_name="余额查询",
            workflow_desc="查询用户账户余额信息",
            field_name="account",
            field_desc="账户号码"
        )

        invest_workflow = self._build_financial_workflow(
            workflow_id="invest_flow_multi",
            workflow_name="理财服务",
            workflow_desc="提供理财产品推荐和购买服务",
            field_name="product",
            field_desc="理财产品名称"
        )

        # 2. 创建 WorkflowAgent
        transfer_agent = self._create_workflow_agent(
            agent_id="transfer_agent",
            description="转账服务，处理用户的转账请求",
            workflow=transfer_workflow
        )

        balance_agent = self._create_workflow_agent(
            agent_id="balance_agent",
            description="余额查询服务，查询用户账户余额",
            workflow=balance_workflow
        )

        invest_agent = self._create_workflow_agent(
            agent_id="invest_agent",
            description="理财服务，提供理财产品推荐和购买",
            workflow=invest_workflow
        )

        # 3. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group_multi",
            leader_agent_id="main_controller"
        )
        hierarchical_group = HierarchicalGroup(config)

        # 4. 创建主 single_agent（HierarchicalMainController）
        main_config = AgentConfig(
            id="main_controller",
            description="金融服务主控制器，识别用户意图并分发任务",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        main_agent = ControllerAgent(main_config, controller=main_controller)

        # 5. 添加所有 single_agent 到 group
        hierarchical_group.add_agent("main_controller", main_agent)
        hierarchical_group.add_agent("transfer_agent", transfer_agent)
        hierarchical_group.add_agent("balance_agent", balance_agent)
        hierarchical_group.add_agent("invest_agent", invest_agent)

        conversation_id = "financial_multi_agent_jump_test_001"

        # ========== 步骤1: 发送转账请求 -> transfer_agent 中断 ==========
        print("\n【步骤1】发送转账请求 -> transfer_agent 中断")
        message1 = Event.create_user_event(
            content="我要转账",
            conversation_id=conversation_id
        )

        chunks1 = []
        try:
            async def collect_stream1():
                async for chunk in hierarchical_group.stream(message1):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks1.append(chunk)

            await asyncio.wait_for(collect_stream1(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤1 超时！")
            raise

        print(f"步骤1 收到 {len(chunks1)} 个 chunks")

        # 校验：transfer_agent 触发中断
        self.assertTrue(len(chunks1) > 0, "步骤1应该有流式输出")
        final_chunk1 = chunks1[-1]
        self.assertEqual(
            final_chunk1.type, const.INTERACTION, "步骤1应该返回交互类型"
        )
        print(f"✅ 步骤1成功：transfer_agent 触发中断，询问金额")

        # ========== 步骤2: 发送理财请求 -> invest_agent 中断 ==========
        print("\n【步骤2】发送理财请求 -> invest_agent 中断（跳转到新Agent）")
        message2 = Event.create_user_event(
            content="我想理财",
            conversation_id=conversation_id
        )

        chunks2 = []
        try:
            async def collect_stream2():
                async for chunk in hierarchical_group.stream(message2):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks2.append(chunk)

            await asyncio.wait_for(collect_stream2(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤2 超时！")
            raise

        print(f"步骤2 收到 {len(chunks2)} 个 chunks")

        # 校验：invest_agent 触发中断
        self.assertTrue(len(chunks2) > 0, "步骤2应该有流式输出")
        final_chunk2 = chunks2[-1]
        self.assertEqual(
            final_chunk2.type, const.INTERACTION, "步骤2应该返回交互类型"
        )
        print(f"✅ 步骤2成功：invest_agent 触发中断，询问产品")

        # ========== 步骤3: 提供金额 -> 恢复 transfer_agent -> 完成 ==========
        print("\n【步骤3】提供金额 -> 恢复 transfer_agent -> 完成转账")
        message3 = Event.create_user_event(
            content="我要转账100元",
            conversation_id=conversation_id
        )

        chunks3 = []
        try:
            async def collect_stream3():
                async for chunk in hierarchical_group.stream(message3):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks3.append(chunk)

            await asyncio.wait_for(collect_stream3(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤3 超时！")
            raise

        print(f"步骤3 收到 {len(chunks3)} 个 chunks")

        # 校验：transfer_agent 恢复并完成
        self.assertTrue(len(chunks3) > 0, "步骤3应该有流式输出")

        # 找到最终结果 chunk
        final_chunk3 = None
        for chunk in chunks3:
            if chunk.type == 'workflow_final':
                final_chunk3 = chunk
                break

        self.assertIsNotNone(final_chunk3, "步骤3应该有 workflow_final chunk")
        payload3 = final_chunk3.payload
        response_content3 = payload3.get('response', '')
        self.assertIn('100', response_content3, "步骤3应该包含转账金额")
        print(f"✅ 步骤3成功：transfer_agent 恢复并完成，返回: {response_content3}")

        # ========== 步骤4: 提供产品 -> 恢复 invest_agent -> 完成 ==========
        print("\n【步骤4】提供产品 -> 恢复 invest_agent -> 完成理财")
        message4 = Event.create_user_event(
            content="我要购买稳健型理财产品",
            conversation_id=conversation_id
        )

        chunks4 = []
        try:
            async def collect_stream4():
                async for chunk in hierarchical_group.stream(message4):
                    print(f"  Stream chunk: {chunk.type}")
                    chunks4.append(chunk)

            await asyncio.wait_for(collect_stream4(), timeout=120.0)
        except asyncio.TimeoutError:
            print("❌ 步骤4 超时！")
            raise

        print(f"步骤4 收到 {len(chunks4)} 个 chunks")

        # 校验：invest_agent 恢复并完成
        self.assertTrue(len(chunks4) > 0, "步骤4应该有流式输出")

        # 找到最终结果 chunk
        final_chunk4 = None
        for chunk in chunks4:
            if chunk.type == 'workflow_final':
                final_chunk4 = chunk
                break

        self.assertIsNotNone(final_chunk4, "步骤4应该有 workflow_final chunk")
        payload4 = final_chunk4.payload
        response_content4 = payload4.get('response', '')
        self.assertIn('稳健', response_content4, "步骤4应该包含理财产品名称")
        print(f"✅ 步骤4成功：invest_agent 恢复并完成，返回: {response_content4}")

        print("\n🎉 多子Agent跳转恢复测试完成！")
        print("   - transfer_agent: 中断 -> 跳转 -> 恢复 -> 完成")
        print("   - invest_agent: 中断 -> 恢复 -> 完成")

    @unittest.skip("skip system test")
    async def test_hierarchical_main_controller_001(self):
        """
        不指定路由，由leader_agent做意图识别

        测试流程：
        1. 创建多种类型的 single_agent（workflow single_agent、llm single_agent、react single_agent）
        2. 创建 HierarchicalGroup，主 single_agent 使用 HierarchicalMainController
        3. 不指定路由，由 leader_agent 做意图识别并分发任务
        """
        print("\n=== 测试 HierarchicalMainController 意图识别 ===")

        conversation_id = "test_hierarchical_main_controller_001"

        # 1、创建 workflow
        cash_access_flow = self._build_questioner_workflow(
            workflow_id="cash_access_flow",
            workflow_name="存取钱",
            workflow_desc="银行存取钱",
            questioner_type="cash_access"
        )
        weather_flow = self._build_questioner_workflow(
            workflow_id="weather_flow",
            workflow_name="天气",
            workflow_desc="城市天气查询",
            questioner_type="weather"
        )

        # 2、创建 single_agent
        cash_access_agent = self._create_workflow_agent(
            agent_id="cash_access_agent",
            description="银行存取钱，处理用户在指定银行进行存取钱操作",
            workflow=cash_access_flow
        )
        weather_agent = self._create_workflow_agent(
            agent_id="weather_agent",
            description="城市天气查询，处理用户对特定城市在某个时间段的天气温度查询",
            workflow=weather_flow
        )
        double_template_agent = self._create_llm_agent(
            agent_id="double_template_agent",
            description="进行翻倍运算并模板输出"
        )
        sum_agent = self._create_react_agent(
            agent_id="sum_agent",
            description="两数求和运算"
        )

        # 3. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group",
            leader_agent_id="main_controller"
        )
        group = HierarchicalGroup(config)

        # 4. 创建 Leader Agent
        main_config = AgentConfig(
            id="main_controller",
            description="组合型agent group",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        # 5. 添加所有 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("cash_access_agent", cash_access_agent)
        group.add_agent("weather_agent", weather_agent)
        group.add_agent("double_template_agent", double_template_agent)
        group.add_agent("sum_agent", sum_agent)

        # 6-1、 与第1个agent进行交互（存取钱）
        # message1 = Event.create_user_event(
        #     content="民生银行存钱5000元",
        #     conversation_id=conversation_id
        # )
        # result = await group.invoke(message1)
        # print(f"single_agent group result: {result}")
        # self.assertEqual(
        #     result["output"].result,
        #     {'output': {'data': {'bank': '民生银行', 'action': '存钱', 'amount': 5000}}}
        # )

        # 6-2、 与第2个agent进行交互（天气查询）
        # message2 = Event.create_user_event(
        #     content="杭州明日天气晴温度25度",
        #     conversation_id=conversation_id
        # )
        # result = await group.invoke(message2)
        # print(f"single_agent group result: {result}")
        # self.assertEqual(
        #     result["output"].result,
        #     {'output': {'data': {'location': '杭州', 'date': '明日', 'weather': '晴', 'temperature': '25度'}}}
        # )

        # 6-3、 与第3个agent进行交互（翻倍运算 - LLM Agent）
        message3 = Event.create_user_event(
            content="帮我把数字5翻倍，然后用模板格式输出结果",
            conversation_id=conversation_id
        )
        result3 = await group.invoke(message3)
        print(f"LLM Agent (翻倍运算) result: {result3}")

        # 6-4、与第4个agent进行交互（求和运算 - React Agent）
        message4 = Event.create_user_event(
            content="请计算 3 加 5 的和是多少",
            conversation_id=conversation_id
        )
        result4 = await group.invoke(message4)
        print(f"React Agent (求和运算) result: {result4}")

        print("\n[PASS] HierarchicalMainController 意图识别测试完成！")

    @patch(
        "openjiuwen.core.application.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    @unittest.skip("skip system test")
    async def test_hierarchical_with_react_agent_only(self, mock_detect_intent):
        """
        测试 HierarchicalGroup 中只有 React Agent 的场景

        测试流程：
        1. 创建 HierarchicalGroup，只添加一个 React Agent
        2. Mock 意图识别直接返回 React Agent 的 ID
        3. 验证 React Agent 被正确调用并返回结果
        """
        print("\n=== 测试 HierarchicalGroup + React Agent Only ===")

        conversation_id = "test_hierarchical_react_agent_only"

        # 1. 创建 React Agent
        sum_agent = self._create_react_agent(
            agent_id="sum_agent",
            description="两数求和运算"
        )

        # 2. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="react_only_group",
            leader_agent_id="main_controller"
        )
        group = HierarchicalGroup(config)

        # 3. 创建 Leader Agent
        main_config = AgentConfig(
            id="main_controller",
            description="组合型agent group",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        # 4. 添加 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("sum_agent", sum_agent)

        # 5. Mock 意图识别直接返回 sum_agent
        mock_detect_intent.return_value = "sum_agent"

        # 6. 发送消息
        message = Event.create_user_event(
            content="请计算 3 加 5 的和是多少",
            conversation_id=conversation_id
        )

        print(f"发送消息：{message.content.get_query()}")
        print("Mock 意图识别返回：sum_agent")

        result = await asyncio.wait_for(
            group.invoke(message),
            timeout=60.0
        )
        print(f"React Agent result: {result}")

        # 7. 验证结果
        self.assertIsNotNone(result, "应该返回结果")
        self.assertIsInstance(result, dict, "结果应该是字典类型")

        print("\n[PASS] HierarchicalGroup + React Agent Only 测试完成！")

    @patch(
        "openjiuwen.core.application.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    @unittest.skip("skip system test")
    async def test_hierarchical_with_llm_agent_only(self, mock_detect_intent):
        """
        测试 HierarchicalGroup 中只有 LLM Agent 的场景

        测试流程：
        1. 创建 HierarchicalGroup，只添加一个 LLM Agent
        2. Mock 意图识别直接返回 LLM Agent 的 ID
        3. 验证 LLM Agent 被正确调用并返回结果
        """
        print("\n=== 测试 HierarchicalGroup + LLM Agent Only ===")

        conversation_id = "test_hierarchical_llm_agent_only"

        # 1. 创建 LLM Agent
        double_agent = self._create_llm_agent(
            agent_id="double_agent",
            description="进行翻倍运算并模板输出"
        )

        # 2. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="llm_only_group",
            leader_agent_id="main_controller"
        )
        group = HierarchicalGroup(config)

        # 3. 创建 Leader Agent
        main_config = AgentConfig(
            id="main_controller",
            description="组合型agent group",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        # 4. 添加 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("double_agent", double_agent)

        # 5. Mock 意图识别直接返回 double_agent
        mock_detect_intent.return_value = "double_agent"

        # 6. 发送消息
        message = Event.create_user_event(
            content="帮我把数字 5 翻倍",
            conversation_id=conversation_id
        )

        print(f"发送消息：{message.content.get_query()}")
        print("Mock 意图识别返回：double_agent")

        result = await asyncio.wait_for(
            group.invoke(message),
            timeout=60.0
        )
        print(f"LLM Agent result: {result}")

        # 7. 验证结果
        self.assertIsNotNone(result, "应该返回结果")
        self.assertIsInstance(result, dict, "结果应该是字典类型")

        # 验证返回了 answer 类型的结果
        if "result_type" in result:
            self.assertEqual(
                result.get("result_type"), "answer",
                "应该返回 answer 类型的结果"
            )

        print("\n[PASS] HierarchicalGroup + LLM Agent Only 测试完成！")

    @patch(
        "openjiuwen.core.application.groups.hierarchical_group.agents.main_controller."
        "HierarchicalMainController._detect_intent"
    )
    @unittest.skip("skip system test")
    async def test_hierarchical_with_llm_agent_with_tools(self, mock_detect_intent):
        """
        测试 HierarchicalGroup 中 LLM Agent 使用工具的场景

        测试流程：
        1. 创建带工具的 LLM Agent
        2. Mock 意图识别直接返回 LLM Agent 的 ID
        3. 验证 LLM Agent 能够正确使用工具
        """
        print("\n=== 测试 HierarchicalGroup + LLM Agent with Tools ===")

        conversation_id = "test_hierarchical_llm_agent_with_tools"

        # 1. 创建带工具的 LLM Agent
        calc_agent = self._create_llm_agent(
            agent_id="calc_agent",
            description="数学计算助手",
            with_tools=True  # 添加工具
        )

        # 2. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="llm_tools_group",
            leader_agent_id="main_controller"
        )
        group = HierarchicalGroup(config)

        # 3. 创建 Leader Agent
        main_config = AgentConfig(
            id="main_controller",
            description="组合型agent group",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        # 4. 添加 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("calc_agent", calc_agent)

        # 5. Mock 意图识别直接返回 calc_agent
        mock_detect_intent.return_value = "calc_agent"

        # 6. 发送消息
        message = Event.create_user_event(
            content="计算 5 乘以 3",
            conversation_id=conversation_id
        )

        print(f"发送消息：{message.content.get_query()}")
        print("Mock 意图识别返回：calc_agent")
        print("期望：LLM Agent 能够正确加载并使用 multiply 工具")

        result = await asyncio.wait_for(
            group.invoke(message),
            timeout=60.0
        )
        print(f"LLM Agent with Tools result: {result}")

        # 7. 验证结果
        self.assertIsNotNone(result, "应该返回结果")
        self.assertIsInstance(result, dict, "结果应该是字典类型")

        # 验证返回了 answer 类型的结果
        if "result_type" in result:
            self.assertEqual(
                result.get("result_type"), "answer",
                "应该返回 answer 类型的结果"
            )

        print("\n[PASS] HierarchicalGroup + LLM Agent with Tools 测试完成！")

    @unittest.skip("skip system test")
    async def test_financial_workflow_with_interrupt_invoke_interactive_input(self):
        """
        金融场景完整用例：HierarchicalGroup + 工作流中断恢复

        测试流程：
        1. 创建 HierarchicalGroup，主 single_agent 使用 HierarchicalMainController
        2. 添加 2 个金融 WorkflowAgent（每个都有中断节点）
        3. 发送转账请求 -> 路由到转账 single_agent -> 触发中断（询问金额）
        4. 提供金额 -> 恢复工作流 -> 完成
        """
        print("\n=== 金融场景 HierarchicalGroup 测试 ===")

        # 1. 创建金融业务工作流
        transfer_workflow = self._build_financial_workflow(
            workflow_id="transfer_flow",
            workflow_name="转账服务",
            workflow_desc="处理用户转账请求，支持转账到指定账户",
            field_name="amount",
            field_desc="转账金额（数字）"
        )

        balance_workflow = self._build_financial_workflow(
            workflow_id="balance_flow",
            workflow_name="余额查询",
            workflow_desc="查询用户账户余额信息",
            field_name="account",
            field_desc="账户号码"
        )

        # 2. 创建 WorkflowAgent
        transfer_agent = self._create_workflow_agent(
            agent_id="transfer_agent",
            description="转账服务，处理用户的转账请求",
            workflow=transfer_workflow
        )

        balance_agent = self._create_workflow_agent(
            agent_id="balance_agent",
            description="余额查询服务，查询用户账户余额",
            workflow=balance_workflow
        )

        # 3. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group",
            leader_agent_id="main_controller"
        )
        hierarchical_group = HierarchicalGroup(config)

        # 4. 创建主 single_agent（HierarchicalMainController）
        main_config = AgentConfig(
            id="main_controller",
            description="金融服务主控制器，识别用户意图并分发任务"
        )
        main_controller = HierarchicalMainController()
        main_agent = ControllerAgent(main_config, controller=main_controller)

        # 5. 添加所有 single_agent 到 group
        hierarchical_group.add_agent("main_controller", main_agent)
        hierarchical_group.add_agent("transfer_agent", transfer_agent)
        hierarchical_group.add_agent("balance_agent", balance_agent)

        conversation_id = "financial_test_001"

        # ========== 步骤1: 发送转账请求 -> 中断 ==========
        # 不指定 receiver_id，让消息自动路由到 leader
        # Leader (HierarchicalMainController) 会通过 LLM 意图识别找到目标 single_agent
        print("\n【步骤1】发送转账请求")
        message1 = Event.create_user_event(
            content="我要转账",
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，消息会自动路由到 leader，由 leader 做意图识别

        try:
            result1 = await asyncio.wait_for(
                hierarchical_group.invoke(message1),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤1 超时！")
            raise

        print(f"步骤1 结果类型: {type(result1)}")

        # 校验：应该触发中断
        self.assertIsInstance(result1, list, "步骤1应该返回交互请求列表")
        self.assertTrue(result1, "步骤1应该有交互请求")
        self.assertEqual(
            result1[0].type, const.INTERACTION, "步骤1应该返回交互类型"
        )
        print(f"✅ 步骤1成功：转账工作流触发中断，询问金额")

        # ========== 步骤2: 提供金额 -> 恢复 -> 完成 ==========
        # 不指定 receiver_id，leader 会自动检测到有中断的 single_agent 并恢复
        print("\n【步骤2】提供转账金额")
        user_input = InteractiveInput()
        component_id = result1[0].payload.id
        user_input.update(component_id, {"amount": "100元"})
        message2 = Event.create_user_event(
            content=user_input,
            conversation_id=conversation_id
        )
        # 不设置 receiver_id，leader 会通过 _get_last_interrupted_agent 恢复到中断的 single_agent

        try:
            result2 = await asyncio.wait_for(
                hierarchical_group.invoke(message2),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤2 超时！")
            raise

        print(f"步骤2 结果: {result2}")

        # 校验：工作流应该完成
        self.assertIsInstance(result2, dict, "步骤2应该返回字典")
        self.assertEqual(
            result2['result_type'], 'answer', "步骤2应该返回answer类型"
        )
        self.assertEqual(
            result2['output'].state.value, 'COMPLETED', "步骤2工作流应该完成"
        )
        response_content = result2['output'].result.get('response', '')
        self.assertIn("100", response_content, "应该包含转账金额100元")
        print(f"✅ 步骤2成功：转账工作流完成，返回: {response_content}")

        print("\n🎉 金融场景测试完成！")

    @unittest.skip("skip system test")
    async def test_hierarchical_group_014(self):
        """
        # @CaseID: test_hierarchical_group_014
        # @Description: HierarchicalGroup添加 workflowAgent(相同超步提问器/中断组件) llmAgent ReactAgent
        #               使用InteractiveInput恢复，通过主agent调度workflowAgent
        # @Precondition: 部署jiuwen开源项目环境
        # @Step:
        # 1、创建1个WorkflowAgent(相同超步提问器/中断组件)、 llmAgent 、ReactAgent
        # 2、创建 HierarchicalGroup
        # 3、将3个Agent、1个leader_agent加入group
        # 4、Runner.run_agent_group_streaming进行会话操作，使用InteractiveInput恢复，通过主agent调度workflowAgent
        # @Result:
        # single_agent group创建成功，会话请求正常
        # @Date:
        # @Status: New
        # @ModifyRecord: None
        # !!================================================================
        """
        print("\n=== 测试 test_hierarchical_group_014 ===")

        conversation_id = "test_hierarchical_group_014"

        # 1、创建 workflow - 银行存取钱业务（包含两个中断组件）
        cash_access_flow = self._build_cash_access_workflow(
            workflow_id="cash_access_flow",
            workflow_name="银行存取钱",
            workflow_desc="处理用户在各类银行（如民生银行、工商银行、建设银行等）进行存钱、取钱操作的业务流程"
        )

        # 2、创建 workflow - 支付密码（包含中断组件）
        cipher_flow = self._build_cipher_workflow(
            workflow_id="cipher_flow",
            workflow_name="支付密码",
            workflow_desc="处理用户设置或修改支付密码的业务流程"
        )

        # 3、创建 WorkflowAgent（包含多个workflow）
        bank_agent = self._create_workflow_agent_multi(
            agent_id="bank_agent",
            description="银行业务助手，处理各类银行（如民生银行、工商银行、建设银行等）的存取款、支付密码设置等金融服务",
            workflows=[cipher_flow, cash_access_flow]
        )

        # 4、创建 LLM Agent
        double_template_agent = self._create_llm_agent(
            agent_id="double_template_agent",
            description="数学翻倍运算助手，帮助用户将数字翻倍（乘以2）并格式化输出结果"
        )

        # 5、创建 React Agent
        sum_agent = self._create_react_agent(
            agent_id="sum_agent",
            description="数学求和助手，帮助用户计算两个数字的和、加法运算"
        )

        # 6. 创建 HierarchicalGroup
        config = HierarchicalGroupConfig(
            group_id="financial_group",
            leader_agent_id="main_controller"
        )
        group = HierarchicalGroup(config)

        # 7. 创建 Leader Agent
        main_config = AgentConfig(
            id="main_controller",
            description="组合型agent group",
            model=self._create_model_config()
        )
        main_controller = HierarchicalMainController()
        leader_agent = ControllerAgent(main_config, controller=main_controller)

        # 8. 添加所有 Agents 到 Group
        group.add_agent("main_controller", leader_agent)
        group.add_agent("double_template_agent", double_template_agent)
        group.add_agent("bank_agent", bank_agent)
        group.add_agent("sum_agent", sum_agent)

        # 9、与bank_agent下面的cash_access_flow进行交互
        print("\n【步骤1】发送银行存取钱请求，触发并行中断")
        message1 = Event.create_user_event(content="我想在民生银行存取款", conversation_id=conversation_id)
        chunks1 = []
        stream1 = Runner.run_agent_group_streaming(group, message1)
        async for chunk in stream1:
            chunks1.append(chunk)
            print(f"single_agent group message1 chunk: {chunk}")

        # 收集所有中断
        interaction_chunks = []
        for chunk in chunks1:
            if chunk.type == '__interaction__':
                interaction_chunks.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")

        self.assertEqual(len(interaction_chunks), 2, "应该同时返回2个中断（interactive和questioner）")

        # 10、使用InteractiveInput同时恢复所有中断
        print("\n【步骤2】使用InteractiveInput同时恢复所有中断")
        user_input = InteractiveInput()

        # 根据中断提示内容智能填充恢复数据
        for chunk in interaction_chunks:
            interaction_id = chunk.payload.id
            interaction_value = str(chunk.payload.value)

            # 根据提示内容判断应该填充什么数据
            if "跳转" in interaction_value or "确认" in interaction_value or "手机银行" in interaction_value:
                user_input.update(interaction_id, "是，确认跳转手机银行操作界面")
                print(f"  填充中断 [{interaction_id}]: 是，确认跳转手机银行操作界面")
            elif "存钱" in interaction_value or "取钱" in interaction_value or "金额" in interaction_value:
                # 提供完整信息：银行+操作+金额
                user_input.update(interaction_id, "在民生银行存钱5000元")
                print(f"  填充中断 [{interaction_id}]: 在民生银行存钱5000元")
            else:
                # 默认策略：根据ID名称判断
                if "interactive" in interaction_id:
                    user_input.update(interaction_id, "是，确认跳转手机银行操作界面")
                    print(f"  填充中断 [{interaction_id}]: 是，确认跳转手机银行操作界面")
                elif "questioner" in interaction_id:
                    user_input.update(interaction_id, "在民生银行存钱5000元")
                    print(f"  填充中断 [{interaction_id}]: 在民生银行存钱5000元")

        message2 = Event.create_user_event(content=user_input, conversation_id=conversation_id)
        chunks2 = []
        stream2 = Runner.run_agent_group_streaming(group, message2)
        async for chunk in stream2:
            chunks2.append(chunk)
            print(f"single_agent group message2 chunk: {chunk}")

        # 验证workflow完成
        final_chunk = None
        for chunk in chunks2:
            if chunk.type == 'workflow_final':
                final_chunk = chunk
                break

        self.assertIsNotNone(final_chunk, "应该有 workflow_final 输出")

        # 验证最终结果包含预期的数据
        final_payload = final_chunk.payload
        print(f"✅ 步骤2成功：workflow 成功完成，结果: {final_payload}")

        # 验证结果包含关键信息
        if isinstance(final_payload, dict):
            response_str = final_payload.get('response', str(final_payload))
        else:
            response_str = str(final_payload)

        self.assertIn("5000", response_str, "结果应包含金额5000")
        self.assertIn("存钱", response_str, "结果应包含操作类型：存钱")
        self.assertIn("确认跳转手机银行操作界面", response_str, "结果应包含确认信息")

        print("\n🎉 test_hierarchical_group_014 测试完成！")
        print("   ✓ 成功创建包含多个workflow的WorkflowAgent")
        print("   ✓ 成功创建LLMAgent和ReactAgent")
        print("   ✓ 成功创建HierarchicalGroup并添加所有agents")
        print("   ✓ 使用Runner.run_agent_group_streaming进行流式交互")
        print("   ✓ 第一次调用返回2个并行中断（同一个超步）")
        print("   ✓ 使用InteractiveInput同时恢复多个中断组件")
        print("   ✓ 验证：通过HierarchicalGroup时，InteractiveInput能跳过意图识别直接恢复")
        print("   ✓ 验证：通过WorkflowAgent时，InteractiveInput的数据能正确传给对应node id")

    def _build_cash_access_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str
    ) -> Workflow:
        """
        构建银行存取钱工作流（包含interactive确认和questioner提问两个中断组件）
        注意：interactive和questioner在同一个超步（并行执行）

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            workflow_desc: 工作流描述

        Returns:
            Workflow: 包含并行中断的工作流
                     start -> interactive \
                              questioner  -> end
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
                description=workflow_desc,
            )
        flow = Workflow(card=card)

        # 创建组件
        start = self._create_start_component()

        # 创建interactive确认组件
        interactive = InteractiveConfirmComponent("interactive")

        # 创建questioner提问组件
        key_fields = [
            FieldInfo(field_name="bank", description="银行名称", required=True),
            FieldInfo(field_name="action", description="操作类型（存钱/取钱）", required=True),
            FieldInfo(field_name="amount", description="金额（数字）", required=True),
        ]
        model_config = self._create_model_config()
        # client_provider 需要使用正确的大小写格式 (OpenAI, SiliconFlow)
        provider = model_config.model_provider
        if provider and provider.lower() == 'openai':
            provider = 'OpenAI'
        elif provider and provider.lower() == 'siliconflow':
            provider = 'SiliconFlow'
        questioner_config = QuestionerConfig(
            model_client_config=ModelClientConfig(
                client_provider=provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=model_config.model_info.temperature,
                top_p=model_config.model_info.top_p,
            ),
            question_content="请您提供明确用户操作：存钱 还是 取钱, 具体金额相关的信息",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({"responseTemplate": "银行操作完成: bank={{bank}}, "
                                       "action={{action}}, amount={{amount}}, confirm={{confirm_result}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "interactive", interactive, inputs_schema={"query": "${start.query}"}
        )
        flow.add_workflow_comp(
            "questioner", questioner, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end", end,
            inputs_schema={
                "bank": "${questioner.bank}",
                "action": "${questioner.action}",
                "amount": "${questioner.amount}",
                "confirm_result": "${interactive.confirm_result}"
            }
        )

        # 连接拓扑: start -> [interactive, questioner] -> end (并行，同一个超步)
        # 使用列表语法让start同时触发两个节点，end等待所有节点完成
        flow.add_connection("start", "interactive")
        flow.add_connection("start", "questioner")
        # 使用列表语法创建barrier，让end等待interactive和questioner都完成
        flow.add_connection(["interactive", "questioner"], "end")

        return flow

    def _build_cipher_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str
    ) -> Workflow:
        """
        构建支付密码工作流（包含两次interactive中断）

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            workflow_desc: 工作流描述

        Returns:
            Workflow: 包含 start -> interactive1 -> interactive2 -> end 的工作流
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
                description=workflow_desc,

        )
        flow = Workflow(card=card)

        # 创建组件
        start = self._create_start_component()

        # 创建两个interactive组件
        interactive1 = InteractivePasswordComponent("interactive1", "请输入支付密码")
        interactive2 = InteractivePasswordComponent("interactive2", "再次输入支付密码")

        # End 组件
        end = End({"responseTemplate": "支付密码设置完成: password1={{password1}}, password2={{password2}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "interactive1", interactive1, inputs_schema={"query": "${start.query}"}
        )
        flow.add_workflow_comp(
            "interactive2", interactive2, inputs_schema={"password1": "${interactive1.password}"}
        )
        flow.set_end_comp(
            "end", end,
            inputs_schema={
                "password1": "${interactive1.password}",
                "password2": "${interactive2.password}"
            }
        )

        # 连接拓扑: start -> interactive1 -> interactive2 -> end
        flow.add_connection("start", "interactive1")
        flow.add_connection("interactive1", "interactive2")
        flow.add_connection("interactive2", "end")

        return flow

    def _create_workflow_agent_multi(
            self,
            agent_id: str,
            description: str,
            workflows: list
    ) -> WorkflowAgent:
        """创建包含多个Workflow的WorkflowAgent"""
        config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description=description,
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows(workflows)
        return agent


# ============ 自定义Interactive组件 ============


class InteractiveConfirmComponent(WorkflowComponent):
    """
    交互确认组件 - 用于用户确认操作
    """

    def __init__(self, comp_id: str):
        super().__init__()
        self.comp_id = comp_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 请求用户确认
        confirm = await session.interact("是否跳转手机银行操作界面")
        return {"confirm_result": confirm}


class InteractivePasswordComponent(WorkflowComponent):
    """
    交互密码输入组件
    """

    def __init__(self, comp_id: str, prompt: str):
        super().__init__()
        self.comp_id = comp_id
        self.prompt = prompt

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 请求用户输入密码
        password = await session.interact(self.prompt)
        return {"password": password}


if __name__ == "__main__":
    unittest.main()
