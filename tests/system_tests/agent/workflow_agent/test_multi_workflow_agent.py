# tests/test_multi_workflow_agent.py
"""
多工作流测试套件
测试场景：
1. 多工作流意图识别路由
2. 多工作流跳转和恢复
3. 实时打断（参考 test_agent_invoke_002）
"""
import os
import uuid

from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import WorkflowComponent

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["RESTFUL_SSL_VERIFY"] = "false"

import asyncio
from datetime import datetime
import unittest
from unittest.mock import patch, AsyncMock

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
    DefaultResponse,
)
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.foundation.llm import (
    ModelConfig, BaseModelInfo, ModelClientConfig, ModelRequestConfig
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import LLMComponent, LLMCompConfig
from openjiuwen.core.workflow import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.core.workflow import Start
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class DelayedComponent(WorkflowComponent):
    """
    带延迟的组件，用于模拟慢速执行场景，测试实时打断功能。
    """

    def __init__(self, comp_id: str, sleep: float = 0, name: str = None):
        super().__init__()
        self.sleep = sleep
        self.name = name or comp_id
        self.comp_id = comp_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f"[{self.name}-{self.comp_id}] 开始执行: {datetime.now().strftime('%H:%M:%S')}")
        await asyncio.sleep(self.sleep)
        print(f"[{self.name}-{self.comp_id}] 执行完成: {datetime.now().strftime('%H:%M:%S')}")
        return f"delayed_{self.sleep}"


