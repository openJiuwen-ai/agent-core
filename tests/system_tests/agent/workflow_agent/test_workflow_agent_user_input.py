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


if __name__ == "__main__":
    unittest.main()
