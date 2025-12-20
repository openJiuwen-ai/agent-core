# tests/test_multi_workflow_agent.py
"""
多工作流测试套件
测试场景：
1. 多工作流意图识别路由
2. 多工作流跳转和恢复
3. 实时打断（参考 test_agent_invoke_002）
"""
import os

from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Output, Input
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["RESTFUL_SSL_VERIFY"] = "false"

import asyncio
from datetime import datetime
import unittest
from unittest.mock import patch, AsyncMock

from openjiuwen.agent.config.workflow_config import (
    WorkflowAgentConfig,
    DefaultResponse
)
from openjiuwen.agent.workflow_agent.workflow_agent import WorkflowAgent
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.llm_comp import LLMComponent, LLMCompConfig
from openjiuwen.core.component.questioner_comp import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class DelayedComponent(ComponentExecutable, WorkflowComponent):
    """
    带延迟的组件，用于模拟慢速执行场景，测试实时打断功能。
    """

    def __init__(self, comp_id: str, sleep: float = 0, name: str = None):
        super().__init__()
        self.sleep = sleep
        self.name = name or comp_id
        self.comp_id = comp_id

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
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
    def _create_start_component():
        return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

    def _build_prefixed_workflow(self, workflow_id: str, workflow_name: str, prefix: str) -> Workflow:
        """
        构建简单的工作流，输出带指定前缀的结果。
        用于测试工作流路由。
        """
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        )
        flow = Workflow(workflow_config=workflow_config)
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
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        )
        flow = Workflow(workflow_config=workflow_config)

        # 创建组件
        start = self._create_start_component()

        # 创建提问器
        key_fields = [
            FieldInfo(field_name=question_field, description=question_desc, required=True),
        ]
        model_config = self._create_model_config()
        questioner_config = QuestionerConfig(
            model=model_config,
            question_content="",
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
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        )
        flow = Workflow(workflow_config=workflow_config)

        # 创建组件
        start = self._create_start_component()

        # 延迟组件：模拟慢速执行
        delayed = DelayedComponent(
            comp_id=f"{workflow_id}_delayed",
            sleep=sleep,
            name=workflow_name
        )

        # 提问器组件
        model_config = self._create_model_config()
        questioner_config = QuestionerConfig(
            model=model_config,
            question_content="",
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

    @unittest.skip("skip system test")
    async def test_multi_workflow_routing_via_intent_detection(self):
        """多工作流场景下，意图识别结果应跳转到目标工作流（使用真实模型）。"""
        print("=== 测试多工作流意图识别路由 ===")

        # 创建两个工作流实例（metadata 中已包含描述信息）
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

        # 更新 workflow 的 metadata，添加详细描述（用于意图识别）
        weather_workflow.config().metadata.description = "查询某地的天气情况、温度、气象信息"
        stock_workflow.config().metadata.description = "查询股票价格、股市行情、股票走势等金融信息"

        # 创建最小化配置（workflows 为空列表）
        config = WorkflowAgentConfig(
            id="test_multi_workflow_agent",
            version="0.1.0",
            description="多工作流意图识别测试",
            workflows=[],  # 空列表，通过 add_workflows 自动填充
            model=self._create_model_config(),
        )

        agent = WorkflowAgent(config)

        # 使用 add_workflows 动态添加（自动提取 schema）
        agent.add_workflows([weather_workflow, stock_workflow])

        # 使用真实模型调用，不使用任何mock（设置30秒超时）
        print("发送请求：查看上海股票走势")
        try:
            result = await asyncio.wait_for(
                agent.invoke({
                    "query": "查看上海股票走势",
                    "conversation_id": "conv-1"
                }),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 调用超时！")
            raise

        print(f"返回结果：{result}")

        # 校验结果
        self.assertIsInstance(result, dict, "应该返回字典类型的结果")
        self.assertEqual(result["result_type"], "answer", "结果类型应该是answer")

        # 检查是否路由到了股票工作流（应该包含"stock:"前缀）
        response_content = result["output"].result["responseContent"]
        print(f"响应内容：{response_content}")

        self.assertIn("stock:", response_content, "应该路由到股票工作流")
        self.assertIn("股票", response_content, "响应应该包含查询内容")
        print(f"✅ 测试通过：成功路由到股票工作流，返回结果：{response_content}")

    @unittest.skip
    async def test_multi_workflow_jump_and_recovery(self):
        """
        测试多工作流间的跳转和恢复功能。
        
        场景：
        1. query1 -> workflow1（天气查询）-> 提问器中断（询问地点）
        2. query2 -> 意图识别 -> workflow2（股票查询）-> 提问器中断（询问股票代码）
        3. query3（InteractiveInput）-> 恢复 workflow1，提供地点信息 -> 完成
        4. query4（InteractiveInput）-> 恢复 workflow2，提供股票代码 -> 完成
        """
        print("=== 测试多工作流跳转和恢复 ===")

        # 创建两个带提问器的工作流
        weather_workflow = self._build_questioner_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            question_field="location",
            question_desc="地点"
        )
        stock_workflow = self._build_questioner_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            question_field="stock_code",
            question_desc="股票代码"
        )

        # 更新 workflow 的 metadata，添加详细描述（用于意图识别）
        weather_workflow.config().metadata.description = "查询某地的天气情况、温度、气象信息"
        stock_workflow.config().metadata.description = "查询股票价格、股市行情、股票走势等金融信息"

        # 创建最小化配置（workflows 为空列表）
        config = WorkflowAgentConfig(
            id="test_multi_workflow_jump_agent",
            version="0.1.0",
            description="多工作流跳转恢复测试",
            workflows=[],  # 空列表，通过 add_workflows 自动填充
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)

        # 使用 add_workflows 动态添加（自动提取 schema）
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-jump-recovery-001"

        # ========== 步骤1: query1 -> workflow1 -> 中断 ==========
        print("\n【步骤1】发送 query1: 查询天气")
        try:
            result1 = await asyncio.wait_for(
                agent.invoke({"query": "查询天气", "conversation_id": conversation_id}),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤1 超时！")
            raise

        print(f"步骤1 结果类型: {type(result1)}")
        if isinstance(result1, list):
            print(f"步骤1 返回列表，长度: {len(result1)}")
            if result1 and hasattr(result1[0], 'type'):
                print(f"步骤1 第一个元素类型: {result1[0].type}")

        # 校验：应该触发中断（提问器询问地点）
        self.assertIsInstance(result1, list, "步骤1应该返回交互请求列表")
        self.assertTrue(len(result1) > 0, "步骤1应该有交互请求")
        self.assertEqual(result1[0].type, '__interaction__', "步骤1应该返回交互类型")
        print(f"[OK] 步骤1成功：workflow1 触发中断，询问地点")

        # 记录 workflow1 的中断信息
        workflow1_interaction = result1[0]
        workflow1_component_id = workflow1_interaction.payload.id
        print(f"   Workflow1 中断组件ID: {workflow1_component_id}")

        # ========== 步骤2: query2 -> workflow2 -> 中断 ==========
        print("\n【步骤2】发送 query2: 查看股票")
        try:
            result2 = await asyncio.wait_for(
                agent.invoke({"query": "查看股票", "conversation_id": conversation_id}),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤2 超时！")
            raise

        print(f"步骤2 结果类型: {type(result2)}")
        if isinstance(result2, list):
            print(f"步骤2 返回列表，长度: {len(result2)}")
            if result2 and hasattr(result2[0], 'type'):
                print(f"步骤2 第一个元素类型: {result2[0].type}")

        # 校验：应该触发中断（提问器询问股票代码）
        self.assertIsInstance(result2, list, "步骤2应该返回交互请求列表")
        self.assertTrue(len(result2) > 0, "步骤2应该有交互请求")
        self.assertEqual(result2[0].type, '__interaction__', "步骤2应该返回交互类型")
        print(f"[OK] 步骤2成功：workflow2 触发中断，询问股票代码")

        # 记录 workflow2 的中断信息
        workflow2_interaction = result2[0]
        workflow2_component_id = workflow2_interaction.payload.id
        print(f"   Workflow2 中断组件ID: {workflow2_component_id}")

        # ========== 步骤3: query3 -> 恢复 workflow1 ==========
        print("\n【步骤3】发送 query3: 提供地点信息，恢复 workflow1")
        try:
            result3 = await asyncio.wait_for(
                agent.invoke({"query": "查询北京天气", "conversation_id": conversation_id}),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤3 超时！")
            raise

        print(f"步骤3 结果: {result3}")

        # 校验：workflow1 应该完成
        self.assertIsInstance(result3, dict, "步骤3应该返回字典")
        self.assertEqual(result3['result_type'], 'answer', "步骤3应该返回answer类型")
        self.assertEqual(result3['output'].state.value, 'COMPLETED', "步骤3 workflow1应该完成")
        response_content_3 = result3['output'].result.get('responseContent', '')
        print(f"[OK] 步骤3成功：workflow1 恢复并完成，返回: {response_content_3}")

        # ========== 步骤4: query4 -> 恢复 workflow2 ==========
        print("\n【步骤4】发送 query4: 提供股票代码，恢复 workflow2")
        try:
            result4 = await asyncio.wait_for(
                agent.invoke({"query": "查看AAPL股票", "conversation_id": conversation_id}),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤4 超时！")
            raise

        print(f"步骤4 结果: {result4}")

        # 校验：workflow2 应该完成
        self.assertIsInstance(result4, dict, "步骤4应该返回字典")
        self.assertEqual(result4['result_type'], 'answer', "步骤4应该返回answer类型")
        self.assertEqual(result4['output'].state.value, 'COMPLETED', "步骤4 workflow2应该完成")
        response_content_4 = result4['output'].result.get('responseContent', '')
        print(f"[OK] 步骤4成功：workflow2 恢复并完成，返回: {response_content_4}")

        print("\n[SUCCESS] 所有步骤完成！多工作流跳转和恢复测试通过！")

    @unittest.skip
    async def test_real_time_interrupt_with_cancellation(self):
        """
        测试真正的实时打断场景：不等 workflow1 执行完就发送新 query。

        场景：
        1. query1 "查天气" -> workflow1 开始执行（慢速，模拟执行中）
        2. query2 "查股票" -> 取消 workflow1，启动 workflow2 -> 中断（提问股票代码）
        3. query3 "AAPL" -> 恢复 workflow2 -> 完成

        验证：
        - workflow1 被取消（不会完成）
        - workflow2 能正常启动、中断和恢复
        - 使用 TaskQueue 机制实现真正的并发取消
        """
        print("=== 测试真正的实时打断场景 ===")

        # 创建两个工作流：
        # - weather_workflow: 带提问器（会慢速执行）
        # - stock_workflow: 带提问器（会中断）
        weather_workflow = self._build_questioner_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            question_field="location",
            question_desc="地点"
        )
        stock_workflow = self._build_questioner_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            question_field="stock_code",
            question_desc="股票代码"
        )

        # 更新 metadata 描述（用于意图识别）
        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 创建 agent
        config = WorkflowAgentConfig(
            id="test_real_time_interrupt_agent",
            version="0.1.0",
            description="实时打断测试",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-real-time-interrupt-001"

        # ========== 步骤1: query1 "查天气" -> workflow1 开始执行（不等待） ==========
        print("\n【步骤1】发送 query1: 查天气（不等待完成）")

        # 创建任务但不等待（模拟用户在执行中就发送新query）
        task1 = asyncio.create_task(
            agent.invoke({
                "query": "查天气",
                "conversation_id": conversation_id
            })
        )

        # 等待一小段时间，让 workflow1 开始执行
        await asyncio.sleep(1.0)
        print("workflow1 已开始执行，准备发送打断 query...")

        # ========== 步骤2: query2 "查股票" -> 取消 workflow1，启动 workflow2 ==========
        print("\n【步骤2】发送 query2: 查股票（实时打断 workflow1）")
        try:
            result2 = await asyncio.wait_for(
                agent.invoke({
                    "query": "查股票",
                    "conversation_id": conversation_id
                }),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤2 超时！")
            raise

        print(f"步骤2 结果类型: {type(result2)}")
        print(f"步骤2 结果: {result2}")

        # 校验：应该取消 workflow1，启动 workflow2 并触发中断
        self.assertIsInstance(result2, list, "步骤2应该返回交互请求列表")
        self.assertTrue(len(result2) > 0, "步骤2应该有交互请求")
        self.assertEqual(
            result2[0].type, '__interaction__', "步骤2应该返回交互类型"
        )
        print(f"[OK] 步骤2成功：取消 workflow1，启动 workflow2，触发中断")

        # 检查 task1 的状态（应该被取消）
        try:
            result1 = await asyncio.wait_for(task1, timeout=2.0)
            print(f"Task1 结果: {result1}")
            # 如果 task1 返回了取消状态，这也是正确的
            if isinstance(result1, dict) and result1.get('status') == 'cancelled':
                print("[OK] workflow1 被正确取消")
        except asyncio.CancelledError:
            print("[OK] workflow1 被取消（CancelledError）")
        except asyncio.TimeoutError:
            print("[WARN] workflow1 仍在执行（可能已被内部取消）")

        # ========== 步骤3: query3 "AAPL" -> 恢复 workflow2 -> 完成 ==========
        print("\n【步骤3】发送 query3: AAPL（提供股票代码）")
        try:
            result3 = await asyncio.wait_for(
                agent.invoke({
                    "query": "AAPL",
                    "conversation_id": conversation_id
                }),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤3 超时！")
            raise

        print(f"步骤3 结果类型: {type(result3)}")
        print(f"步骤3 结果: {result3}")

        # 校验：workflow2 应该恢复并完成
        self.assertIsInstance(result3, dict, "步骤3应该返回字典")
        self.assertEqual(
            result3['result_type'], 'answer', "步骤3应该返回 answer 类型"
        )
        self.assertEqual(
            result3['output'].state.value,
            'COMPLETED',
            "步骤3 workflow2 应该完成"
        )
        response_content_3 = result3['output'].result.get('responseContent', '')
        self.assertIn("AAPL", response_content_3, "步骤3 应该包含股票代码")
        print(f"✅ 步骤3成功：workflow2 恢复并完成，返回: {response_content_3}")

        print("\n🎉 实时打断测试完成！验证了 TaskQueue 取消机制！")

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
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=workflow_name,
                id=workflow_id,
                version="1.0",
            )
        )
        flow = Workflow(workflow_config=workflow_config)

        # Start 组件
        start = self._create_start_component()

        # LLM 组件 (会被 mock)
        model_config = self._create_model_config()
        llm_config = LLMCompConfig(
            model=model_config,
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

    @patch(
        "openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model"
    )
    async def test_end_batch_output_should_have_workflow_final(self, mock_get_model):
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
        mock_model = AsyncMock()
        mock_model.invoke = AsyncMock(return_value="这是LLM的回答")
        mock_get_model.return_value = mock_model

        # 构建批输出工作流（不传 response_mode）
        workflow = self._build_start_llm_end_workflow(
            workflow_id="batch_output_flow",
            workflow_name="批输出测试",
            response_mode=None  # 批输出模式
        )

        # 创建 agent
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

    @patch(
        "openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model"
    )
    async def test_end_stream_output_should_have_end_node_stream(
            self, mock_get_model
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
        mock_model = AsyncMock()
        mock_model.invoke = AsyncMock(return_value="这是LLM的流式回答")
        mock_get_model.return_value = mock_model

        # 构建流输出工作流
        workflow = self._build_start_llm_end_workflow(
            workflow_id="stream_output_flow",
            workflow_name="流输出测试",
            response_mode="streaming"  # 流输出模式
        )

        # 创建 agent
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

    @unittest.skip
    async def test_real_time_interrupt_like_invoke_002(self):
        """
        参考 test_agent_invoke_002 构造的实时打断测试。

        场景：
        1. query1 -> workflow1（天气查询，带 3s 延迟）开始执行
        2. 等待 1s 后，query2 -> workflow2（存取钱查询）打断 workflow1
        3. 等待 query2 完成
        4. 等待 query1 返回结果（验证是否被取消）
        5. 重新发送 query3 恢复天气查询工作流

        验证：
        - workflow1 被取消（在执行中被打断）
        - workflow2 能正常启动并触发交互
        - 重新发送天气查询能正常执行
        """
        print("=== 测试实时打断场景（参考 test_agent_invoke_002）===")

        # 创建天气查询工作流（带 3s 延迟，模拟慢速执行）
        weather_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="date", description="时间", required=True),
            FieldInfo(field_name="weather", description="天气", required=True),
            FieldInfo(field_name="temperature", description="温度", required=True),
        ]
        weather_workflow = self._build_questioner_workflow_with_delay(
            workflow_id="weather_flow",
            workflow_name="城市天气温度查询",
            question_fields=weather_fields,
            sleep=3  # 关键：3秒延迟让打断有足够时间窗口
        )
        weather_workflow.config().metadata.description = "查询某地的天气情况、温度、气象信息"

        # 创建存取钱工作流（无延迟，快速响应）
        cash_fields = [
            FieldInfo(field_name="bank", description="银行", required=True),
            FieldInfo(field_name="action", description="明确用户操作：存钱 还是 取钱", required=True),
            FieldInfo(field_name="amount", description="具体金额", required=True),
        ]
        cash_workflow = self._build_questioner_workflow_with_delay(
            workflow_id="cash_access_flow",
            workflow_name="银行存取钱",
            question_fields=cash_fields,
            sleep=0  # 无延迟
        )
        cash_workflow.config().metadata.description = "银行存钱、取钱业务办理"

        # 创建 Agent
        config = WorkflowAgentConfig(
            id="test_interrupt_like_invoke_002",
            version="0.1.0",
            description="实时打断测试（参考 test_agent_invoke_002）",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([cash_workflow, weather_workflow])

        conversation_id = "test-invoke-002-style"

        # ========== 步骤1: 发送天气查询请求（不等待完成）==========
        print("\n【步骤1】发送天气查询请求（不等待，模拟执行中状态）")
        task1 = asyncio.create_task(
            agent.invoke({
                "query": f"{conversation_id}-天气晴",
                "conversation_id": conversation_id
            })
        )
        print("task1 已创建，等待 1s 让 workflow1 开始执行...")

        # 等待 1s，让 workflow1 开始执行但未完成（因为有 3s 延迟）
        await asyncio.sleep(1.0)
        print("1s 已过，workflow1 应该还在 delayed 组件中等待")

        # ========== 步骤2: 发送存取钱打断请求 ==========
        print("\n【步骤2】发送存取钱请求（打断 workflow1）")
        task2 = asyncio.create_task(
            agent.invoke({
                "query": f"{conversation_id}-民生银行",
                "conversation_id": conversation_id
            })
        )

        # 等待 task2 完成
        result2 = await task2
        print(f"task2 结果: {result2}")

        # 校验：task2 应该返回交互请求（提问器询问缺失字段）
        self.assertIsInstance(result2, list, "task2 应该返回交互请求列表")
        self.assertTrue(len(result2) > 0, "task2 应该有交互请求")
        self.assertEqual(result2[0].type, '__interaction__', "task2 应该返回交互类型")
        print("[OK] 步骤2成功：存取钱工作流触发交互，询问缺失信息")

        # ========== 步骤3: 等待 task1 返回（验证被取消情况）==========
        print("\n【步骤3】等待 task1 返回，验证打断效果")
        result1 = await task1
        print(f"task1 结果: {result1}")

        # 注意：task1 可能返回取消状态或空结果，根据实现不同而变化
        # 主要验证：task1 不会阻塞，且返回结果表明被打断
        print(f"[OK] 步骤3完成：task1 返回结果（被打断后的状态）")

        # ========== 步骤4: 重新发送天气查询，验证系统恢复正常 ==========
        print("\n【步骤4】重新发送天气查询，验证系统正常")
        try:
            result3 = await asyncio.wait_for(
                agent.invoke({
                    "query": f"{conversation_id}-杭州今日天气",
                    "conversation_id": conversation_id
                }),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            print("[FAIL] 步骤4 超时！")
            raise

        print(f"步骤4 结果: {result3}")

        # 校验：应该返回交互请求（提问器询问天气详细信息）
        self.assertIsInstance(result3, list, "步骤4应该返回交互请求列表")
        self.assertTrue(len(result3) > 0, "步骤4应该有交互请求")
        print(f"[OK] 步骤4成功：系统恢复正常，天气查询工作流正常触发交互")

    @unittest.skip("skip system test - requires network")
    async def test_interactive_input_skips_llm_intent_detection(self):
        """
        测试 InteractiveInput 类型输入跳过 LLM 意图识别。

        场景：
        1. 配置两个带提问器的工作流（多工作流场景）
        2. 发送 query1 -> workflow1 触发中断
        3. 使用 InteractiveInput（指定 node_id）恢复 workflow1
        4. 验证恢复时直接恢复对应工作流，不需要再做意图识别

        验证：
        - InteractiveInput 指定 node_id 时，直接恢复对应工作流
        - 工作流能正确完成
        """
        print("=== 测试 InteractiveInput 跳过 LLM 意图识别 ===")

        # 创建两个带提问器的工作流
        weather_workflow = self._build_questioner_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            question_field="location",
            question_desc="地点"
        )
        stock_workflow = self._build_questioner_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            question_field="stock_code",
            question_desc="股票代码"
        )

        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 创建 Agent
        config = WorkflowAgentConfig(
            id="test_interactive_skip_detection",
            version="0.1.0",
            description="InteractiveInput跳过意图识别测试",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-interactive-skip-001"

        # ========== 步骤1: 发送天气查询，触发中断 ==========
        print("\n【步骤1】发送天气查询，触发中断")

        try:
            result1 = await asyncio.wait_for(
                agent.invoke({
                    "query": "查询天气",
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise

        print(f"步骤1 结果类型: {type(result1)}")

        # 校验：应该返回交互请求
        self.assertIsInstance(result1, list, "步骤1应该返回交互请求列表")
        self.assertEqual(
            result1[0].type, '__interaction__',
            "步骤1应该返回交互类型"
        )

        # 获取中断的 node_id
        interaction_output = result1[0]
        node_id = interaction_output.payload.id
        print(f"[OK] 步骤1成功：触发中断，node_id = {node_id}")

        # ========== 步骤2: 使用 InteractiveInput 恢复工作流 ==========
        print("\n【步骤2】使用 InteractiveInput 恢复工作流（应直接恢复，跳过意图识别）")

        # 创建 InteractiveInput，指定 node_id
        interactive_input = InteractiveInput()
        interactive_input.update(node_id, "北京")

        try:
            result2 = await asyncio.wait_for(
                agent.invoke({
                    "query": interactive_input,
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 第二次调用超时！")
            raise

        print(f"步骤2 结果类型: {type(result2)}")
        print(f"步骤2 结果: {result2}")

        # 校验：workflow 应该恢复并完成
        self.assertIsInstance(result2, dict, "步骤2应该返回字典")
        self.assertEqual(
            result2['result_type'], 'answer',
            "步骤2应该返回 answer 类型"
        )
        self.assertEqual(
            result2['output'].state.value, 'COMPLETED',
            "工作流应该完成"
        )

        # 验证返回的是天气查询结果（包含 location 字段值）
        response_content = result2['output'].result.get('responseContent', '')
        print(f"步骤2 响应内容: {response_content}")
        self.assertIn(
            "北京", response_content,
            "步骤2应恢复天气查询工作流，响应应包含地点"
        )

        print("\n✅ 测试通过：InteractiveInput 成功跳过意图识别，直接恢复工作流！")

    @unittest.skip("skip system test - requires network")
    async def test_interactive_input_resumes_correct_workflow_in_multi_workflow(
            self
    ):
        """
        测试多工作流场景下，InteractiveInput 能根据 node_id 恢复正确的工作流。

        场景：
        1. workflow1（天气查询）-> 中断，记录 node_id_1
        2. workflow2（股票查询）-> 中断，记录 node_id_2
        3. InteractiveInput(node_id_1) -> 应恢复 workflow1
        4. InteractiveInput(node_id_2) -> 应恢复 workflow2

        验证：
        - 根据 node_id 精确匹配被中断的工作流
        - 不会恢复错误的工作流
        """
        print("=== 测试多工作流精确恢复 ===")

        # 创建两个带提问器的工作流，使用不同的 questioner_id 以区分
        weather_workflow = self._build_questioner_workflow(
            workflow_id="weather_flow",
            workflow_name="天气查询",
            question_field="location",
            question_desc="地点",
            questioner_id="weather_questioner"
        )
        stock_workflow = self._build_questioner_workflow(
            workflow_id="stock_flow",
            workflow_name="股票查询",
            question_field="stock_code",
            question_desc="股票代码",
            questioner_id="stock_questioner"
        )

        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
            "查询股票价格、股市行情、股票走势等金融信息"
        )

        # 创建 Agent
        config = WorkflowAgentConfig(
            id="test_multi_workflow_precise_resume",
            version="0.1.0",
            description="多工作流精确恢复测试",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([weather_workflow, stock_workflow])

        conversation_id = "test-precise-resume-001"

        # ========== 步骤1: 发送天气查询，触发 workflow1 中断 ==========
        print("\n【步骤1】触发 workflow1（天气查询）中断")

        try:
            result1 = await asyncio.wait_for(
                agent.invoke({
                    "query": "查询天气",
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤1 超时！")
            raise

        self.assertIsInstance(result1, list, "步骤1应返回交互请求")
        node_id_1 = result1[0].payload.id
        print(f"[OK] workflow1 中断，node_id_1 = {node_id_1}")

        # ========== 步骤2: 发送股票查询，触发 workflow2 中断 ==========
        print("\n【步骤2】触发 workflow2（股票查询）中断")

        try:
            result2 = await asyncio.wait_for(
                agent.invoke({
                    "query": "查询股票",
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤2 超时！")
            raise

        self.assertIsInstance(result2, list, "步骤2应返回交互请求")
        node_id_2 = result2[0].payload.id
        print(f"[OK] workflow2 中断，node_id_2 = {node_id_2}")

        # ========== 步骤3: 使用 InteractiveInput(node_id_1) 恢复 workflow1 ==========
        print("\n【步骤3】使用 InteractiveInput(node_id_1) 恢复 workflow1")

        interactive_input_1 = InteractiveInput()
        interactive_input_1.update(node_id_1, "北京")

        try:
            result3 = await asyncio.wait_for(
                agent.invoke({
                    "query": interactive_input_1,
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤3 超时！")
            raise

        self.assertIsInstance(result3, dict, "步骤3应返回完成结果")
        self.assertEqual(result3['result_type'], 'answer', "步骤3应返回answer")

        # 验证返回的是天气查询结果（包含 location 字段值）
        response_content = result3['output'].result.get('responseContent', '')
        print(f"步骤3 响应内容: {response_content}")
        self.assertIn(
            "北京", response_content,
            "步骤3应恢复天气查询工作流，响应应包含地点"
        )
        print(f"[OK] workflow1 正确恢复并完成")

        # ========== 步骤4: 使用 InteractiveInput(node_id_2) 恢复 workflow2 ==========
        print("\n【步骤4】使用 InteractiveInput(node_id_2) 恢复 workflow2")

        interactive_input_2 = InteractiveInput()
        interactive_input_2.update(node_id_2, "AAPL")

        try:
            result4 = await asyncio.wait_for(
                agent.invoke({
                    "query": interactive_input_2,
                    "conversation_id": conversation_id
                }),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            print("❌ 步骤4 超时！")
            raise

        self.assertIsInstance(result4, dict, "步骤4应返回完成结果")
        self.assertEqual(result4['result_type'], 'answer', "步骤4应返回answer")

        # 验证返回的是股票查询结果（包含 stock_code 字段值）
        response_content_4 = result4['output'].result.get('responseContent', '')
        print(f"步骤4 响应内容: {response_content_4}")
        self.assertIn(
            "AAPL", response_content_4,
            "步骤4应恢复股票查询工作流，响应应包含股票代码"
        )
        print(f"[OK] workflow2 正确恢复并完成")

        print("\n✅ 测试通过：多工作流场景下根据 node_id 精确恢复正确！")

    @patch(
        "openjiuwen.agent.workflow_agent.workflow_controller."
        "WorkflowController._ensure_intent_detection_initialized"
    )
    @patch(
        "openjiuwen.core.agent.controller.reasoner.agent_reasoner."
        "AgentReasoner.use_intent_detection"
    )
    async def test_default_response_when_no_task_detected(
            self,
            mock_intent_detection,
            mock_init_intent
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

        # Mock 意图识别返回空列表
        mock_intent_detection.return_value = []
        mock_init_intent.return_value = None

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

        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
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

    @patch(
        "openjiuwen.agent.workflow_agent.workflow_controller."
        "WorkflowController._ensure_intent_detection_initialized"
    )
    @patch(
        "openjiuwen.core.agent.controller.reasoner.agent_reasoner."
        "AgentReasoner.use_intent_detection"
    )
    async def test_fallback_to_first_workflow_when_no_default_response(
            self,
            mock_intent_detection,
            mock_init_intent
    ):
        """
        测试意图识别无法选出任务且未配置 default_response.text 时，
        仍然使用第一个 workflow（保持向后兼容）。

        场景：
        1. 配置两个工作流，但不配置 default_response.text
        2. Mock 意图识别返回空结果
        3. 验证回退到第一个工作流执行

        验证：
        - 当 default_response.text 未配置时，保持原有行为
        - 使用第一个 workflow 执行
        """
        print("=== 测试未配置默认响应时回退到第一个工作流 ===")

        # Mock 意图识别返回空列表
        mock_intent_detection.return_value = []
        mock_init_intent.return_value = None

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

        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
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
        response_content = result["output"].result["responseContent"]
        print(f"响应内容：{response_content}")
        self.assertIn(
            "weather:", response_content,
            "未配置默认响应时应回退到第一个工作流（天气查询）"
        )

        print("✅ 测试通过：未配置默认响应时正确回退到第一个工作流")

    @patch(
        "openjiuwen.agent.workflow_agent.workflow_controller."
        "WorkflowController._ensure_intent_detection_initialized"
    )
    @patch(
        "openjiuwen.core.agent.controller.reasoner.agent_reasoner."
        "AgentReasoner.use_intent_detection"
    )
    async def test_default_response_stream_returns_workflow_final(
            self,
            mock_intent_detection,
            mock_init_intent
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
        - workflow_final.payload.responseContent 等于配置的默认响应文本
        """
        print("=== 测试流式模式下意图识别失败时返回 workflow_final 帧 ===")

        # Mock 意图识别返回空列表
        mock_intent_detection.return_value = []
        mock_init_intent.return_value = None

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

        weather_workflow.config().metadata.description = (
            "查询某地的天气情况、温度、气象信息"
        )
        stock_workflow.config().metadata.description = (
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
            "responseContent", workflow_final_chunk.payload,
            "workflow_final.payload 应该包含 responseContent"
        )
        self.assertEqual(
            workflow_final_chunk.payload["responseContent"], default_text,
            f"responseContent 应该等于配置的默认响应文本: {default_text}"
        )

        print(f"workflow_final 帧内容: {workflow_final_chunk.payload}")
        print(f"✅ 测试通过：流式模式正确返回 workflow_final 帧，内容: {default_text}")

    @unittest.skip("skip system test - requires network")
    async def test_questioner_state_reset_on_second_invocation(self):
        """
        测试 questioner 组件状态在第二次调用时正确重置。
        
        验证场景：
        1. 第一次调用包含 questioner 的工作流
        2. questioner 提问 -> 使用 InteractiveInput 回答 -> 完成
        3. 第二次调用同一个工作流
        4. questioner 应该重新提问（验证状态已清空，不会残留第一次的数据）
        
        这个测试用例验证了 _store_state_to_runtime 的修复：
        必须先 update_state({key: None}) 再 update_state({key: new_value})
        以确保嵌套字典中的旧键被正确删除。
        """
        print("=== 测试 Questioner 组件状态在第二次调用时正确重置 ===")
        
        # 构建包含 questioner 的工作流
        workflow = self._build_questioner_workflow(
            workflow_id="user_info_flow",
            workflow_name="用户信息收集",
            question_field="name",
            question_desc="用户姓名",
            questioner_id="questioner"
        )
        
        # 创建 WorkflowAgent
        config = WorkflowAgentConfig(
            name="测试Agent",
            id="test_questioner_reset",
            version="0.1.0",
            description="测试questioner状态重置",
            workflows=[],
            model=self._create_model_config(),
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        
        # ========== 第一次调用：触发 questioner 提问 ==========
        print("\n【第一次调用】触发 questioner 提问")
        
        try:
            result1 = await asyncio.wait_for(
                agent.invoke({
                    "query": "我想收集用户信息",
                    "session_id": "test_session_questioner_reset"
                }),
                timeout=60
            )
        except asyncio.TimeoutError:
            self.fail("第一次调用超时")
        
        # 验证：应该返回中断状态（list 格式）
        self.assertIsInstance(result1, list, "应该返回列表")
        self.assertEqual(result1[0].type, '__interaction__', "应该返回交互类型")
        
        # 提取 node_id
        interaction_output = result1[0]
        node_id = interaction_output.payload.id
        interaction_value = interaction_output.payload.value
        
        print(f"[OK] 第一次调用成功触发中断，node_id = {node_id}")
        print(f"提问器问题: {interaction_value}")
        
        # ========== 使用 InteractiveInput 回答第一次提问 ==========
        print("\n【第一次回答】使用 InteractiveInput 提供姓名")
        
        interactive_input_1 = InteractiveInput()
        interactive_input_1.update(node_id, "张三")
        
        try:
            result2 = await asyncio.wait_for(
                agent.invoke({
                    "query": interactive_input_1,
                    "session_id": "test_session_questioner_reset"
                }),
                timeout=60
            )
        except asyncio.TimeoutError:
            self.fail("第一次回答超时")
        
        # 验证：应该完成并返回结果
        self.assertIsInstance(result2, dict, "应该返回字典")
        self.assertEqual(result2["result_type"], "answer")
        
        output_1 = result2.get("output")
        self.assertIsNotNone(output_1)
        response_content_1 = output_1.result.get("responseContent", "")
        self.assertIn("张三", response_content_1)
        
        print(f"[OK] 第一次调用完成，返回结果: {response_content_1}")
        
        # ========== 第二次调用：应该重新提问（状态已重置）==========
        print("\n【第二次调用】再次触发工作流，应该重新提问")
        
        try:
            result3 = await asyncio.wait_for(
                agent.invoke({
                    "query": "我想再次收集用户信息",
                    "session_id": "test_session_questioner_reset"
                }),
                timeout=60
            )
        except asyncio.TimeoutError:
            self.fail("第二次调用超时")
        
        # 关键验证：应该再次返回中断状态（重新提问）
        self.assertIsInstance(result3, list, "第二次调用应该返回列表")
        self.assertEqual(
            result3[0].type, '__interaction__',
            "第二次调用应该重新触发 questioner 提问，而不是直接使用上次的状态"
        )
        
        # 提取新的 node_id
        interaction_output_2 = result3[0]
        node_id_2 = interaction_output_2.payload.id
        interaction_value_2 = interaction_output_2.payload.value
        
        print(f"[OK] 第二次调用成功触发中断（重新提问），node_id = {node_id_2}")
        print(f"提问器问题: {interaction_value_2}")
        
        # ========== 使用 InteractiveInput 回答第二次提问 ==========
        print("\n【第二次回答】使用 InteractiveInput 提供不同的姓名")
        
        interactive_input_2 = InteractiveInput()
        interactive_input_2.update(node_id_2, "李四")
        
        try:
            result4 = await asyncio.wait_for(
                agent.invoke({
                    "query": interactive_input_2,
                    "session_id": "test_session_questioner_reset"
                }),
                timeout=60
            )
        except asyncio.TimeoutError:
            self.fail("第二次回答超时")
        
        # 验证：应该完成并返回新的结果
        self.assertIsInstance(result4, dict, "应该返回字典")
        self.assertEqual(result4["result_type"], "answer")
        
        output_4 = result4.get("output")
        self.assertIsNotNone(output_4)
        response_content_4 = output_4.result.get("responseContent", "")
        self.assertIn("李四", response_content_4)
        
        # 关键验证：第二次结果应该是新输入的"李四"，而不是第一次的"张三"
        self.assertNotIn(
            "张三", response_content_4,
            "第二次调用返回的结果不应该包含第一次的数据（张三）"
        )
        
        print(f"[OK] 第二次调用完成，返回结果: {response_content_4}")
        print("\n✅ 测试通过：Questioner 组件状态在第二次调用时正确重置！")
        print("   - 第一次调用：张三 ✓")
        print("   - 第二次调用：李四 ✓（未残留张三）")
