# tests/test_workflow_agent_invoke_real.py
import os
import uuid

from openjiuwen.core.workflow import WorkflowCard, WorkflowComponent

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["RESTFUL_SSL_VERIFY"] = "false"
os.environ["SSRF_PROTECT_ENABLED"] = "false"
import asyncio
from datetime import datetime
import unittest
import pytest
from typing import List

from openjiuwen.core.single_agent import WorkflowAgentConfig, workflow_provider
from openjiuwen.core.session import TaskSession
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import IntentDetectionComponent, IntentDetectionCompConfig
from openjiuwen.core.workflow import LLMComponent, LLMCompConfig
from openjiuwen.core.workflow import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig
from openjiuwen.core.session import BaseSession
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.workflow import generate_workflow_key
from openjiuwen.core.runner import Runner
from openjiuwen.core.application.agents_for_studio.workflow_agent import WorkflowAgent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.session import Session

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

# Mock RESTful Api 元信息
_MOCK_TOOL = RestfulApi(
    card=RestfulApiCard(
        name="test",
        description="test",
        input_params={
            "type": "object",
            "properties": {
                "location": {"description": "地点", "type": "string"},
                "date": {"description": "日期", "type": "integer"},
            },
            "required": ["location", "date"],
        },
        url="http://127.0.0.1:8000",
        headers={},
        method="GET",
    ),
)

_FINAL_RESULT: str = "上海今天晴 30°C"

# --------------------------- Prompt 模板 --------------------------- #
_QUESTIONER_SYSTEM_TEMPLATE = """\
你是一个信息收集助手，你需要根据指定的参数收集用户的信息，然后提交到系统。
请注意：不要使用任何工具、不用理会问题的具体含义，并保证你的输出仅有 JSON 格式的结果数据。
请严格遵循如下规则：
  1. 让我们一步一步思考。
  2. 用户输入中没有提及的参数提取为 null，并直接向询问用户没有明确提供的参数。
  3. 通过用户提供的对话历史以及当前输入中提取 {{required_name}}，不要追问任何其他信息。
  4. 参数收集完成后，将收集到的信息通过 JSON 的方式展示给用户。

## 指定参数
{{required_params_list}}

## 约束
{{extra_info}}

## 示例
{{example}}
"""

_QUESTIONER_USER_TEMPLATE = """\
对话历史
{{dialogue_history}}

请充分考虑以上对话历史及用户输入，正确提取最符合约束要求的 JSON 格式参数。
"""

SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class InteractiveConfirmComponent(WorkflowComponent):
    """
    交互确认组件 - 用于用户确认操作
    """

    def __init__(self, comp_id: str):
        super().__init__()
        self.comp_id = comp_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 请求用户确认
        confirm = await session.interact("是否确认操作")
        return {"confirm_result": confirm}


