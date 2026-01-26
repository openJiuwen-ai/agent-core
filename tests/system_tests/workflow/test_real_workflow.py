"""
端到端（End-to-End）测试：最小可运行的「旅行助手」工作流。

工作流链路：
    用户问句
        ↓  意图识别（IntentDetection）
    意图 ∈ {旅游, 天气}
        ↓  大模型（LLM）
    {地点, 日期}
        ↓  插件调用（Plugin）
    最终自然语言回答

本测试通过参数化一次性覆盖 **两条意图分支**。
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.workflow import BranchComponent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import (
    IntentDetectionComponent,
    IntentDetectionCompConfig,
)
from openjiuwen.core.workflow import LLMCompConfig, LLMComponent
from openjiuwen.core.workflow import (
    FieldInfo,
    QuestionerComponent,
    QuestionerConfig,
)
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.stream import CustomSchema
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
from openjiuwen.core.workflow import Workflow
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode

# 注意：切勿将真实密钥提交到仓库！
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# Mock 插件返回值
_FINAL_RESULT: str = "上海今天晴 30°C"

os.environ["SSRF_PROTECT_ENABLED"] = "false"
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


class RealWorkflowTest(unittest.TestCase):
    """
    端到端测试类。

    通过 mock 插件调用，验证「意图识别 → LLM → 信息收集 → 插件调用 → 结果返回」
    整条链路能否正确执行。
    """

    def setUp(self) -> None:
        """每个测试用例开始前创建全新事件循环。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self) -> None:
        """测试结束后关闭事件循环，防止 ResourceWarning。"""
        self.loop.close()

    # ------------------------------------------------------------------ #
    #                           构建工作流辅助方法                          #
    # ------------------------------------------------------------------ #
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
                timeout=30,
            ),
        )

    @staticmethod
    def _create_intent_detection_component() -> IntentDetectionComponent:
        """创建意图识别组件。"""
        model_config = RealWorkflowTest._create_model_config()
        user_prompt = """
        {{user_prompt}}

        当前可供选择的功能分类如下：
        {{category_info}}

        用户与助手的对话历史：
        {{chat_history}}

        当前输入：
        {{input}}

        请根据当前输入和对话历史分析并输出最适合的功能分类。输出格式为 JSON：
        {"class": "分类xx"}
        如果没有合适的分类，请输出 {{default_class}}。
        """
        config = IntentDetectionCompConfig(
            user_prompt="请判断用户意图",
            category_name_list=["旅游", "天气"],
            default_class="分类1",
            model=model_config,
            intent_detection_template=PromptTemplate(
                name="default",
                content=[{"role": "user", "content": user_prompt}],
            ),
            enable_input=True,
        )
        return IntentDetectionComponent(config)

    @staticmethod
    def _create_llm_component() -> LLMComponent:
        """创建 LLM 组件，仅用于抽取结构化字段（location/date）。"""
        model_config = RealWorkflowTest._create_model_config()
        config = LLMCompConfig(
            model=model_config,
            template_content=[{"role": "user", "content": "{{query}}"}],
            response_format={"type": "json"},
            output_config={
                "location": {"type": "string", "description": "地点", "required": True}
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
        model_config = RealWorkflowTest._create_model_config()
        config = QuestionerConfig(
            model=model_config,
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
            prompt_template=[
                {"role": "system", "content": _QUESTIONER_SYSTEM_TEMPLATE},
                {"role": "user", "content": _QUESTIONER_USER_TEMPLATE},
            ],
        )
        return QuestionerComponent(config)

    @staticmethod
    def _create_plugin_component() -> ToolComponent:
        """创建插件组件，真正调用外部 RESTful API。"""
        tool_config = ToolComponentConfig(needValidate=False)
        return ToolComponent(tool_config)

    @staticmethod
    async def _async_stream_workflow_for_stream_writer(flow, inputs, context, tracer_chunks):
        async for chunk in flow.stream(inputs, context):
            if isinstance(chunk, CustomSchema):
                tracer_chunks.append(chunk)

    def _build_workflow(
            self,
            mock_plugin_get_tool,
            mock_plugin_invoke,
    ) -> tuple[BaseSession, Workflow]:
        """
        根据 mock 工具函数构建完整工作流拓扑。

        返回 (context, workflow) 二元组，可直接用于 invoke。
        """
        # 1. Mock 插件行为
        mock_plugin_get_tool.return_value = _MOCK_TOOL
        mock_plugin_invoke.return_value = {"result": _FINAL_RESULT}

        # 2. 初始化工作流与上下文
        flow = Workflow(
        )
        context = create_agent_session(session_id="test")

        # 3. 实例化各组件
        start = MockStartNode("start")
        intent = self._create_intent_detection_component()
        llm = self._create_llm_component()
        questioner = self._create_questioner_component()
        plugin = self._create_plugin_component()
        end = MockEndNode("end")
        branch = BranchComponent()

        # 4. 注册组件到工作流
        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "intent",
            intent,
            inputs_schema={"input": "${query}"},
        )
        flow.add_workflow_comp(
            "llm",
            llm,
            inputs_schema={"userFields": {"query": "${start.query}"}},
        )
        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={"query": "${start.query}"},  # TODO：临时 mock
        )
        flow.add_workflow_comp(
            "plugin",
            plugin,
            inputs_schema={
                "userFields": "${questioner.userFields.key_fields}",
                "validated": True,
            },
        )
        flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.result}"})

        # 5. 分支逻辑
        branch.add_branch("${intent.classificationId} < 1", ["llm"], "1")
        branch.add_branch("${intent.classificationId} = 1", ["end"], "2")
        flow.add_workflow_comp("branch", branch)

        # 6. 连接拓扑
        flow.add_connection("start", "intent")
        flow.add_connection("intent", "branch")
        flow.add_connection("llm", "questioner")
        flow.add_connection("questioner", "plugin")
        flow.add_connection("plugin", "end")

        return context.create_workflow_session(), flow

    # ------------------------------------------------------------------ #
    #                            测试用例本身                             #
    # ------------------------------------------------------------------ #
    @unittest.skip("skip system test")
    @patch("openjiuwen.core.foundation.tool.service_api.restful_api.RestfulApi.invoke")
    @patch("openjiuwen.core.workflow.components.tool.tool_comp.ToolExecutable.get_tool")
    def test_workflow_llm_questioner_plugin(
            self,
            mock_plugin_get_tool,
            mock_plugin_invoke,
    ) -> None:
        """
        测试链路：
        Start → Intent → Branch → LLM → Questioner → Plugin → End
                            |----------------------------------↑

        输入：查询杭州的旅游景点
        断言：最终返回 _FINAL_RESULT
        """
        context, flow = self._build_workflow(
            mock_plugin_get_tool,
            mock_plugin_invoke,
        )

        inputs = {"query": "查询杭州的旅游景点"}
        result = self.loop.run_until_complete(flow.invoke(inputs, context))

        self.assertEqual(result, '上海今天晴 30°C')

    @unittest.skip("skip system test")
    def test_stream_workflow_llm_with_stream_writer(self):
        """
        测试LLM组件通过StreamWriter流出数据
        """
        context = create_agent_session(session_id="test")
        flow = Workflow()

        start = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        llm_config = LLMCompConfig(
            model=RealWorkflowTest._create_model_config(),
            template_content=[{"role": "user", "content": "{{query}}"}],
            response_format={"type": "text"},
            output_config={
                "joke": {"type": "string", "description": "笑话", "required": True}
            },
        )
        llm_component = LLMComponent(llm_config)

        flow.set_start_comp("s", start, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${llm.userFields}"})
        flow.add_workflow_comp("llm", llm_component, inputs_schema={"userFields": {"query": "${s.systemFields.query}"}})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        inputs = {"query": "写一个笑话。注意：不要超过20个字！"}
        writer_chunks = []
        self.loop.run_until_complete(
            self._async_stream_workflow_for_stream_writer(flow, inputs, context.create_workflow_session(),
                                                          writer_chunks))
        print(writer_chunks)
