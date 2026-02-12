#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
测试 WorkflowController 的交互逻辑改进
测试场景：
1. dict类型中断（组件返回结构化数据）- 应该再次返回中断
2. str类型中断（人机交互文本）- 应该正常执行工作流
"""
import os
import uuid

import unittest
from dataclasses import dataclass, field
from typing import Any, List

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import ComponentConfig, WorkflowCard, WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.common.logging import logger

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["RESTFUL_SSL_VERIFY"] = "false"
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


# ============ 自定义组件：返回dict格式的中断 ============

@dataclass
class UserInputElem:
    """单个用户输入字段配置"""
    input_name: str
    input_description: str = field(default="")
    required: bool = field(default=True)
    default_value: Any = field(default=None)


@dataclass
class UserInputsConfig(ComponentConfig):
    """用户输入组件配置"""
    inputs: List[UserInputElem] = field(default_factory=list)


class UserInputComponent(WorkflowComponent):
    """
    用户输入组件 - 返回dict格式的中断
    
    这个组件会请求用户提供结构化的输入数据，
    返回的是一个字典，而不是简单的字符串。
    """

    def __init__(self, conf: UserInputsConfig) -> None:
        super().__init__()
        self.input_conf_list: List[UserInputElem] = conf.inputs

    async def invoke(self, inputs: Input, session: Session, context: Any) -> Output:
        # 准备要请求的字段列表（dict格式）
        request_dict = {
            elem.input_name: {
                "description": elem.input_description,
                "required": elem.required,
                "default": elem.default_value
            }
            for elem in self.input_conf_list
        }

        # 使用 session.interact 请求用户输入（传入dict）
        result = await session.interact(request_dict)

        # 验证必填字段
        for input_elem in self.input_conf_list:
            if not isinstance(input_elem, UserInputElem):
                raise ValueError("Node data type is wrong")
            if result.get(input_elem.input_name) is None and input_elem.required is True:
                raise build_error(StatusCode.ERROR)
        return result


# ============ 测试用例 ============

class WorkflowAgentUserInputTest(unittest.IsolatedAsyncioTestCase):
    """测试 WorkflowController 对不同类型中断的处理逻辑"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_start_component():
        return Start()

    def _build_user_input_workflow(self) -> Workflow:
        """
        构建包含UserInputComponent的工作流
        这个工作流会产生dict格式的中断
        """
        workflow_card = WorkflowCard(
                name="用户输入工作流",
                id="user_input_flow",
                version="1.0",
                description="测试dict格式中断的工作流",
                input_params=dict(
                    type="object",
                    properties={
                        "query": {
                            "type": "string",
                            "description": "用户输入"
                        }
                    }
                )
        )
        flow = Workflow(card=workflow_card)

        # 创建组件
        start = self._create_start_component()

        # UserInputComponent - 请求用户提供location和date
        user_input_config = UserInputsConfig(
            inputs=[
                UserInputElem(
                    input_name="location",
                    input_description="请提供地点信息",
                    required=True
                ),
                UserInputElem(
                    input_name="date",
                    input_description="请提供日期信息",
                    required=False,
                    default_value="今天"
                )
            ]
        )
        user_input_comp = UserInputComponent(user_input_config)

        end = End({"responseTemplate": "收到您的输入：地点={{location}}，日期={{date}}"})

        # 连接工作流
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("user_input", user_input_comp, inputs_schema={})
        flow.set_end_comp("end", end, inputs_schema={
            "location": "${user_input.location}",
            "date": "${user_input.date}"
        })

        flow.add_connection("start", "user_input")
        flow.add_connection("user_input", "end")

        return flow

    @unittest.skip("skip system test")
    async def test_dict_interrupt_should_return_again(self):
        """
        测试场景1：dict类型中断应该再次返回
        
        当用户再次发送查询时，如果上一次中断的payload.value是dict（组件返回的结构化数据），
        控制器应该再次返回这个中断，而不是继续执行工作流。
        """
        # 1. 创建工作流和agent
        workflow = self._build_user_input_workflow()
        agent_config = WorkflowAgentConfig(
            id="test_dict_interrupt_agent",
            version="0.1.0",
            description="测试dict中断逻辑",
            workflows=[]  # 初始为空
        )
        agent = WorkflowAgent(agent_config)
        agent.add_workflows([workflow])  # 使用add_workflows添加工作流

        # 使用固定的 conversation_id 保持会话状态
        session_id = "test-dict-interrupt-001"

        # 2. 第一次调用 - 触发中断
        result1 = []
        async for chunk in agent.stream({"query": "我想查询天气", "conversation_id": session_id}):
            if isinstance(chunk, OutputSchema):
                result1.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"  完整payload: {chunk.payload}")

        # 验证第一次调用产生了中断
        interaction_chunks = [c for c in result1 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks), 1, "应该产生一个中断")

        first_interrupt = interaction_chunks[0]
        self.assertIsInstance(first_interrupt.payload.value, dict, "第一次中断应该是dict类型")
        logger.info(f"第一次中断payload.value: {first_interrupt.payload.value}")

        # 3. 第二次调用 - 用户再次查询（不提供InteractiveInput）
        # 根据需求：控制器应该识别出上次是dict中断，再次返回这个中断
        logger.info("\n--- 第二次调用：用户再次查询（不提供输入） ---")
        result2 = []
        async for chunk in agent.stream({"query": "还是想查询天气", "conversation_id": session_id}):
            if isinstance(chunk, OutputSchema):
                result2.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"  完整payload: {chunk.payload}")

        # 验证第二次调用也产生了中断（再次返回）
        interaction_chunks_2 = [c for c in result2 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks_2), 1, "应该再次返回中断")

        second_interrupt = interaction_chunks_2[0]
        self.assertIsInstance(second_interrupt.payload.value, dict, "第二次中断也应该是dict类型")
        logger.info(f"第二次中断payload.value: {second_interrupt.payload.value}")

        # 4. 第三次调用 - 提供正确的InteractiveInput
        logger.info("\n--- 第三次调用：提供InteractiveInput ---")
        interactive_input = InteractiveInput()
        interactive_input.update("user_input", {"location": "北京", "date": "明天"})

        result3 = []
        async for chunk in agent.stream(
                {"query": interactive_input, "conversation_id": session_id}
        ):
            if isinstance(chunk, OutputSchema):
                result3.append(chunk)
                if chunk.type == "workflow_final":
                    logger.info(f"工作流完成: {chunk.payload}")

        # 验证第三次调用完成了工作流
        final_chunks = [c for c in result3 if c.type == "workflow_final"]
        self.assertEqual(len(final_chunks), 1, "应该有一个workflow_final")
        logger.info(f"最终结果: {final_chunks[0].payload}")

    @unittest.skip("skip system test")
    async def test_str_interrupt_should_continue_with_questioner(self):
        """
        测试场景2：str类型中断应该正常执行
        
        当用户提供文本回复时，如果上一次中断的payload.value是str（人机交互），
        控制器应该正常执行工作流。
        
        注：由于QuestionerComponent返回的是str，这个测试使用Questioner
        """
        logger.info("\n========== 测试：str类型中断应该正常执行 ==========")

        # 导入QuestionerComponent
        from openjiuwen.core.workflow.components.llm.questioner_comp import (
            QuestionerComponent, QuestionerConfig, FieldInfo)

        # 创建模型配置
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=120,
            max_retries=3,
            verify_ssl=False
        )
        model_request_config = ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.7,
            top_p=0.9
        )

        # 1. 构建包含Questioner的工作流
        workflow_card = WorkflowCard(
                name="问答工作流",
                id="questioner_flow",
                version="1.0",
                description="测试str格式中断的工作流"
        )
        flow = Workflow(card=workflow_card)

        start = self._create_start_component()
        questioner_config = QuestionerConfig(
            model_config=model_request_config,
            model_client_config=model_client_config,
            question_content="请您提供地点相关的信息",
            field_names=[
                FieldInfo(field_name="location", description="地点")
            ]
        )
        questioner = QuestionerComponent(questioner_config)
        end = End({"responseTemplate": "收到您的回答：{{location}}"})

        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("questioner", questioner, inputs_schema={})
        flow.set_end_comp("end", end, inputs_schema={"location": "${questioner.location}"})
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    def _build_questioner_workflow(self) -> Workflow:
        """
        构建包含QuestionerComponent的工作流
        这个工作流会产生str格式的中断
        """
        from openjiuwen.core.workflow.components.llm.questioner_comp import (
            QuestionerComponent, QuestionerConfig, FieldInfo)

        # 创建模型配置
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=120,
            max_retries=3,
            verify_ssl=False
        )
        model_request_config = ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.7,
            top_p=0.9
        )

        workflow_card = WorkflowCard(
                name="问答工作流",
                id="questioner_flow",
                version="1.0",
                description="测试str格式中断的工作流"
        )
        flow = Workflow(card=workflow_card)

        start = self._create_start_component()
        questioner_config = QuestionerConfig(
            model_config=model_request_config,
            model_client_config=model_client_config,
            question_content="请您提供地点相关的信息",
            field_names=[
                FieldInfo(field_name="location", description="地点")
            ]
        )
        questioner = QuestionerComponent(questioner_config)
        end = End({"responseTemplate": "收到您的回答：{{location}}"})

        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("questioner", questioner, inputs_schema={})
        flow.set_end_comp("end", end, inputs_schema={"location": "${questioner.location}"})
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    @unittest.skip("skip system test")
    async def test_str_interrupt_should_continue(self):
        """
        测试场景2：str类型中断应该正常执行
        
        当用户提供文本回复时，如果上一次中断的payload.value是str（人机交互），
        控制器应该正常执行工作流。
        
        注：由于QuestionerComponent返回的是str，这个测试使用Questioner
        """
        logger.info("\n========== 测试：str类型中断应该正常执行 ==========")

        # 1. 构建包含Questioner的工作流
        flow = self._build_questioner_workflow()

        # 2. 创建agent
        agent_config = WorkflowAgentConfig(
            id="test_str_interrupt_agent",
            version="0.1.0",
            description="测试str中断逻辑",
            workflows=[]  # 初始为空
        )
        agent = WorkflowAgent(agent_config)
        agent.add_workflows([flow])  # 使用add_workflows添加工作流

        # 使用固定的 conversation_id 保持会话状态
        session_id = "test-str-interrupt-001"

        # 3. 第一次调用 - 触发中断（Questioner返回str）
        result1 = []
        async for chunk in agent.stream({"query": "我想查询天气", "conversation_id": session_id}):
            if isinstance(chunk, OutputSchema):
                result1.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"【str中断完整数据】")
                    logger.info(f"  完整payload: {chunk.payload}")

        interaction_chunks = [c for c in result1 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks), 1, "应该产生一个中断")

        first_interrupt = interaction_chunks[0]
        self.assertIsInstance(first_interrupt.payload.value, str, "Questioner的中断应该是str类型")
        logger.info(f"第一次中断payload.value: {first_interrupt.payload.value}")

        # 4. 第二次调用 - 用户只传入query（不是InteractiveInput）
        # 根据需求：控制器识别出上次是str中断（人机交互），应该正常继续执行
        logger.info("\n--- 第二次调用：用户只传入query（不是InteractiveInput） ---")
        result2 = []
        async for chunk in agent.stream(
                {"query": "我想查天津的天气", "conversation_id": session_id}
        ):
            logger.info(f"流式输出：{chunk}")
            if isinstance(chunk, OutputSchema):
                result2.append(chunk)
                if chunk.type == "workflow_final":
                    logger.info(f"工作流完成: {chunk.payload}")

        # 验证第二次调用完成了工作流
        final_chunks = [c for c in result2 if c.type == "workflow_final"]
        self.assertEqual(len(final_chunks), 1, "应该完成工作流（str中断+普通query）")
        logger.info(f"第二次调用最终结果: {final_chunks[0].payload}")

        # 5. 第三次调用 - 用户提供InteractiveInput（模拟用户明确回复）
        # 清理上一次的状态，重新触发一次中断进行测试
        logger.info("\n--- 第三次调用：重新触发中断 ---")
        result3 = []
        async for chunk in agent.stream(
                {"query": "再查一次天气", "conversation_id": session_id}
        ):
            if isinstance(chunk, OutputSchema):
                result3.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"\n【第三次触发str中断】")
                    logger.info(f"  chunk.payload.value: {chunk.payload.value}")

        # 验证第三次调用产生了中断
        interaction_chunks_3 = [c for c in result3 if c.type == "__interaction__"]
        if len(interaction_chunks_3) > 0:
            logger.info("第三次成功触发中断")

            # 6. 第四次调用 - 用户提供InteractiveInput
            logger.info("\n--- 第四次调用：提供InteractiveInput（明确回复） ---")
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "上海")

            result4 = []
            async for chunk in agent.stream(
                    {"query": interactive_input, "conversation_id": session_id}
            ):
                if isinstance(chunk, OutputSchema):
                    result4.append(chunk)
                    if chunk.type == "workflow_final":
                        logger.info(f"工作流完成: {chunk.payload}")

            # 验证第四次调用完成了工作流
            final_chunks_4 = [c for c in result4 if c.type == "workflow_final"]
            self.assertEqual(len(final_chunks_4), 1, "应该完成工作流（str中断+InteractiveInput）")
            logger.info(f"第四次调用最终结果: {final_chunks_4[0].payload}")

    @unittest.skip("skip system test")
    async def test_workflow_jump_with_mixed_interrupts(self):
        """
        测试场景3：工作流跳转与混合中断类型
        
        测试流程：
        1. 配置两个工作流：
           - user_input_flow: 包含UserInputComponent（dict类型中断）
           - questioner_flow: 包含QuestionerComponent（str类型中断）
        2. 先执行user_input_flow，触发dict中断
        3. 第二次调用跳转执行questioner_flow，触发str中断
        4. 完成questioner_flow
        5. 第三次调用回到user_input_flow，应该再次返回dict中断
        6. 完成user_input_flow
        """
        logger.info("\n========== 测试：工作流跳转与混合中断类型 ==========")

        # 1. 创建两个工作流
        user_input_workflow = self._build_user_input_workflow()
        questioner_workflow = self._build_questioner_workflow()

        # 更新workflow的metadata.description，用于意图识别
        user_input_workflow.card.description = "查询天气信息、温度、气象数据"
        questioner_workflow.card.description = "询问地点信息、位置查询"

        # 创建模型配置
        model_config = ModelConfig(
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

        # 2. 创建agent并添加两个工作流（配置model用于意图识别）
        agent_config = WorkflowAgentConfig(
            id="test_workflow_jump_agent",
            version="0.1.0",
            description="测试工作流跳转和混合中断",
            workflows=[],  # 初始为空
            model=model_config  # 配置意图识别模型
        )
        agent = WorkflowAgent(agent_config)
        agent.add_workflows([user_input_workflow, questioner_workflow])

        # 使用固定的 conversation_id 保持会话状态
        session_id = "test-workflow-jump-001"

        # ========== 步骤1: user_input_flow触发dict中断 ==========
        
        logger.info("\n--- 步骤1：查询天气（触发user_input_flow的dict中断）---")
        result1 = []
        async for chunk in agent.stream({
            "query": "查询天气",
            "conversation_id": session_id
        }):
            if isinstance(chunk, OutputSchema):
                result1.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"  第一次中断: {chunk.payload}")

        # 验证第一次调用产生了dict中断
        interaction_chunks_1 = [c for c in result1 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks_1), 1, "步骤1应该产生dict中断")
        first_interrupt = interaction_chunks_1[0]
        self.assertIsInstance(first_interrupt.payload.value, dict, "第一次中断应该是dict类型")
        first_component_id = first_interrupt.payload.id
        logger.info(f"✅ 步骤1完成：user_input_flow触发dict中断，component_id={first_component_id}")

        # ========== 步骤2: 跳转到questioner_flow触发str中断 ==========
        
        logger.info("\n--- 步骤2：询问位置信息（跳转到questioner_flow）---")
        result2 = []
        async for chunk in agent.stream({
            "query": "我想询问地点位置信息",
            "conversation_id": session_id
        }):
            if isinstance(chunk, OutputSchema):
                result2.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"  第二次中断（questioner的str）: {chunk.payload}")

        # 验证第二次调用产生了str中断
        interaction_chunks_2 = [c for c in result2 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks_2), 1, "步骤2应该产生str中断")
        second_interrupt = interaction_chunks_2[0]
        self.assertIsInstance(second_interrupt.payload.value, str, "第二次中断应该是str类型")
        second_component_id = second_interrupt.payload.id
        logger.info(f"✅ 步骤2完成：questioner_flow触发str中断，component_id={second_component_id}")

        # ========== 步骤3: 完成questioner_flow ==========
        
        logger.info("\n--- 步骤3：提供地点信息，完成questioner_flow ---")
        interactive_input_questioner = InteractiveInput()
        interactive_input_questioner.update(second_component_id, "上海")
        
        result3 = []
        async for chunk in agent.stream({
            "query": interactive_input_questioner,
            "conversation_id": session_id
        }):
            if isinstance(chunk, OutputSchema):
                result3.append(chunk)
                logger.info(f"  步骤3收到chunk: type={chunk.type}")
                if chunk.type == "workflow_final":
                    logger.info(f"  questioner_flow完成: {chunk.payload}")

        # 验证questioner_flow完成
        final_chunks_3 = [c for c in result3 if c.type == "workflow_final"]
        logger.info(f"步骤3收到的所有chunk类型: {[c.type for c in result3]}")
        self.assertGreaterEqual(len(final_chunks_3), 1, f"questioner_flow应该完成，实际收到: {[c.type for c in result3]}")
        logger.info(f"✅ 步骤3完成：questioner_flow执行完成")

        # ========== 步骤4: 回到user_input_flow，应该再次返回dict中断 ==========
        
        logger.info("\n--- 步骤4：查询天气温度（应该再次返回dict中断）---")
        result4 = []
        async for chunk in agent.stream({
            "query": "查询天气温度气象数据",
            "conversation_id": session_id
        }):
            if isinstance(chunk, OutputSchema):
                result4.append(chunk)
                if chunk.type == "__interaction__":
                    logger.info(f"  第四次中断（再次返回dict）: {chunk.payload}")

        # 验证第四次调用再次返回dict中断
        interaction_chunks_4 = [c for c in result4 if c.type == "__interaction__"]
        self.assertEqual(len(interaction_chunks_4), 1, "步骤4应该再次返回dict中断")
        fourth_interrupt = interaction_chunks_4[0]
        self.assertIsInstance(fourth_interrupt.payload.value, dict, "第四次中断应该是dict类型")
        self.assertEqual(fourth_interrupt.payload.id, first_component_id, "应该是同一个component_id")
        logger.info(f"✅ 步骤4完成：user_input_flow再次返回dict中断（验证dict中断特性）")

        # ========== 步骤5: 完成user_input_flow ==========
        
        logger.info("\n--- 步骤5：提供用户输入，完成user_input_flow ---")
        interactive_input_user = InteractiveInput()
        interactive_input_user.update(first_component_id, {"location": "北京", "date": "明天"})
        
        result5 = []
        async for chunk in agent.stream({
            "query": interactive_input_user,
            "conversation_id": session_id
        }):
            if isinstance(chunk, OutputSchema):
                result5.append(chunk)
                if chunk.type == "workflow_final":
                    logger.info(f"  user_input_flow完成: {chunk.payload}")

        # 验证user_input_flow完成
        final_chunks_5 = [c for c in result5 if c.type == "workflow_final"]
        self.assertEqual(len(final_chunks_5), 1, "user_input_flow应该完成")
        logger.info(f"✅ 步骤5完成：user_input_flow执行完成")

        logger.info("\n========== 测试完成：工作流跳转与混合中断类型验证通过 ==========")
        logger.info("验证了以下场景：")
        logger.info("1. ✅ user_input_flow触发dict类型中断")
        logger.info("2. ✅ 跳转到questioner_flow触发str类型中断")
        logger.info("3. ✅ questioner_flow完成")
        logger.info("4. ✅ 回到user_input_flow再次返回dict中断（验证dict中断特性）")
        logger.info("5. ✅ user_input_flow完成")


if __name__ == "__main__":
    unittest.main()