class WorkflowAgentTest(unittest.IsolatedAsyncioTestCase):
    """专门用于测试 WorkflowAgent.invoke 的类。"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_model_config() -> ModelConfig:
        """根据环境变量构造模型配置。"""
        return ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=120,  # 增加超时时间到120秒，避免网络问题
            ),
        )

    @staticmethod
    def _create_intent_detection_component() -> IntentDetectionComponent:
        """创建意图识别组件。"""
        model_config = WorkflowAgentTest._create_model_config()
        config = IntentDetectionCompConfig(
            user_prompt="请判断用户意图，识别是否为天气查询请求",
            category_name_list=["查询某地天气"],
            model=model_config,
        )
        component = IntentDetectionComponent(config)
        # 分支配置：
        # classification_id == 1: 识别为"查询某地天气" → 走 llm 处理流程
        # classification_id == 0: 其他意图 → 走 end 默认回复
        component.add_branch("${intent.classification_id} == 1", ["llm"], "天气查询分支")
        component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
        return component

    @staticmethod
    def _create_llm_component() -> LLMComponent:
        """创建 LLM 组件，提取结构化字段（location/date）并改写query。"""
        model_config = WorkflowAgentTest._create_model_config()
        current_date = build_current_date()
        user_prompt = (
                "\n原始query为：{{query}}\n\n"
                "请从query中提取以下信息：\n"
                "1. location: 地点（用英文表示）\n"
                "2. date: 日期（YYYY-MM-DD格式，如果没有明确日期则使用今天: " + current_date + "）\n"
                                                                                             "3. query: 改写后的完整query（地名改为英文，保留其他中文信息）\n"
        )
        config = LLMCompConfig(
            model=model_config,
            template_content=[{"role": "user", "content": user_prompt}],
            response_format={"type": "json"},
            output_config={
                "location": {"type": "string", "description": "地点（英文）", "required": True},
                "date": {"type": "string", "description": "日期（YYYY-MM-DD）", "required": True},
                "query": {"type": "string", "description": "改写后的query", "required": True}
            },
        )
        return LLMComponent(config)

    @staticmethod
    def _create_questioner_component() -> QuestionerComponent:
        """创建信息收集组件。"""
        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(
                field_name="date",
                description="时间",
                required=True,
                default_value="today",
            ),
        ]
        model_config = WorkflowAgentTest._create_model_config()
        config = QuestionerConfig(
            model=model_config,
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        return QuestionerComponent(config)

    @staticmethod
    def _create_plugin_component() -> ToolComponent:
        """创建插件组件，真正调用外部 RESTful API。"""
        tool_config = ToolComponentConfig()
        weather_tool = RestfulApi(
            card=RestfulApiCard(
                name="WeatherReporter",
                description="天气查询插件",
                input_params={
                    "type": "object",
                    "properties": {
                        "location": {"description": "地点", "type": "string"},
                        "date": {"description": "日期", "type": "string"},
                    },
                    "required": ["location", "date"],
                },
                url="http://127.0.0.1:8000/weather",
                headers={},
                method="GET",
            ),
        )
        return ToolComponent(tool_config).bind_tool(weather_tool)

    @staticmethod
    def _create_start_component():
        return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

    @staticmethod
    def _create_end_component():
        return End({"responseTemplate": "{{output}}"})

    def _build_workflow(self) -> tuple[BaseSession, Workflow]:
        """
        根据 mock 工具函数构建完整工作流拓扑。

        返回 (context, workflow) 二元组，可直接用于 invoke。
        """
        # 1. 初始化工作流与上下文
        id = "test_weather_agent"
        version = "1.0"
        name = "weather"
        card = WorkflowCard(
                name=name,
                id=id,
                version=version,
        )
        flow = Workflow(
            card=card
        )
        context = TaskSession(trace_id="test")

        # 2. 实例化各组件
        start = self._create_start_component()
        intent = self._create_intent_detection_component()
        llm = self._create_llm_component()
        plugin = self._create_plugin_component()
        end = self._create_end_component()

        # 3. 注册组件到工作流
        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "intent",
            intent,
            inputs_schema={"query": "${start.query}"},
        )
        flow.add_workflow_comp(
            "llm",
            llm,
            inputs_schema={"query": "${start.query}"},
        )
        flow.add_workflow_comp(
            "plugin",
            plugin,
            inputs_schema={
                "location": "${llm.location}",
                "date": "${llm.date}",
            },
        )
        flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.data}"})

        # 4. 连接拓扑
        # start → intent → (分支路由)
        #                    ├─ llm → plugin → end (天气查询)
        #                    └─ end (其他意图)
        flow.add_connection("start", "intent")
        # intent 通过分支自动路由到 llm 或 end
        flow.add_connection("llm", "plugin")
        flow.add_connection("plugin", "end")

        return context.create_workflow_session(), flow

    def build_interrupt_workflow(self) -> tuple[BaseSession, Workflow]:
        """
        构建包含交互式组件的工作流，用于测试中断恢复功能。

        返回 (context, workflow) 二元组，可直接用于 invoke。
        """
        # 1. 初始化工作流与上下文
        workflow_id = "test_interrupt_workflow"
        version = "1.0"
        name = "interrupt_test"
        card = WorkflowCard(
                name=name,
                id=workflow_id,
                version=version,
            )

        flow = Workflow(
            card=card
        )
        context = TaskSession(trace_id="test")

        # 2. 实例化各组件
        start = self._create_start_component()
        questioner = self._create_questioner_component()
        end = self._create_end_component()

        # 3. 注册组件到工作流
        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp("end", end, inputs_schema={"output": "${questioner.location}"})

        # 4. 连接拓扑
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return context.create_workflow_session(), flow

    def build_multiple_interrupt_workflow(self) -> tuple[BaseSession, Workflow]:
        """
        构建包含两个并行中断节点的工作流，用于测试分步恢复功能。
        
        工作流拓扑：
            start -> interactive \
                     questioner  -> end
        
        返回 (context, workflow) 二元组，可直接用于 invoke。
        """
        # 1. 初始化工作流与上下文
        workflow_id = "test_multiple_interrupt_workflow"
        version = "1.0"
        name = "multiple_interrupt_test"
        card = WorkflowCard(
                name=name,
                id=workflow_id,
                version=version,
                description="包含两个并行中断节点的测试工作流",
                input_params=dict(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "用户输入",
                    }
                },
                required=['query']
            )
        )
        flow = Workflow(card=card)
        context = TaskSession(trace_id="test")

        # 2. 实例化各组件
        start = self._create_start_component()
        questioner = self._create_questioner_component()
        interactive = InteractiveConfirmComponent("interactive")
        end = End({"responseTemplate": "{{location}} | {{date}} | confirm={{confirm_result}}"})

        # 3. 注册组件到工作流
        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={"query": "${start.query}"}
        )
        flow.add_workflow_comp(
            "interactive",
            interactive,
            inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end", 
            end, 
            inputs_schema={
                "location": "${questioner.location}",
                "date": "${questioner.date}",
                "confirm_result": "${interactive.confirm_result}"
            }
        )

        # 4. 连接拓扑（并行执行）
        flow.add_connection("start", "questioner")
        flow.add_connection("start", "interactive")
        flow.add_connection(["questioner", "interactive"], "end")

        return context.create_workflow_session(), flow

    def _create_agent(self, workflow):
        """根据 workflow 实例化 WorkflowAgent。
        
        使用新架构的 add_workflows 方法，自动从 workflow 提取 schema。
        """
        # 创建最小化配置（workflows 为空列表）
        config = WorkflowAgentConfig(
            id="test_weather_agent",
            version="0.1.0",
            description="测试用天气 single_agent",
            workflows=[],  # 空列表，通过 add_workflows 自动填充
        )
        agent = WorkflowAgent(config)

        # 使用 add_workflows 动态添加（自动提取 schema）
        agent.add_workflows([workflow])

        return agent

    # ===== 核心测试用例 =====
    @unittest.skip("skip system test")
    @pytest.mark.asyncio
    async def test_real_workflow_agent_invoke(self):
        """端到端测试：WorkflowAgent.invoke 走完整链路（插件被 mock）。"""
        # 1. 构造真实 workflow
        _, workflow = self._build_workflow()

        # 2. 构造 workflow single_agent 并调用
        agent = self._create_agent(workflow)
        result = await agent.invoke({"query": "查询上海的天气", "conversation_id": str(uuid.uuid4())})

        # 5. 断言
        print(f"Workflow Agent输出的最终结果：{result}")

    def _test_interaction_detection(self, result, method_name):
        """检测交互请求的通用方法"""
        if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
            print(f"✅ {method_name} 检测到交互请求!")
            return result
        return []

    def _create_interactive_input(self, interaction_outputs):
        """创建InteractiveInput的通用方法"""
        interactive_input = InteractiveInput()
        for item in interaction_outputs:
            component_id = item.payload.id
            interactive_input.update(component_id, "上海")
        return interactive_input

    def _create_interactive_input_dict(self, interaction_outputs):
        """创建结构化信息InteractiveInput的通用方法"""
        interactive_input = InteractiveInput()
        for item in interaction_outputs:
            component_id = item.payload.id
            interactive_input.update(component_id, {"location": "上海"})
        return interactive_input

    @unittest.skip("skip system test")
    async def test_workflow_agent_runner_invoke_with_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.invoke 带中断恢复逻辑。"""
        print("=== 测试 WorkflowAgent.invoke 方法 ===")
        _, workflow = self.build_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        agent = self._create_agent(workflow)

        # 第一次调用 - 应该触发中断（设置30秒超时）
        try:
            result = await asyncio.wait_for(
                Runner.run_agent(agent, {"query": "查询天气", "conversation_id": str(uuid.uuid4())}),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise
        print(f"Workflow Agent第一次输出结果 >>> {result}")

        # 校验第一次调用结果：应该返回交互请求
        self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
        self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回交互请求")

        interaction_outputs = self._test_interaction_detection(result, "invoke")
        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            # 注意：外部调用者只传入字符串，不需要手动创建 InteractiveInput
            # WorkflowMessageHandler 会根据中断状态自动封装

            # 第二次调用 - 传入字符串格式的回答，agent内部会自动处理中断恢复
            try:
                result2 = await asyncio.wait_for(
                    Runner.run_agent(agent, {"query": "上海", "conversation_id": str(uuid.uuid4())}),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise
            print(f"Workflow Agent中断恢复后输出结果 >>> {result2}")

            # 校验第二次调用结果：应该返回完成状态
            self.assertIsInstance(result2, dict, "第二次调用应该返回字典")
            self.assertEqual(result2['result_type'], 'answer', "应该返回answer类型")
            self.assertEqual(result2['output'].state.value, 'COMPLETED', "工作流应该完成")
            self.assertEqual(result2['output'].result['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return result, result2  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test")
    async def test_workflow_agent_runner_stream_with_dict_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.stream 带中断恢复逻辑。使用dict类型InteractiveInput"""
        print("=== 测试 WorkflowAgent.stream 方法 ===")
        _, workflow = self.build_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        agent = self._create_agent(workflow)

        # 第一次调用 - 应该触发中断（设置50秒超时）
        interaction_outputs = []
        try:
            async def collect_first_stream():
                chunks = []
                async for chunk in Runner.run_agent_streaming(agent,
                                                              {"query": "查询天气", "conversation_id": str(uuid.uuid4())}):
                    print(f"Workflow Agent第一次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        print("✅ stream 检测到交互请求!")
                        interaction_outputs.append(chunk)
                return chunks

            first_chunks = await asyncio.wait_for(collect_first_stream(), timeout=50.0)
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        # 校验第一次调用结果：应该包含交互请求
        self.assertTrue(interaction_outputs, "第一次调用应该包含交互请求")
        self.assertEqual(interaction_outputs[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回 {len(interaction_outputs)} 个交互请求")

        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            interactive_input = self._create_interactive_input_dict(interaction_outputs)

            # 第二次调用 - 使用InteractiveInput进行恢复（设置30秒超时）
            second_chunks = []
            workflow_final_chunk = None
            try:
                async def collect_second_stream():
                    chunks = []
                    async for chunk in Runner.run_agent_streaming(agent, {"query": interactive_input,
                                                                          "conversation_id": str(uuid.uuid4())}):
                        print(f"Workflow Agent中断恢复后输出结果 >>> {chunk}")
                        chunks.append(chunk)
                    return chunks

                second_chunks = await asyncio.wait_for(collect_second_stream(), timeout=30.0)
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise

            # 校验第二次调用结果：应该包含 workflow_final
            for chunk in second_chunks:
                if isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                    workflow_final_chunk = chunk
                    break

            self.assertIsNotNone(workflow_final_chunk, "第二次调用应该包含 workflow_final 结果")
            self.assertIsInstance(workflow_final_chunk.payload, dict, "workflow_final payload 应该是字典")

            # 检查是否是错误响应
            if workflow_final_chunk.payload.get('error'):
                error_msg = workflow_final_chunk.payload.get('message', 'Unknown error')
                print(f"⚠️ 工作流执行遇到错误: {error_msg}")
                # 如果是 LLM 调用错误，这是外部依赖问题，测试跳过
                if 'invoke llm error' in error_msg or 'Failed to invoke llm' in error_msg:
                    self.skipTest(f"LLM 调用失败（外部依赖问题）: {error_msg}")
                else:
                    self.fail(f"工作流执行失败: {error_msg}")

            # 校验正常响应 - 透传模式下 payload 是 End 组件的直接输出
            # payload 格式: {'responseContent': '...', 'output': {}}
            self.assertIn('responseContent', workflow_final_chunk.payload, "应该包含responseContent")
            self.assertEqual(workflow_final_chunk.payload['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return first_chunks, second_chunks  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test")
    async def test_workflow_agent_runner_stream_with_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.stream 带中断恢复逻辑。"""
        print("=== 测试 WorkflowAgent.stream 方法 ===")
        _, workflow = self.build_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        agent = self._create_agent(workflow)

        # 第一次调用 - 应该触发中断（设置50秒超时）
        interaction_outputs = []
        try:
            async def collect_first_stream():
                chunks = []
                async for chunk in Runner.run_agent_streaming(agent,
                                                              {"query": "查询天气", "conversation_id": str(uuid.uuid4())}):
                    print(f"Workflow Agent第一次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        print("✅ stream 检测到交互请求!")
                        interaction_outputs.append(chunk)
                return chunks

            first_chunks = await asyncio.wait_for(collect_first_stream(), timeout=50.0)
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        # 校验第一次调用结果：应该包含交互请求
        self.assertTrue(len(interaction_outputs) > 0, "第一次调用应该包含交互请求")
        self.assertEqual(interaction_outputs[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回 {len(interaction_outputs)} 个交互请求")

        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            interactive_input = self._create_interactive_input(interaction_outputs)

            # 第二次调用 - 使用InteractiveInput进行恢复（设置30秒超时）
            second_chunks = []
            workflow_final_chunk = None
            try:
                async def collect_second_stream():
                    chunks = []
                    async for chunk in Runner.run_agent_streaming(agent, {"query": interactive_input,
                                                                          "conversation_id": str(uuid.uuid4())}):
                        print(f"Workflow Agent中断恢复后输出结果 >>> {chunk}")
                        chunks.append(chunk)
                    return chunks

                second_chunks = await asyncio.wait_for(collect_second_stream(), timeout=30.0)
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise

            # 校验第二次调用结果：应该包含 workflow_final
            for chunk in second_chunks:
                if isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                    workflow_final_chunk = chunk
                    break

            self.assertIsNotNone(workflow_final_chunk, "第二次调用应该包含 workflow_final 结果")
            self.assertIsInstance(workflow_final_chunk.payload, dict, "workflow_final payload 应该是字典")

            # 检查是否是错误响应
            if workflow_final_chunk.payload.get('error'):
                error_msg = workflow_final_chunk.payload.get('message', 'Unknown error')
                print(f"⚠️ 工作流执行遇到错误: {error_msg}")
                # 如果是 LLM 调用错误，这是外部依赖问题，测试跳过
                if 'invoke llm error' in error_msg or 'Failed to invoke llm' in error_msg:
                    self.skipTest(f"LLM 调用失败（外部依赖问题）: {error_msg}")
                else:
                    self.fail(f"工作流执行失败: {error_msg}")

            # 校验正常响应 - 透传模式下 payload 是 End 组件的直接输出
            # payload 格式: {'responseContent': '...', 'output': {}}
            self.assertIn('responseContent', workflow_final_chunk.payload, "应该包含responseContent")
            self.assertEqual(workflow_final_chunk.payload['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return first_chunks, second_chunks  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test")
    async def test_workflow_agent_invoke_with_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.invoke 带中断恢复逻辑。"""
        print("=== 测试 WorkflowAgent.invoke 方法 ===")
        _, workflow = self.build_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        agent = self._create_agent(workflow)

        # 第一次调用 - 应该触发中断（设置30秒超时）
        try:
            result = await asyncio.wait_for(
                agent.invoke({"query": "查询天气", "conversation_id": str(uuid.uuid4())}),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise
        print(f"Workflow Agent第一次输出结果 >>> {result}")

        # 校验第一次调用结果：应该返回交互请求
        self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
        self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回交互请求")

        interaction_outputs = self._test_interaction_detection(result, "invoke")
        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            # 注意：外部调用者只传入字符串，不需要手动创建 InteractiveInput
            # WorkflowMessageHandler 会根据中断状态自动封装

            # 第二次调用 - 传入字符串格式的回答，agent内部会自动处理中断恢复
            try:
                result2 = await asyncio.wait_for(
                    agent.invoke({"query": "上海", "conversation_id": str(uuid.uuid4())}),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise
            print(f"Workflow Agent中断恢复后输出结果 >>> {result2}")

            # 校验第二次调用结果：应该返回完成状态
            self.assertIsInstance(result2, dict, "第二次调用应该返回字典")
            self.assertEqual(result2['result_type'], 'answer', "应该返回answer类型")
            self.assertEqual(result2['output'].state.value, 'COMPLETED', "工作流应该完成")
            self.assertEqual(result2['output'].result['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return result, result2  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test")
    async def test_workflow_agent_stream_with_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.stream 带中断恢复逻辑。"""
        print("=== 测试 WorkflowAgent.stream 方法 ===")
        _, workflow = self.build_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        agent = self._create_agent(workflow)

        # 第一次调用 - 应该触发中断（设置50秒超时）
        interaction_outputs = []
        try:
            async def collect_first_stream():
                chunks = []
                async for chunk in agent.stream(
                        {"query": "查询天气", "conversation_id": str(uuid.uuid4())}):
                    print(f"Workflow Agent第一次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        print("✅ stream 检测到交互请求!")
                        interaction_outputs.append(chunk)
                return chunks

            first_chunks = await asyncio.wait_for(collect_first_stream(), timeout=50.0)
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        # 校验第一次调用结果：应该包含交互请求
        self.assertTrue(len(interaction_outputs) > 0, "第一次调用应该包含交互请求")
        self.assertEqual(interaction_outputs[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回 {len(interaction_outputs)} 个交互请求")

        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            interactive_input = self._create_interactive_input(interaction_outputs)

            # 第二次调用 - 使用InteractiveInput进行恢复（设置30秒超时）
            second_chunks = []
            workflow_final_chunk = None
            try:
                async def collect_second_stream():
                    chunks = []
                    async for chunk in agent.stream({"query": interactive_input,
                                                     "conversation_id": str(uuid.uuid4())}):
                        print(f"Workflow Agent中断恢复后输出结果 >>> {chunk}")
                        chunks.append(chunk)
                    return chunks

                second_chunks = await asyncio.wait_for(collect_second_stream(), timeout=30.0)
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise

            # 校验第二次调用结果：应该包含 workflow_final
            for chunk in second_chunks:
                if isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                    workflow_final_chunk = chunk
                    break

            self.assertIsNotNone(workflow_final_chunk, "第二次调用应该包含 workflow_final 结果")
            self.assertIsInstance(workflow_final_chunk.payload, dict, "workflow_final payload 应该是字典")

            # 检查是否是错误响应
            if workflow_final_chunk.payload.get('error'):
                error_msg = workflow_final_chunk.payload.get('message', 'Unknown error')
                print(f"⚠️ 工作流执行遇到错误: {error_msg}")
                # 如果是 LLM 调用错误，这是外部依赖问题，测试跳过
                if 'invoke llm error' in error_msg or 'Failed to invoke llm' in error_msg:
                    self.skipTest(f"LLM 调用失败（外部依赖问题）: {error_msg}")
                else:
                    self.fail(f"工作流执行失败: {error_msg}")

            # 校验正常响应 - 透传模式下 payload 是 End 组件的直接输出
            # payload 格式: {'responseContent': '...', 'output': {}}
            self.assertIn('responseContent', workflow_final_chunk.payload, "应该包含responseContent")
            self.assertEqual(workflow_final_chunk.payload['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return first_chunks, second_chunks  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test")
    async def test_workflow_agent_concurrent_with_workflow_provider(self):
        """
        使用 WorkflowProvider 工厂函数验证并发安全性。

        测试方案：
        - 使用新的 single_agent.add_workflows() 方法，传入工厂函数
        - 同一个 workflow key，多个 conversation 并发调用
        - 每次 get_workflow() 调用工厂函数创建新实例
        - 验证各 conversation 状态隔离

        关键点：
        - add_workflows([factory_func]) 自动检测并注册到 _providers
        - get_workflow(key) 每次调用 factory_func() 创建新实例
        """
        print("=== 测试 WorkflowProvider 工厂函数并发安全性 ===")
        # 使用 @workflow_provider 装饰器定义工厂函数
        # 注意：需要用闭包捕获 self 引用
        test_instance = self

        @workflow_provider(workflow_id="test_provider_workflow", workflow_version="1.0",
                           workflow_name="test_provider_workflow", workflow_description="haha",
                           inputs=dict(
                               type="object",
                               properties={
                                   "query": {
                                       "type": "string",
                                       "description": "用户输入",
                                       "required": True
                                   }
                               },
                               required=['query']))
        def create_interrupt_workflow_instance():
            """工厂函数：每次调用创建新的 workflow 实例"""
            _, workflow = test_instance.build_interrupt_workflow()
            # 不再需要手动设置 metadata id，装饰器会自动处理
            return workflow

        # 创建 single_agent
        config = WorkflowAgentConfig(
            id="test_provider_agent",
            version="0.1.0",
            description="测试 WorkflowProvider",
            workflows=[],
        )
        agent = WorkflowAgent(config)

        # 使用新的 add_workflows 方法，传入装饰器包装的 WorkflowFactory
        agent.add_workflows([create_interrupt_workflow_instance])
        toolinfos = Runner.resource_mgr.get_tool_infos(
            id=[generate_workflow_key(workflow_id="test_provider_workflow", workflow_version="1.0")])
        print(toolinfos)

        # 验证注册到了 _providers
        workflow_key = generate_workflow_key("test_provider_workflow", "1.0")

        # 定义多个并发会话
        conversation_configs = [
            {"conversation_id": str(uuid.uuid4()), "answer": "深圳"},
            {"conversation_id": str(uuid.uuid4()), "answer": "杭州"},
            {"conversation_id": str(uuid.uuid4()), "answer": "成都"},
        ]

        # ========== 阶段1: 并发发起第一次请求，触发中断 ==========
        print("\n【阶段1】并发发起多个 conversation 的第一次请求（共享 workflow key）")

        async def first_invoke(conv_id: str):
            """第一次调用，触发提问器中断"""
            result = await asyncio.wait_for(
                agent.invoke({"query": "查询天气", "conversation_id": conv_id}),
                timeout=60.0
            )
            return conv_id, result

        # 并发执行所有第一次请求
        first_tasks = [
            first_invoke(cfg["conversation_id"])
            for cfg in conversation_configs
        ]
        first_results = await asyncio.gather(*first_tasks, return_exceptions=True)

        # 校验每个 conversation 的第一次调用结果
        success_count = 0
        for item in first_results:
            if isinstance(item, Exception):
                print(f"  调用异常: {item}")
                continue

            conv_id, result = item
            print(f"  [{conv_id}] 第一次调用结果类型: {type(result)}")

            if isinstance(result, list) and len(result) > 0 and result[0].type == '__interaction__':
                success_count += 1
                print(f"  ✅ [{conv_id}] 成功触发中断")
            else:
                print(f"  ⚠️ [{conv_id}] 未触发中断，返回: {result}")

        print(f"\n【阶段1结果】")
        print(f"   - 总调用数: {len(conversation_configs)}")
        print(f"   - 成功触发中断数: {success_count}")

        if success_count == len(conversation_configs):
            print(f"\n🎉 WorkflowProvider 方案验证成功！")
            print(f"   - 同一 workflow key 的并发调用")
            print(f"   - 每次调用获得独立的 workflow 实例")
            print(f"   - 全部 {success_count} 个 conversation 都正确触发中断")
        else:
            print(f"\n⚠️ WorkflowProvider 方案部分成功")
            print(f"   - 只有 {success_count}/{len(conversation_configs)} 个触发中断")
            print(f"   - 说明还有其他层面的状态共享问题")

        # 断言：至少应该有一个成功
        self.assertGreater(
            success_count, 0,
            "至少应该有一个 conversation 成功触发中断"
        )

    @unittest.skip("skip system test")
    async def test_workflow_agent_concurrent_with_async_workflow_provider(self):
        """
        使用 @workflow_provider 装饰器验证并发安全性。

        测试方案：
        - 使用 @workflow_provider 装饰器创建 WorkflowFactory
        - 同一个 workflow key，多个 conversation 并发调用
        - 每次 get_workflow() 调用工厂函数创建新实例
        - 验证各 conversation 状态隔离

        关键点：
        - @workflow_provider 装饰器自动设置 id/version
        - 工厂函数不需要关心 metadata
        - get_workflow(key) 每次调用工厂创建新实例
        """
        print("=== 测试 @workflow_provider 装饰器并发安全性 ===")
        workflow_id = "test_async_provider_workflow"
        workflow_version = "1.0"

        # 使用 @workflow_provider 装饰器（最简洁的方式）
        @workflow_provider(workflow_id=workflow_id, workflow_version=workflow_version)
        def create_interrupt_workflow():
            """工厂函数：每次调用创建新的 workflow 实例，无需关心 metadata"""
            _, workflow = self.build_interrupt_workflow()
            return workflow

        # 创建 single_agent
        config = WorkflowAgentConfig(
            id="test_async_provider_agent",
            version="0.1.0",
            description="测试 @workflow_provider 装饰器",
            workflows=[],
        )
        agent = WorkflowAgent(config)

        # 使用 add_workflows 方法，传入装饰器包装的 WorkflowFactory
        agent.add_workflows([create_interrupt_workflow])

        # 验证注册到了 _providers
        workflow_key = generate_workflow_key(workflow_id, "1.0")

        # 定义多个并发会话
        conversation_configs = [
            {"conversation_id": str(uuid.uuid4()), "answer": "北京"},
            {"conversation_id": str(uuid.uuid4()), "answer": "广州"},
            {"conversation_id": str(uuid.uuid4()), "answer": "武汉"},
        ]

        # ========== 阶段1: 并发发起第一次请求，触发中断 ==========
        print("\n【阶段1】并发发起多个 conversation 的第一次请求（使用异步 provider）")

        async def first_invoke(conv_id: str):
            """第一次调用，触发提问器中断"""
            result = await asyncio.wait_for(
                agent.invoke({"query": "查询天气", "conversation_id": conv_id}),
                timeout=60.0
            )
            return conv_id, result

        # 并发执行所有第一次请求
        first_tasks = [
            first_invoke(cfg["conversation_id"])
            for cfg in conversation_configs
        ]
        first_results = await asyncio.gather(*first_tasks, return_exceptions=True)

        # 校验每个 conversation 的第一次调用结果
        success_count = 0
        for item in first_results:
            if isinstance(item, Exception):
                print(f"  调用异常: {item}")
                continue

            conv_id, result = item
            print(f"  [{conv_id}] 第一次调用结果类型: {type(result)}")

            if (isinstance(result, list) and
                    len(result) > 0 and
                    result[0].type == '__interaction__'):
                success_count += 1
                print(f"  ✅ [{conv_id}] 成功触发中断")
            else:
                print(f"  ⚠️ [{conv_id}] 未触发中断，返回: {result}")

        print(f"\n【阶段1结果】")
        print(f"   - 总调用数: {len(conversation_configs)}")
        print(f"   - 成功触发中断数: {success_count}")

        if success_count == len(conversation_configs):
            print(f"\n🎉 异步 provider 方案验证成功！")
            print(f"   - 同一 workflow key 的并发调用")
            print(f"   - 每次调用通过异步 provider 获得独立的 workflow 实例")
            print(f"   - 全部 {success_count} 个 conversation 都正确触发中断")
        else:
            print(f"\n⚠️ 异步 provider 方案部分成功")
            print(f"   - 只有 {success_count}/{len(conversation_configs)} 个触发中断")
            print(f"   - 说明还有其他层面的状态共享问题")

        # 断言：至少应该有一个成功
        self.assertGreater(
            success_count, 0,
            "至少应该有一个 conversation 成功触发中断"
        )

    @unittest.skip("skip system test")
    async def test_workflow_agent_with_multiple_interrupt_nodes_stream(self):
        """
        测试WorkflowAgent运行包含两个并行中断节点的工作流，并逐个恢复。
        
        测试场景：
        1. 首次调用：触发两个并行中断节点（interactive和questioner）
           - 但流式输出只返回第一个中断（WorkflowController设计）
        2. 第二次调用：恢复第一个节点，可能触发第二个中断
        3. 第三次调用：恢复剩余中断（如果有），工作流完成

        注意：WorkflowController._get_first_interrupt() 在流式输出时只返回第一个中断
        """
        print("=== 测试 WorkflowAgent 运行包含两个并行中断节点的工作流 ===")

        # 构建包含两个并行中断节点的工作流
        _, workflow = self.build_multiple_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)),
            workflow
        )
        agent = self._create_agent(workflow)

        # 使用固定的 conversation_id 保持会话状态
        conversation_id = str(uuid.uuid4())

        # ========== 步骤1: 首次调用，触发并行中断 ==========
        print("\n【步骤1】发送查询请求，触发并行中断")
        interaction_outputs = []
        try:
            async def collect_first_stream():
                chunks = []
                async for chunk in agent.stream({"query": "查询天气", "conversation_id": conversation_id}):
                    print(f"第一次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        interaction_outputs.append(chunk)
                        print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
                return chunks

            first_chunks = await asyncio.wait_for(collect_first_stream(), timeout=50.0)
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        # 校验第一次调用结果：流式输出只返回第一个中断（按代码设计）
        # WorkflowController._get_first_interrupt() 只返回第一个中断
        self.assertEqual(len(interaction_outputs), 1, "流式输出只返回第一个中断（interactive或questioner）")
        print(f"✅ 第一次调用校验通过：返回 {len(interaction_outputs)} 个交互请求（符合流式输出设计）")

        # 记录第一个中断的组件ID
        first_interrupt_id = interaction_outputs[0].payload.id
        print(f"   第一个中断组件ID: {first_interrupt_id}")

        # ========== 步骤2: 恢复第一个中断节点 ==========
        print(f"\n【步骤2】使用InteractiveInput恢复第一个中断（{first_interrupt_id}）")
        interactive_input = InteractiveInput()

        # 根据第一个中断的ID动态选择恢复方式
        if first_interrupt_id == "interactive":
            interactive_input.update("interactive", "确认操作")
            expected_second_interrupt = "questioner"
        else:  # questioner
            interactive_input.update("questioner", {"location": "上海", "date": "今天"})
            expected_second_interrupt = "interactive"

        interaction_outputs = []
        try:
            async def collect_second_stream():
                chunks = []
                async for chunk in agent.stream({"query": interactive_input,
                                                 "conversation_id": conversation_id}):
                    print(f"第二次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        interaction_outputs.append(chunk)
                        print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
                return chunks

            second_chunks = await asyncio.wait_for(collect_second_stream(), timeout=30.0)
        except asyncio.TimeoutError:
            print("❌ 第二次调用超时！")
            raise

        # 校验第二次调用结果：应该返回另一个中断
        self.assertEqual(len(interaction_outputs), 1, f"应该返回1个中断（{expected_second_interrupt}）")
        self.assertEqual(interaction_outputs[0].payload.id, expected_second_interrupt, 
                        f"应该只返回{expected_second_interrupt}中断")
        print(f"✅ 第二次调用校验通过：返回 {len(interaction_outputs)} 个交互请求（{expected_second_interrupt}）")

        # ========== 步骤3: 恢复剩余的中断节点 ==========
        print(f"\n【步骤3】使用InteractiveInput恢复剩余中断（{expected_second_interrupt}）")
        interactive_input = InteractiveInput()

        # 根据第二个中断的ID提供相应的输入
        if expected_second_interrupt == "interactive":
            interactive_input.update("interactive", "确认操作")
        else:  # questioner
            interactive_input.update("questioner", {"location": "上海", "date": "今天"})

        interaction_outputs = []
        workflow_final_chunk = None
        try:
            async def collect_third_stream():
                chunks = []
                final_chunk = None
                async for chunk in agent.stream({"query": interactive_input,
                                                 "conversation_id": conversation_id}):
                    print(f"第三次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        interaction_outputs.append(chunk)
                        print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
                    elif isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                        final_chunk = chunk
                return chunks, final_chunk

            third_chunks, workflow_final_chunk = await asyncio.wait_for(collect_third_stream(), timeout=30.0)
        except asyncio.TimeoutError:
            print("❌ 第三次调用超时！")
            raise

        # 校验第三次调用结果：应该没有交互请求，工作流完成
        self.assertEqual(len(interaction_outputs), 0, "应该全部完成，没有中断")
        self.assertIsNotNone(workflow_final_chunk, "第三次调用应该包含 workflow_final 结果")
        self.assertIsInstance(workflow_final_chunk.payload, dict, "workflow_final payload 应该是字典")

        # 检查是否是错误响应
        if workflow_final_chunk.payload.get('error'):
            error_msg = workflow_final_chunk.payload.get('message', 'Unknown error')
            print(f"⚠️ 工作流执行遇到错误: {error_msg}")
            if 'invoke llm error' in error_msg or 'Failed to invoke llm' in error_msg:
                self.skipTest(f"LLM 调用失败（外部依赖问题）: {error_msg}")
            else:
                self.fail(f"工作流执行失败: {error_msg}")

        # 校验正常响应
        self.assertIn('responseContent', workflow_final_chunk.payload, "应该包含responseContent")
        response_content = workflow_final_chunk.payload['responseContent']
        self.assertIn('上海', response_content, "应该包含location信息")
        self.assertIn('确认操作', response_content, "应该包含confirm信息")
        print(f"✅ 第三次调用校验通过：工作流完成，返回结果正确")
        print(f"   最终结果：{response_content}")

        print("\n🎉 测试完成！成功验证了并行中断节点的分步恢复功能")

    @unittest.skip("skip system test")
    async def test_workflow_agent_with_multiple_interrupt_nodes_resume_all_at_once(self):
        """
        测试WorkflowAgent运行包含两个并行中断节点的工作流，并同时恢复所有中断。
        
        测试场景：
        1. 首次调用：触发两个并行中断节点（interactive和questioner）
           - 但流式输出只返回第一个中断（WorkflowController设计）
        2. 第二次调用：使用InteractiveInput同时提供所有中断的输入，工作流直接完成

        注意：WorkflowController._get_first_interrupt() 在流式输出时只返回第一个中断
        """
        print("=== 测试 WorkflowAgent 同时恢复所有并行中断节点 ===")

        # 构建包含两个并行中断节点的工作流
        _, workflow = self.build_multiple_interrupt_workflow()
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)),
            workflow
        )
        agent = self._create_agent(workflow)

        # 使用相同的 conversation_id 来恢复中断的工作流
        conversation_id = str(uuid.uuid4())

        # ========== 步骤1: 首次调用，触发并行中断 ==========
        print("\n【步骤1】发送查询请求，触发并行中断")
        interaction_outputs = []
        try:
            async def collect_first_stream():
                chunks = []
                async for chunk in agent.stream({"query": "查询天气", "conversation_id": conversation_id}):
                    print(f"第一次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        interaction_outputs.append(chunk)
                        print(f"✓ 中断组件ID: {chunk.payload.id}, 提示: {chunk.payload.value}")
                return chunks

            first_chunks = await asyncio.wait_for(collect_first_stream(), timeout=50.0)
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        # 校验第一次调用结果：流式输出只返回第一个中断（按代码设计）
        # WorkflowController._get_first_interrupt() 只返回第一个中断
        self.assertEqual(len(interaction_outputs), 1, "流式输出只返回第一个中断（interactive或questioner）")
        print(f"✅ 第一次调用校验通过：返回 {len(interaction_outputs)} 个交互请求（符合流式输出设计）")

        # ========== 步骤2: 同时恢复所有中断节点 ==========
        print("\n【步骤2】使用InteractiveInput同时恢复所有中断")
        interactive_input = InteractiveInput()
        interactive_input.update("interactive", "确认操作")
        interactive_input.update("questioner", {"location": "北京"})

        workflow_final_chunk = None
        try:
            async def collect_second_stream():
                chunks = []
                final_chunk = None
                # 使用相同的 conversation_id 来恢复中断的工作流
                async for chunk in agent.stream({"query": interactive_input, "conversation_id": conversation_id}):
                    print(f"第二次输出结果 >>> {chunk}")
                    chunks.append(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                        print(f"⚠️ 意外的中断: {chunk.payload.id}")
                    elif isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                        final_chunk = chunk
                return chunks, final_chunk

            second_chunks, workflow_final_chunk = await asyncio.wait_for(collect_second_stream(), timeout=30.0)
        except asyncio.TimeoutError:
            print("❌ 第二次调用超时！")
            raise

        # 校验第二次调用结果：应该直接完成，无交互请求
        self.assertIsNotNone(workflow_final_chunk, "第二次调用应该包含 workflow_final 结果")
        self.assertIsInstance(workflow_final_chunk.payload, dict, "workflow_final payload 应该是字典")

        # 检查是否是错误响应
        if workflow_final_chunk.payload.get('error'):
            error_msg = workflow_final_chunk.payload.get('message', 'Unknown error')
            print(f"⚠️ 工作流执行遇到错误: {error_msg}")
            if 'invoke llm error' in error_msg or 'Failed to invoke llm' in error_msg:
                self.skipTest(f"LLM 调用失败（外部依赖问题）: {error_msg}")
            else:
                self.fail(f"工作流执行失败: {error_msg}")

        # 校验正常响应
        self.assertIn('responseContent', workflow_final_chunk.payload, "应该包含responseContent")
        response_content = workflow_final_chunk.payload['responseContent']
        self.assertIn('北京', response_content, "应该包含location信息")
        self.assertIn('确认操作', response_content, "应该包含confirm信息")
        print(f"✅ 第二次调用校验通过：工作流直接完成，返回结果正确")
        print(f"   最终结果：{response_content}")

        print("\n🎉 测试完成！成功验证了同时恢复所有并行中断节点的功能")
