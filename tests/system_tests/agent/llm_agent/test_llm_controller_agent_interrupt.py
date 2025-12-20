import os
import unittest
from datetime import datetime
from typing import List

from openjiuwen.agent.common.schema import WorkflowSchema
from openjiuwen.agent.llm_agent.llm_agent import create_llm_agent_config, create_llm_agent, LLMAgent
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata, WorkflowInputsSchema
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.component.questioner_comp import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class InteractiveConfirmComponent(ComponentExecutable, WorkflowComponent):
    """
    交互确认组件 - 用于用户确认操作
    """

    def __init__(self, comp_id: str):
        super().__init__()
        self.comp_id = comp_id

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        # 请求用户确认
        confirm = await runtime.interact("是否确认操作")
        return {"confirm_result": confirm}


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


class LLMAgentInterruptTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

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
            path="http://127.0.0.1:8000/weather",
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
    def _create_prompt_template():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！注意如果是查询天气，必须调用工具。今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
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

    def _build_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str
    ) -> Workflow:
        """
        构建测试工作流（包含interactive确认和questioner提问两个中断组件）
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
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
                description=workflow_desc,
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
        flow = Workflow(workflow_config=workflow_config)

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

        interactive = InteractiveConfirmComponent("interactive")
        end_component = End({"responseTemplate": "{{location}} | {{time}} | confirm={{confirm_result}}"})

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
        flow.add_workflow_comp(
            "interactive", interactive, inputs_schema={"query": "${s.query}"}
        )

        flow.add_connection("s", "questioner")
        flow.add_connection("s", "interactive")
        flow.add_connection(["interactive", "questioner"], "e")

        return flow

    def _setup_test_environment_and_agent(self):
        """Setup common test environment and create LLMAgent instance with workflow"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        llm_agent_prompt_template = self._create_prompt_template()

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

        llm_agent_config = create_llm_agent_config(
            agent_id="llm_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=llm_agent_prompt_template
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 动态绑定workflow
        llm_agent.add_workflows([flow])
            
        return llm_agent
        
    @unittest.skip("requires network")
    async def test_llm_agent_with_workflow_interrupt_agent_invoke(self):
        llm_agent = self._setup_test_environment_and_agent()

        result = await Runner.run_agent(llm_agent, {"conversation_id": "12345", "query": "昨天天气查询"})
        print(f"LLMAgent 第一次输出结果：{result}")
        self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
        self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回交互请求")

        # 第二次大模型恢复上次中断workflow
        if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
            interactive_input = InteractiveInput()
            interactive_input.update("questioner", "上海")
            result = await Runner.run_agent(llm_agent, {"conversation_id": "12345", "query": interactive_input})
            print(f"LLMAgent 第二次输出结果：{result}")

            self.assertIsInstance(result, dict, "第二次调用应该返回字典")
            self.assertEqual(result['result_type'], 'answer', "应该返回answer类型")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

    @unittest.skip("requires network")
    async def test_llm_agent_with_workflow_interrupt_with_stream(self):
        llm_agent = self._setup_test_environment_and_agent()

        interaction_output_schema = []
        async for chunk in llm_agent.stream({"query": "昨天天气查询", "conversation_id": "c123"}):
            print(f"LLMAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✅ 第一次调用校验通过：返回交互请求")

        if interaction_output_schema:
            user_input = InteractiveInput()
            for item in interaction_output_schema:
                component_id = item.payload.id
                user_input.update(component_id, "杭州")
            final_chunk = None
            async for chunk in llm_agent.stream({"query": user_input, "conversation_id": "c123"}):
                print(f"LLMAgent 第二次输出结果 >>> {chunk}")
                if chunk.type == "answer":
                    final_chunk = chunk
            self.assertIn("杭州", final_chunk.payload["output"], "应该包含杭州")
            print(f"✅ 第二次调用校验通过：恢复中断工作流完成，返回结果正确")

    @unittest.skip("requires network")
    async def test_llm_agent_with_workflow_interrupt_dict_with_stream(self):
        llm_agent = self._setup_test_environment_and_agent()

        interaction_output_schema = []
        async for chunk in Runner.run_agent_streaming(llm_agent, {"query": "昨天天气查询", "conversation_id": "c123"}):
            print(f"LLMAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✅ 第一次调用校验通过：返回交互请求")

        if interaction_output_schema:
            user_input = InteractiveInput()
            for item in interaction_output_schema:
                component_id = item.payload.id
                user_input.update(component_id, {"location": "杭州"})
            final_chunk = None
            async for chunk in Runner.run_agent_streaming(llm_agent, {"query": user_input, "conversation_id": "c123"}):
                print(f"LLMAgent 第二次输出结果 >>> {chunk}")
                if chunk.type == "answer":
                    final_chunk = chunk
            self.assertIn("杭州", final_chunk.payload["output"], "应该包含杭州")
            print(f"✅ 第二次调用校验通过：恢复中断工作流完成，返回结果正确")

    @unittest.skip("require network")
    async def test_llm_agent_with_workflow_interrupt_agent_invoke_multi_rounds(self):
        llm_agent = self._setup_test_environment_and_agent()

        result = await Runner.run_agent(llm_agent, {"conversation_id": "12345", "query": "今天天气查询"})
        print(f"LLMAgent 第一次输出结果：{result}")
        self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
        self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回交互请求")

        result = await Runner.run_agent(llm_agent, {"conversation_id": "12345", "query": "随机森林算法是什么"})
        print(f"LLMAgent 第二次输出结果：{result}")
        self.assertIsInstance(result, dict, "第二次调用应该返回字典")
        self.assertEqual(result['result_type'], 'answer', "应该返回answer类型")
        print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

        interactive_input = InteractiveInput()
        interactive_input.update("questioner", "上海")
        result = await Runner.run_agent(llm_agent, {"conversation_id": "12345", "query": interactive_input})
        print(f"LLMAgent 第三次输出结果：{result}")
        self.assertIsInstance(result, dict, "第三次调用应该返回字典")
        self.assertEqual(result['result_type'], 'answer', "应该返回answer类型")
        print(f"✅ 第三次调用校验通过：恢复中断工作流完成，返回结果正确")

    @unittest.skip("require network")
    async def test_llm_agent_with_workflow_interrupt_agent_stream_multi_rounds(self):
        llm_agent = self._setup_test_environment_and_agent()

        interaction_output_schema = []
        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": "昨天天气查询"}):
            print(f"LLMAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✅ 第一次调用校验通过：返回交互请求")

        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": "今天是周几"}):
            print(f"LLMAgent 第二次输出结果 >>> {chunk}")
        print(f"✅ 第二次调用校验通过：调用完成，返回结果正确")

        interactive_input = InteractiveInput()
        interactive_input.update("questioner", {"location": "上海"})
        final_chunk = None
        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": interactive_input}):
            print(f"LLMAgent 第三次输出结果 >>> {chunk}")
            if chunk.type == "answer":
                final_chunk = chunk
        self.assertIn("上海", final_chunk.payload["output"], "应该包含上海")
        print(f"✅ 第三次调用校验通过：恢复中断工作流完成，返回结果正确")

    @unittest.skip("require network")
    async def test_llm_agent_with_multiple_interrupt_nodes_workflow(self):
        """
        构建包含interactive和questioner2个中断组件的workflow
        注意：interactive和questioner在同一个超步（并行执行）

        Runner.run_agent_group_streaming进行会话操作，使用InteractiveInput分次恢复
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        # 创建 workflow - 包含两个中断组件
        flow = self._build_workflow(
            workflow_id="questioner_weather_workflow",
            workflow_name="questioner_weather_workflow",
            workflow_desc="天气查询"
        )
        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))
        llm_agent_config = create_llm_agent_config(
            agent_id="llm_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=self._create_prompt_template()
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 动态绑定workflow
        llm_agent.add_workflows([flow])

        print("\n【步骤1】发送天气查询请求，触发并行中断")
        interaction_output_schema = []
        async for chunk in Runner.run_agent_streaming(llm_agent, {"query": "昨天天气查询", "conversation_id": "12345"}):
            print(f"LLMAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")

        self.assertEqual(len(interaction_output_schema), 2, "应该同时返回2个中断（interactive和questioner）")

        # 使用interactiveInput恢复的中断
        print("\n【步骤2】使用InteractiveInput恢复一个中断")
        interactive_input = InteractiveInput()
        interactive_input.update("interactive", {"confirm_result": "确认操作"})
        interaction_output_schema = []
        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": interactive_input}):
            print(f"LLMAgent 第二次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
        self.assertEqual(len(interaction_output_schema), 1, "应该返回1个中断")
        self.assertEqual(interaction_output_schema[0].payload.id, "questioner", "应该只返回interactive中断")

        print("\n【步骤3】使用InteractiveInput恢复所有中断")
        interactive_input = InteractiveInput()
        interactive_input.update("questioner", {"location": "上海"})
        interaction_output_schema = []
        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": interactive_input}):
            print(f"LLMAgent 第三次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
        self.assertEqual(len(interaction_output_schema), 0, "应该全部完成")
        print(f"✅ 调用校验通过：恢复中断工作流完成，返回结果正确")

    @unittest.skip("require network")
    async def test_llm_agent_with_multiple_interrupt_nodes_workflow_resume_once(self):
        """
        构建包含interactive和questioner2个中断组件的workflow
        注意：interactive和questioner在同一个超步（并行执行）

        Runner.run_agent_group_streaming进行会话操作，使用InteractiveInput一次恢复所有的中断
        """
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        # 创建 workflow - 包含两个中断组件
        flow = self._build_workflow(
            workflow_id="questioner_weather_workflow",
            workflow_name="questioner_weather_workflow",
            workflow_desc="天气查询"
        )
        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))
        llm_agent_config = create_llm_agent_config(
            agent_id="llm_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=self._create_prompt_template()
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 动态绑定workflow
        llm_agent.add_workflows([flow])

        print("\n【步骤1】发送天气查询请求，触发并行中断")
        interaction_output_schema = []
        async for chunk in Runner.run_agent_streaming(llm_agent, {"query": "昨天天气查询", "conversation_id": "12345"}):
            print(f"LLMAgent 第一次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")

        self.assertEqual(len(interaction_output_schema), 2, "应该同时返回2个中断（interactive和questioner）")

        # 使用interactiveInput恢复的中断
        print("\n【步骤2】使用InteractiveInput恢复全部中断")
        interactive_input = InteractiveInput()
        interactive_input.update("questioner", {"location": "上海"})
        interactive_input.update("interactive", {"confirm_result": "确认操作"})
        interaction_output_schema = []
        async for chunk in llm_agent.stream({"conversation_id": "12345", "query": interactive_input}):
            print(f"LLMAgent 第二次输出结果 >>> {chunk}")
            if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                interaction_output_schema.append(chunk)
                print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
        self.assertEqual(len(interaction_output_schema), 0, "应该全部完成")
        print(f"✅ 调用校验通过：恢复中断工作流完成，返回结果正确")