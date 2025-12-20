import os
import unittest
from datetime import datetime
from typing import List

from jiuwen.agent.common.schema import PluginSchema, WorkflowSchema
from jiuwen.agent.react_agent import create_react_agent_config, create_react_agent, ReActAgent
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.stream.base import OutputSchema
from jiuwen.core.utils.llm.base import BaseModelInfo
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from jiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata, WorkflowInputsSchema
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.component.questioner_comp import QuestionerComponent, QuestionerConfig, FieldInfo

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class MockInteractiveTool:
    """模拟交互式工具，用于测试中断恢复功能"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def invoke(self, args: dict):
        """模拟工具调用，返回交互请求"""
        action = args.get("action", "")
        details = args.get("details", "")

        # 返回交互请求格式，触发中断
        return [
            {
                "type": "__interaction__",
                "payload": {
                    "id": "user_confirmation",
                    "value": f"需要确认操作：{action}。详情：{details}。请回答 yes 或 no："
                }
            }
        ]


class ReActAgentTest(unittest.IsolatedAsyncioTestCase):  # ① 关键改动
    @staticmethod
    def _create_model():
        return ModelConfig(model_provider=MODEL_PROVIDER,
                           model_info=BaseModelInfo(  # type: ignore
                               model=MODEL_NAME,
                               api_base=API_BASE,
                               api_key=API_KEY,
                               temperature=0.7,
                               top_p=0.9,
                               timeout=30  # 添加超时设置
                           ))

    @staticmethod
    def _create_tool():
        weather_plugin = RestfulApi(
            name="WeatherReporter",
            description="天气查询插件",
            params=[
                Param(name="location", description="天气查询的地点，必须为英文", type="string", required=True),
                Param(name="date", description="天气查询的时间，格式为YYYY-MM-DD", type="string", required=True),
            ],
            path="http://******",
            headers={},
            method="GET",
            response=[],
        )
        return weather_plugin

    @staticmethod
    def _create_interactive_tool():
        """创建交互式工具用于测试中断恢复"""
        return MockInteractiveTool(
            name="UserConfirmation",
            description="用户确认工具，需要用户交互"
        )

    @staticmethod
    def _create_tool_schema():
        tool_info = PluginSchema(
            name='WeatherReporter',
            description='天气查询插件',
            inputs={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "天气查询的地点。\n注意：地点名称必须为英文",
                        "required": True
                    },
                    "date": {
                        "type": "string",
                        "description": "天气查询的时间，格式为YYYY-MM-DD",
                        "required": True
                    }
                }
            }
        )
        return tool_info

    @staticmethod
    def _create_interactive_tool_schema():
        """创建交互式工具的schema"""
        return PluginSchema(
            name='UserConfirmation',
            description='用户确认工具，需要用户交互确认操作',
            inputs={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "需要确认的操作名称",
                        "required": True
                    },
                    "details": {
                        "type": "string",
                        "description": "操作的详细信息",
                        "required": True
                    }
                }
            }
        )

    @staticmethod
    def _create_prompt_template():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    @staticmethod
    def _create_interactive_prompt_template():
        """创建支持交互的提示模板"""
        system_prompt = "你是一个AI助手，可以调用工具帮助用户完成任务。当需要用户确认时，请调用UserConfirmation工具。今天的日期为：{}"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    @unittest.skip
    async def test_react_agent_invoke_with_workflow_interrupt_agent_invoke(self):
        react_agent_prompt_template = self._create_prompt_template()

        questioner_workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name="questioner",
                id="questioner_workflow",
                version="1.0",
                description="用户输入"
            ),
            workflow_inputs_schema = WorkflowInputsSchema(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "用户输入",
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

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))
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

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[workflow_schema],
            model=model_config,
            prompt_template=react_agent_prompt_template
        )

        # react_agent要创建，但要打桩下面的逻辑：1. 大模型创建； 2. 大模型输出
        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[flow],
            tools=[]
        )

        # 第一次大模型返回的结果要让调用sub_task0
        result = await react_agent.invoke({"conversation_id": "12345", "query": "查询今天天气"})
        print(f"ReActAgent 第一次输出结果：{result}")

        # 第二次大模型返回的结果不让调用sub_task
        if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "杭州")
            result = await react_agent.invoke({"conversation_id": "12345", "query": interactive_input})

            print(f"ReActAgent 第二次输出结果：{result}")


    @unittest.skip
    async def test_react_agent_invoke_with_workflow_interrupt_with_stream(self):
        react_agent_prompt_template = self._create_prompt_template()

        questioner_workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name="questioner",
                id="questioner_workflow",
                version="1.0",
                description="用户输入"
            ),
            workflow_inputs_schema = WorkflowInputsSchema(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "用户输入",
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

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))
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


        react_agent_config = create_react_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[workflow_schema],
            model=model_config,
            prompt_template=react_agent_prompt_template
        )

        # react_agent要创建，但要打桩下面的逻辑：1. 大模型创建； 2. 大模型输出
        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[flow],
            tools=[]
        )

        # 第一次大模型返回的结果要让调用sub_task0
        interaction_output_schema = []
        async for chunk in react_agent.stream({"conversation_id": "12345", "query": "查询今天天气"}):
            print(f"ReActAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)

        if interaction_output_schema:
            user_input = InteractiveInput()
            for item in interaction_output_schema:
                component_id = item.payload.id
                user_input.update(component_id, "杭州")
            async for chunk in react_agent.stream({"conversation_id": "12345", "query": user_input}):
                print(f"ReActAgent 第二次输出结果 >>> {chunk}")
