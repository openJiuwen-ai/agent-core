import os
import unittest
from datetime import datetime
from typing import List
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.common.constants.enums import ControllerType, TaskType
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.application.llm_agent import (
    create_llm_agent_config,
    create_llm_agent,
    LLMAgent
)
from openjiuwen.core.application.workflow_agent import (
    WorkflowAgent
)
from openjiuwen.core.controller import Task, TaskInput
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.foundation.llm import ModelConfig, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.workflow import End, WorkflowCard
from openjiuwen.core.workflow import FieldInfo, QuestionerConfig, QuestionerComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.session import FORCE_DEL_WORKFLOW_STATE_ENV_KEY
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.foundation.llm import BaseModelInfo, Model
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.workflow import Workflow

os.environ["LLM_SSL_VERIFY"] = "false"

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

class MockLLMModel:
    def model_provider(self):
        return MODEL_PROVIDER
    
    def invoke(self, model_name, messages, tools=None):
        """Mock invoke method for Questioner component"""
        # Return a mock response with extracted fields
        return AssistantMessage(
            content='{"location": "hangzhou", "time": "today"}',
            tool_calls=[],
            usage_metadata=UsageMetadata(input_tokens=10, output_tokens=20, total_tokens=30)
        )


@patch.dict(os.environ, {FORCE_DEL_WORKFLOW_STATE_ENV_KEY: "True"})
class TestReActAgentInterrupt:  # ① 关键改动
    @staticmethod
    def _create_model():
        return ModelConfig(model_provider=MODEL_PROVIDER,
                           model_info=BaseModelInfo(
                               model=MODEL_NAME,
                               api_base=API_BASE,
                               api_key=API_KEY,
                               temperature=0.7,
                               top_p=0.9,
                               timeout=30  # 添加超时设置
                           ))

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
    def _create_prompt_template():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工作流，帮助我查询一下天气"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    # 临时关闭
    @unittest.skip("skip system test")
    @pytest.mark.asyncio
    @patch("openjiuwen.single_agent.llm_agent.llm_controller.LLMController._generate_plan_from_llm")
    @patch(
        "openjiuwen.core.workflow.components.llm_related.questioner_comp."
        "QuestionerDirectReplyHandler._invoke_llm_for_extraction"
    )
    @patch(
        "openjiuwen.core.workflow.component.basic_components.questioner_comp."
        "QuestionerDirectReplyHandler._build_llm_inputs"
    )
    @patch("openjiuwen.core.foundation.llm.model.Model")
    async def test_react_agent_invoke_with_workflow_interrupt(self, mock_get_model, mock_llm_inputs,
                                                               mock_extraction, mock_generate_plan_from_llm):
        mock_get_model.return_value = MockLLMModel()

        react_agent_prompt_template = self._create_prompt_template()

        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]

        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(location="hangzhou")

        questioner_workflow_card = WorkflowCard(
                name="questioner",
                id="questioner_workflow",
                version="1.0",
            )

        flow = Workflow(card=questioner_workflow_card)

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        model_config = self._create_model()
        questioner_config = QuestionerConfig(
            model_config=self._create_model_request_config(),
            model_client_config=self._create_model_client_config(),
            question_content="查询什么城市的天气",
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
            id=flow.card.id,
            name=flow.card.name,
            version=flow.card.version,
            description="追问器工作流",
            inputs={"query": {
                "type": "string",
            }}
        )

        task = Task(
            id = workflow_schema.id,
            task_type = TaskType.WORKFLOW,
            input = TaskInput(
                target_id = f"{workflow_schema.id}_{workflow_schema.version}",
                target_name = workflow_schema.name,
            )
        )

        react_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[workflow_schema],
            model=model_config,
            prompt_template=react_agent_prompt_template
        )

        # react_agent要创建，但要打桩下面的逻辑：1. 大模型创建； 2. 大模型输出
        react_agent: LLMAgent = create_llm_agent(
            agent_config=react_agent_config,
            workflows=[flow],
            tools=[]
        )

        # 第一次大模型返回的结果让调用task
        # 返回格式改为元组: (tasks, llm_output)
        mock_generate_plan_from_llm.return_value = (
            [task],
            AssistantMessage(content="This is first mock LLM output"),
        )

        result = await react_agent.invoke({"conversation_id": "12345", "query": "查询杭州的天气"})
        print(f"LLMAgent 第一次输出结果：{result}")

        # 第二次大模型返回的结果不让调用task
        # 返回格式改为元组: (tasks, llm_output)
        mock_generate_plan_from_llm.return_value = (
            [],
            AssistantMessage(content="This is second mock LLM output"),
        )
        if result.get("result_type") == 'question':
            result = await react_agent.invoke({"conversation_id": "12345", "query": "查询杭州天气"})
            print(f"LLMAgent 第二次输出结果：{result}")

    @patch("openjiuwen.core.foundation.llm.model.Model")
    @patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config", return_value=MagicMock())
    @pytest.mark.asyncio
    async def test_real_react_agent_invoke_with_workflow_interrupt(self, mock_set_scope, mock_get_model):
        # Mock LLM model
        mock_get_model.return_value = MockLLMModel()
        
        react_agent_prompt_template = self._create_prompt_template()

        prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]

        questioner_workflow_card = WorkflowCard(
                name="questioner",
                id="questioner_workflow",
                version="1.0",
            )

        flow = Workflow(card=questioner_workflow_card)

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{output}}"})

        model_config = ModelConfig(model_provider="OpenAI",
                                   model_info=BaseModelInfo(
                                       model="gpt-4",
                                       api_base="mock-url",
                                       api_key="mock-key",
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30
                                   ))

        questioner_config = QuestionerConfig(
            model_config=self._create_model_request_config(),
            model_client_config=self._create_model_client_config(),
            question_content="查询什么城市的天气",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False
        )
        questioner_component = QuestionerComponent(questioner_comp_config=questioner_config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${questioner.userFields.key_fields}"})
        flow.add_workflow_comp("questioner", questioner_component, inputs_schema={"query": "${start.query}"})

        flow.add_connection("s", "questioner")
        flow.add_connection("questioner", "e")

        workflow_schema = WorkflowSchema(
            id=flow.card.id,
            name=flow.card.name,
            version=flow.card.version,
            description="追问器工作流",
            inputs={"query": {
                "type": "string",
            }}
        )

        task = Task(
            id=workflow_schema.id,
            task_type=TaskType.WORKFLOW,
            input=TaskInput(
                target_id=f"{workflow_schema.id}_{workflow_schema.version}",
                target_name=workflow_schema.name,
            )
        )

        react_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[workflow_schema],
            model=model_config,
            prompt_template=react_agent_prompt_template
        )

        # react_agent要创建，但要打桩下面的逻辑：1. 大模型创建； 2. 大模型输出
        react_agent: LLMAgent = create_llm_agent(
            agent_config=react_agent_config,
            workflows=[flow],
            tools=[]
        )

        # 第一次大模型返回的结果要让调用task
        try:
            result = await react_agent.invoke({"conversation_id": "12345", "query": "查询今天天气"})
            print(f"LLMAgent 第一次输出结果：{result}")

            # 第二次大模型返回的结果不让调用task
            if result.get("result_type") == 'question':
                result = await react_agent.invoke({"conversation_id": "12345", "query": "查询杭州天气"})
                print(f"LLMAgent 第二次输出结果：{result}")
        except JiuWenBaseException:
            assert True


    @pytest.mark.asyncio
    @patch(
        "openjiuwen.core.workflow.components.llm_related.questioner_comp."
        "QuestionerDirectReplyHandler._invoke_llm_for_extraction"
    )
    @patch("openjiuwen.core.foundation.llm.model.Model")
    async def test_real_workflow_agent_invoke_with_workflow_interrupt(self, mock_get_model, mock_extraction):
        # Mock LLM model
        mock_get_model.return_value = MockLLMModel()
        # Mock extraction to return expected fields
        mock_extraction.return_value = {"location": "hangzhou", "time": "today"}
        
        questioner_workflow_card = WorkflowCard(
                name="questioner",
                id="questioner_workflow",
                version="1.0",
        )

        flow = Workflow(card=questioner_workflow_card)

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})


        model_config = ModelConfig(model_provider="OpenAI",
                                   model_info=BaseModelInfo(
                                       model="gpt-4",
                                       api_base="mock-url",
                                       api_key="mock-key",
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30
                                   ))

        questioner_config = QuestionerConfig(
            model_config=self._create_model_request_config(),
            model_client_config=self._create_model_client_config(),
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
            id=flow.card.id,
            name=flow.card.name,
            version=flow.card.version,
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

        config = WorkflowAgentConfig(
            id="write_agent",
            version="0.1.0",
            description="interrupt workflow single_agent",
            workflows=[workflow_schema],
            controller_type=ControllerType.WorkflowController,
        )

        workflow_agent = WorkflowAgent(config)
        workflow_agent.bind_workflows([flow])

        result = await workflow_agent.invoke({"conversation_id": "12345", "query": "查询今天天气"})
        print(f"WorkflowAgent 第一次输出结果：{result}")

        if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "杭州")
            result = await workflow_agent.invoke({"conversation_id": "12345", "query": interactive_input})
            print(f"WorkflowAgent 第二次输出结果：{result}")

    @pytest.mark.asyncio
    @patch(
        "openjiuwen.core.workflow.components.llm_related.questioner_comp."
        "QuestionerDirectReplyHandler._invoke_llm_for_extraction"
    )
    @patch("openjiuwen.core.foundation.llm.model.Model")
    async def test_real_workflow_agent_stream_with_workflow_interrupt(self, mock_get_model, mock_extraction):
        # Mock LLM model
        mock_get_model.return_value = MockLLMModel()
        # Mock extraction to return expected fields
        mock_extraction.return_value = {"location": "hangzhou", "time": "today"}
        
        questioner_workflow_card = WorkflowCard(
                name="questioner",
                id="questioner_workflow",
                version="1.0",

        )

        flow = Workflow(card=questioner_workflow_card)

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})


        model_config = ModelConfig(model_provider="OpenAI",
                                   model_info=BaseModelInfo(
                                       model="gpt-4",
                                       api_base="mock-url",
                                       api_key="mock-key",
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30
                                   ))

        questioner_config = QuestionerConfig(
            model_config=self._create_model_request_config(),
            model_client_config=self._create_model_client_config(),
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
            id=flow.card.id,
            name=flow.card.name,
            version=flow.card.version,
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

        config = WorkflowAgentConfig(
            id="write_agent",
            version="0.1.0",
            description="interrupt workflow single_agent",
            workflows=[workflow_schema],
            controller_type=ControllerType.WorkflowController,
        )

        workflow_agent = WorkflowAgent(config)
        workflow_agent.bind_workflows([flow])

        interaction_output_schema = []
        async for result in workflow_agent.stream({"conversation_id": "12345", "query": "查询今天天气"}):
            print(f"WorkflowAgent stream 第一次输出结果：{result}")
            if isinstance(result, OutputSchema) and result.type == "__interaction__":
                interaction_output_schema.append(result)


        if interaction_output_schema:
            user_input = InteractiveInput()
            for item in interaction_output_schema:
                component_id = item.payload.id
                user_input.update(component_id, "杭州")
            async for chunk in workflow_agent.stream({"conversation_id": "12345", "query": user_input}):
                print(f"WorkflowAgent 第二次输出结果 >>> {chunk}")