class MultiWorkflowAgentTest(unittest.IsolatedAsyncioTestCase):
    """专门用于测试多工作流场景的类。"""

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
                timeout=120,
            ),
        )

    @staticmethod
    def _create_model_client_config() -> ModelClientConfig:
        """创建模型客户端配置。"""
        return ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=120,
            max_retries=3,
            verify_ssl=False
        )

    @staticmethod
    def _create_model_request_config() -> ModelRequestConfig:
        """创建模型请求配置。"""
        return ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.7,
            top_p=0.9
        )

    @staticmethod
    def _create_start_component():
        return Start()

    def _build_prefixed_workflow(self, workflow_id: str, workflow_name: str, prefix: str) -> Workflow:
        """
        构建简单的工作流，输出带指定前缀的结果。
        用于测试工作流路由。
        """
        card = WorkflowCard(
            name=workflow_name,
            id=workflow_id,
            version="1.0",
        )
        flow = Workflow(card=card)
        start = self._create_start_component()
        end = End({"responseTemplate": f"{prefix}{{{{output}}}}"})

        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.set_end_comp(
            "end",
            end,
            inputs_schema={"output": "${start.query}"},
        )
        flow.add_connection("start", "end")
        return flow

    def _build_questioner_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            question_field: str,
            question_desc: str,
            questioner_id: str = "questioner"
    ) -> Workflow:
        """
        构建包含提问器的简单工作流。

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            question_field: 提问字段名
            question_desc: 提问字段描述
            questioner_id: 提问器组件ID，默认为 "questioner"

        Returns:
            Workflow: 包含 start -> questioner -> end 的工作流
        """
        workflow_card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
        )
        flow = Workflow(card=workflow_card)

        # 创建组件
        start = self._create_start_component()

        # 创建提问器
        key_fields = [
            FieldInfo(field_name=question_field, description=question_desc, required=True),
        ]
        model_client_config = self._create_model_client_config()
        model_request_config = self._create_model_request_config()
        questioner_config = QuestionerConfig(
            model_config=model_request_config,
            model_client_config=model_client_config,
            question_content=f"请提供{question_desc}",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
            extra_prompt_for_fields_extraction="",
            example_content="",
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件，返回提问器收集的字段值
        end = End({"responseTemplate": f"{{{{{question_field}}}}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            questioner_id, questioner, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end", end, inputs_schema={question_field: f"${{{questioner_id}.{question_field}}}"}
        )

        # 连接拓扑
        flow.add_connection("start", questioner_id)
        flow.add_connection(questioner_id, "end")

        return flow

    def _build_questioner_workflow_with_delay(
            self,
            workflow_id: str,
            workflow_name: str,
            question_fields: list[FieldInfo],
            sleep: float = 0
    ) -> Workflow:
        """
        构建包含延迟组件和提问器的工作流。
        用于测试实时打断场景：执行过程中被新请求打断。

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            question_fields: 提问器字段列表
            sleep: 延迟组件等待时间（秒）

        Returns:
            Workflow: 包含 start -> delayed -> questioner -> end 的工作流
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        flow = Workflow(card=card)

        # 创建组件
        start = self._create_start_component()

        # 延迟组件：模拟慢速执行
        delayed = DelayedComponent(
            comp_id=f"{workflow_id}_delayed",
            sleep=sleep,
            name=workflow_name
        )

        # 提问器组件
        model_client_config = self._create_model_client_config()
        model_request_config = self._create_model_request_config()
        field_descs = ", ".join([f.description for f in question_fields])
        questioner_config = QuestionerConfig(
            model_config=model_request_config,
            model_client_config=model_client_config,
            question_content=f"请提供以下信息：{field_descs}",
            extract_fields_from_response=True,
            field_names=question_fields,
            with_chat_history=False,
            extra_prompt_for_fields_extraction="",
            example_content="",
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({"responseTemplate": "{{data}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("delayed", delayed, inputs_schema={})
        flow.add_workflow_comp("questioner", questioner, inputs_schema={"query": "${start.query}"})
        flow.set_end_comp("end", end, inputs_schema={"data": "${questioner}"})

        # 连接拓扑：start -> delayed -> questioner -> end
        flow.add_connection("start", "delayed")
        flow.add_connection("delayed", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    def _build_start_llm_end_workflow(
            self,
            workflow_id: str,
            workflow_name: str,
            response_mode: str = None
    ) -> Workflow:
        """
        构建简单的 start -> llm -> end 工作流。
        
        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            response_mode: End 组件的响应模式，"streaming" 表示流输出，None 表示批输出
        
        Returns:
            Workflow: 构建的工作流
        """
        card = WorkflowCard(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        flow = Workflow(card=card)

        # Start 组件
        start = self._create_start_component()

        # LLM 组件 (会被 mock)
        model_client_config = self._create_model_client_config()
        model_request_config = self._create_model_request_config()
        llm_config = LLMCompConfig(
            model_config=model_request_config,
            model_client_config=model_client_config,
            template_content=[
                {"role": "user", "content": "请回答: {{query}}"}
            ],
            response_format={"type": "text"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(llm_config)

        # End 组件
        end = End({"responseTemplate": "结果: {{output}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "llm", llm_comp, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end",
            end,
            inputs_schema={"output": "${llm.output}"},
            response_mode=response_mode
        )

        # 连接
        flow.add_connection("start", "llm")
        flow.add_connection("llm", "end")

        return flow

    @unittest.skip("skip system test")
    @patch(
        "openjiuwen.core.foundation.llm.model.Model.invoke"
    )
    async def test_end_batch_output_should_have_workflow_final(self, mock_invoke):
        """
        测试 End 节点批输出模式：应该只收到 workflow_final 帧。
        
        场景：
        - 构建 start -> llm -> end 工作流
        - End 组件不配置 response_mode（默认批输出）
        - 流式输出应该包含 workflow_final 帧
        - 不应该包含 end node stream 帧
        """
        print("=== 测试 End 节点批输出模式 ===")

        # Mock LLM 返回
        mock_invoke.return_value = AssistantMessage(content="这是LLM的回答")

        # 构建批输出工作流（不传 response_mode）
        workflow = self._build_start_llm_end_workflow(
            workflow_id="batch_output_flow",
            workflow_name="批输出测试",
            response_mode=None  # 批输出模式
        )

        # 创建 single_agent
        config = WorkflowAgentConfig(
            id="test_batch_output_agent",
            version="0.1.0",
            description="End批输出测试",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])

        conversation_id = "test-batch-output-001"

        # 收集流式输出
        chunks = []
        async for chunk in agent.stream({
            "query": "你好",
            "conversation_id": conversation_id
        }):
            chunks.append(chunk)
            print(f"收到 chunk: type={getattr(chunk, 'type', type(chunk).__name__)}")

        # 检查是否有 workflow_final 帧
        workflow_final_chunks = [
            c for c in chunks
            if isinstance(c, OutputSchema) and c.type == "workflow_final"
        ]
        end_node_stream_chunks = [
            c for c in chunks
            if isinstance(c, OutputSchema) and c.type == "end node stream"
        ]

        print(f"workflow_final 帧数量: {len(workflow_final_chunks)}")
        print(f"end node stream 帧数量: {len(end_node_stream_chunks)}")

        # 打印 workflow_final 的完整格式
        for i, chunk in enumerate(workflow_final_chunks):
            print(f"\n=== workflow_final[{i}] 完整格式 ===")
            print(f"type: {chunk.type}")
            print(f"index: {chunk.index}")
            print(f"payload: {chunk.payload}")
            print(f"payload type: {type(chunk.payload)}")
            if hasattr(chunk, 'model_dump'):
                print(f"model_dump: {chunk.model_dump()}")
            print("=" * 40)

        # 断言：批输出模式应该有 workflow_final，不应该有 end node stream
        self.assertEqual(
            len(workflow_final_chunks), 1,
            "批输出模式应该有且只有一个 workflow_final 帧"
        )
        self.assertEqual(
            len(end_node_stream_chunks), 0,
            "批输出模式不应该有 end node stream 帧"
        )

        print("✅ 测试通过：批输出模式正确返回 workflow_final 帧")

    @unittest.skip("skip system test")
    @patch(
        "openjiuwen.core.foundation.llm.model.Model.invoke"
    )
    async def test_end_stream_output_should_have_end_node_stream(
            self, mock_invoke
    ):
        """
        测试 End 节点流输出模式：应该收到 end node stream 帧，不应该收到 workflow_final 帧。

        场景：
        - 构建 start -> llm -> end 工作流
        - End 组件配置 response_mode="streaming"（流输出）
        - 流式输出应该包含 end node stream 帧
        - 不应该包含 workflow_final 帧
        """
        print("=== 测试 End 节点流输出模式 ===")

        # Mock LLM 返回
        mock_invoke.return_value = AssistantMessage(content="这是LLM的流式回答")

        # 构建流输出工作流
        workflow = self._build_start_llm_end_workflow(
            workflow_id="stream_output_flow",
            workflow_name="流输出测试",
            response_mode="streaming"  # 流输出模式
        )

        # 创建 single_agent
        config = WorkflowAgentConfig(
            id="test_stream_output_agent",
            version="0.1.0",
            description="End流输出测试",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])

        conversation_id = "test-stream-output-001"

        # 收集流式输出
        chunks = []
        async for chunk in agent.stream({
            "query": "你好",
            "conversation_id": conversation_id
        }):
            chunks.append(chunk)
            print(f"收到 chunk: type={getattr(chunk, 'type', type(chunk).__name__)}")

        # 检查帧类型
        workflow_final_chunks = [
            c for c in chunks
            if isinstance(c, OutputSchema) and c.type == "workflow_final"
        ]
        end_node_stream_chunks = [
            c for c in chunks
            if isinstance(c, OutputSchema) and c.type == "end node stream"
        ]

        print(f"workflow_final 帧数量: {len(workflow_final_chunks)}")
        print(f"end node stream 帧数量: {len(end_node_stream_chunks)}")

        # 断言：流输出模式应该有 end node stream，不应该有 workflow_final
        self.assertGreater(
            len(end_node_stream_chunks), 0,
            "流输出模式应该有 end node stream 帧"
        )
        self.assertEqual(
            len(workflow_final_chunks), 0,
            "流输出模式不应该有 workflow_final 帧"
        )

        print("✅ 测试通过：流输出模式正确返回 end node stream 帧")

    @unittest.skip("skip system test")
    @patch(
        "openjiuwen.core.application.workflow_agent.workflow_controller."
        "WorkflowController._detect_workflow_via_llm"
    )
    async def test_default_response_when_no_task_detected(
            self,
            mock_detect_workflow
    ):
        """
        测试意图识别无法选出任务时，使用配置的 default_response.text 作为响应。

        场景：
        1. 配置两个工作流（多工作流场景会触发 LLM 意图识别）
        2. 配置 default_response.text = "抱歉，我无法理解您的问题"
        3. Mock 意图识别返回空结果
        4. 验证返回的是配置的默认响应文本

        验证：
        - 当意图识别返回空时，不再使用第一个 workflow
        - 返回配置的 default_response.text
        """
        print("=== 测试意图识别失败时返回默认响应 ===")

        # Mock _detect_workflow_via_llm 返回 None，触发默认响应
        mock_detect_workflow.return_value = None

        # 创建两个工作流
        weather_workflow = self._build_prefixed_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            prefix="weather:"
        )
        stock_workflow = self._build_prefixed_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            prefix="stock:"
        )

        weather_workflow.card.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.card.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 配置默认响应
        default_text = "抱歉，我无法理解您的问题，请换一种方式表达"
        config = WorkflowAgentConfig(
            id="test_default_response_agent",
            version="0.1.0",
            description="默认响应测试",
            workflows=[],
            model=self._create_model_config(),
            default_response=DefaultResponse(type="text", text=default_text),
        )

        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-default-response-001"

        # 发送一个模糊的查询
        print("\n发送查询：一些无法识别的内容")
        try:
            result = await asyncio.wait_for(
                agent.invoke({
                    "query": "blahblah随机内容xyz",
                    "conversation_id": conversation_id
                }),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            print("❌ 调用超时！")
            raise

        print(f"返回结果：{result}")

        # 校验结果
        self.assertIsInstance(result, dict, "应该返回字典类型的结果")
        self.assertEqual(
            result["status"], "default_response",
            "状态应该是 default_response"
        )
        self.assertEqual(
            result["result_type"], "answer",
            "结果类型应该是 answer"
        )
        self.assertEqual(
            result["output"]["answer"], default_text,
            f"应该返回配置的默认响应文本: {default_text}"
        )

        print(f"✅ 测试通过：意图识别失败时正确返回默认响应: {default_text}")

    @unittest.skip("skip system test")
    async def test_fallback_to_first_workflow_when_no_default_response(self):
        """
        测试意图识别模块未初始化时，使用第一个 workflow（保持向后兼容）。

        场景：
        1. 配置两个工作流，但不配置 default_response.text
        2. 由于 _intent_detector 未初始化，会直接使用第一个工作流
        3. 验证回退到第一个工作流执行

        验证：
        - 当意图识别模块未初始化时，保持原有行为
        - 使用第一个 workflow 执行
        """
        print("=== 测试未配置默认响应时回退到第一个工作流 ===")

        # 创建两个工作流
        weather_workflow = self._build_prefixed_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            prefix="weather:"
        )
        stock_workflow = self._build_prefixed_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            prefix="stock:"
        )

        weather_workflow.card.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.card.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 不配置默认响应（使用默认空配置）
        config = WorkflowAgentConfig(
            id="test_no_default_response_agent",
            version="0.1.0",
            description="无默认响应测试",
            workflows=[],
            model=self._create_model_config(),
            # default_response 使用默认值（text 为 None）
        )

        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-no-default-response-001"

        # 发送一个模糊的查询
        print("\n发送查询：一些无法识别的内容")
        try:
            result = await asyncio.wait_for(
                agent.invoke({
                    "query": "blahblah随机内容xyz",
                    "conversation_id": conversation_id
                }),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            print("❌ 调用超时！")
            raise

        print(f"返回结果：{result}")

        # 校验结果：应该使用第一个工作流（天气查询）
        self.assertIsInstance(result, dict, "应该返回字典类型的结果")
        self.assertEqual(
            result["result_type"], "answer",
            "结果类型应该是 answer"
        )

        # 检查是否使用了第一个工作流（天气查询，前缀是 weather:）
        response_content = result["output"].result["response"]
        print(f"响应内容：{response_content}")
        self.assertIn(
            "weather:", response_content,
            "未配置默认响应时应回退到第一个工作流（天气查询）"
        )

        print("✅ 测试通过：未配置默认响应时正确回退到第一个工作流")

    @unittest.skip("skip system test")
    @patch(
        "openjiuwen.core.application.workflow_agent.workflow_controller."
        "WorkflowController._detect_workflow_via_llm"
    )
    async def test_default_response_stream_returns_workflow_final(
            self,
            mock_detect_workflow
    ):
        """
        测试意图识别无法选出任务时，流式模式返回 workflow_final 帧。

        场景：
        1. 配置两个工作流（多工作流场景会触发 LLM 意图识别）
        2. 配置 default_response.text
        3. Mock 意图识别返回空结果
        4. 使用 stream 方法调用
        5. 验证返回的流中包含 workflow_final 帧，且内容正确

        验证：
        - 流式输出包含 workflow_final 帧
        - workflow_final.payload.response 等于配置的默认响应文本
        """
        print("=== 测试流式模式下意图识别失败时返回 workflow_final 帧 ===")

        # Mock _detect_workflow_via_llm 返回 None，触发默认响应
        mock_detect_workflow.return_value = None

        # 创建两个工作流
        weather_workflow = self._build_prefixed_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            prefix="weather:"
        )
        stock_workflow = self._build_prefixed_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            prefix="stock:"
        )

        weather_workflow.card.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.card.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 配置默认响应
        default_text = "抱歉，我无法理解您的问题，请换一种方式表达"
        config = WorkflowAgentConfig(
            id="test_default_response_stream_agent",
            version="0.1.0",
            description="默认响应流式测试",
            workflows=[],
            model=self._create_model_config(),
            default_response=DefaultResponse(type="text", text=default_text),
        )

        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-default-response-stream-001"

        # 使用流式方法调用
        print("\n使用 stream 方法发送查询：一些无法识别的内容")
        chunks = []
        workflow_final_chunk = None

        try:
            async for chunk in agent.stream({
                "query": "blahblah随机内容xyz",
                "conversation_id": conversation_id
            }):
                chunks.append(chunk)
                print(f"收到 chunk: type={getattr(chunk, 'type', type(chunk).__name__)}")
                if isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                    workflow_final_chunk = chunk
        except asyncio.TimeoutError:
            print("❌ 流式调用超时！")
            raise

        print(f"\n收到 {len(chunks)} 个 chunk")

        # 校验：应该有 workflow_final 帧
        self.assertIsNotNone(
            workflow_final_chunk,
            "流式输出应该包含 workflow_final 帧"
        )

        # 校验 workflow_final 的内容格式
        self.assertIsInstance(
            workflow_final_chunk.payload, dict,
            "workflow_final.payload 应该是字典"
        )
        self.assertIn(
            "response", workflow_final_chunk.payload,
            "workflow_final.payload 应该包含 response"
        )
        self.assertEqual(
            workflow_final_chunk.payload["response"], default_text,
            f"response 应该等于配置的默认响应文本: {default_text}"
        )

        print(f"workflow_final 帧内容: {workflow_final_chunk.payload}")
        print(f"✅ 测试通过：流式模式正确返回 workflow_final 帧，内容: {default_text}")
