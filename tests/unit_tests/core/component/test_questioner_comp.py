import asyncio
import os
import time
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig, ContextEngine
from openjiuwen.core.session import InteractionOutput
from openjiuwen.core.session.agent import Session
from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.foundation.llm import ModelConfig, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import FieldInfo, QuestionerConfig, QuestionerComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.graph.executable import Input
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session import WorkflowSession
from openjiuwen.core.session.stream import TraceSchema, OutputSchema
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.workflow import Workflow, WorkflowExecutionState, WorkflowOutput
from openjiuwen.core.single_agent import create_agent_session

os.environ.setdefault("LLM_SSL_VERIFY", "false")
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


class MockLLMModel:
    pass


def _create_model_request_config() -> ModelRequestConfig:
    """创建模型配置"""
    return ModelRequestConfig(
        model="gpt-3.5-turbo",
        temperature=0.7,
        top_p=0.9
    )


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


class TestQuestionComp:
    @pytest.fixture(autouse=True)
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    @staticmethod
    def invoke_workflow(inputs: Input, context: Session, flow: Workflow):
        loop = asyncio.get_event_loop()
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, session=context.create_workflow_session()))
        loop.run_until_complete(feature)
        return feature.result()

    @staticmethod
    def invoke_workflow_with_workflow_context(inputs: Input, session: WorkflowSession, flow: Workflow):
        loop = asyncio.get_event_loop()
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, session=session))
        loop.run_until_complete(feature)
        return feature.result()

    @staticmethod
    def _create_context(session_id):
        return Session(session_id=session_id)

    @patch("openjiuwen.core.workflow.components.llm.questioner_comp."
           "QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerExecutable._init_prompt")
    @patch("openjiuwen.core.foundation.llm.model.Model")
    def test_invoke_questioner_component_in_workflow_initial_ask(self, mock_get_model, mock_init_prompt,
                                                                 mock_llm_inputs,
                                                                 mock_extraction):
        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_init_prompt.return_value = PromptTemplate(name="test", content=mock_prompt_template)
        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(location="hangzhou")

        context = Session(session_id="test")
        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start()
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
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

        result = self.invoke_workflow({"query": "查询杭州的天气"}, context, flow)
        assert result == WorkflowOutput(
            result={'response': "hangzhou | today"},
            state=WorkflowExecutionState.COMPLETED)

    @patch("openjiuwen.core.workflow.components.llm.questioner_comp."
           "QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("openjiuwen.core.foundation.llm.model.Model")
    def test_invoke_questioner_component_in_workflow_repeat_ask(self, mock_get_model, mock_llm_inputs,
                                                                mock_extraction):
        """
        测试提问器中断恢复流程
        """
        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(location="hangzhou")

        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start()
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
            question_content="查询什么城市的天气",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner_component = QuestionerComponent(questioner_comp_config=questioner_config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"location": "${questioner.location}", "time": "${questioner.time}"})
        flow.add_workflow_comp("questioner", questioner_component, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "questioner")
        flow.add_connection("questioner", "e")

        session_id = "test_questioner"
        workflow_context = Session(session_id=session_id).create_workflow_session()
        first_question = self.invoke_workflow_with_workflow_context({"query": "你好"}, workflow_context, flow)
        first_question = first_question.result[0] if first_question else dict()
        payload = first_question.payload
        if isinstance(payload, InteractionOutput) and payload.id is not None:
            component_id = payload.id
        else:
            assert False
        user_input = InteractiveInput()
        user_input.update(component_id, "地点是杭州")  # 第一个入参是组件id

        workflow_context = Session(session_id=session_id).create_workflow_session()
        final_result = self.invoke_workflow_with_workflow_context(user_input, workflow_context,
                                                                  flow)  # workflow实例、session id保持一致
        assert final_result.result.get("response") == "hangzhou | today"

    @patch("openjiuwen.core.workflow.components.llm.questioner_comp."
           "QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerExecutable._init_prompt")
    @patch("openjiuwen.core.foundation.llm.model.Model")
    def test_stream_questioner_component_in_workflow_initial_ask_with_tracer(self, mock_get_model, mock_init_prompt,
                                                                             mock_llm_inputs, mock_extraction):
        '''
        tracer使用问题记录：
        1. 必须调用workflow、agent的 stream方法，才能获取到tracer的数据帧
        2. workflow组件的输入输出，tracer都已经记录了，组件只需要关注额外的数据
        3. agent用agent_tracer，workflow用workflow_tracer
        4. event有定义，参考handler.py的@trigger_event
        5. on_invoke_data是固定的结构，结构为 dict(on_invoke_data={"on_invoke_data": "extra trace data"})
        6. span只有agent需要
        7. workflow不需要context显式地调用set_tracer
        '''

        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_init_prompt.return_value = PromptTemplate(name="test", content=mock_prompt_template)
        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(location="hangzhou")

        context = Session(session_id="test")
        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start()
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
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

        async def _async_stream_workflow_for_tracer(_flow, _inputs, _session, _tracer_chunks):
            async for chunk in flow.stream(_inputs, _session):
                if isinstance(chunk, TraceSchema):
                    _tracer_chunks.append(chunk)

        tracer_chunks = []
        self.loop.run_until_complete(_async_stream_workflow_for_tracer(flow, {"query": "查询杭州的天气"},
                                                                       context.create_workflow_session(),
                                                                       tracer_chunks))
        print(tracer_chunks)


class TestQuestionerStream:

    @pytest.mark.asyncio
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp."
           "QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("openjiuwen.core.foundation.llm.model.Model")
    async def test_invoke_questioner_component_in_workflow_repeat_ask_with_stream_writer_and_context_engine(
            self,
            mock_get_model,
            mock_llm_inputs,
            mock_extraction):
        """
        测试提问器中断恢复流程
        """
        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(time="tomorrow")

        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start()
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=True
        )
        questioner_component = QuestionerComponent(questioner_comp_config=questioner_config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"location": "${questioner.location}", "time": "${questioner.time}"})
        flow.add_workflow_comp("questioner", questioner_component, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "questioner")
        flow.add_connection("questioner", "e")

        session_id = "test_questioner"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="questioner_workflow")
        workflow_session = Session(session_id=session_id).create_workflow_session()
        interaction_output_schema = []
        async for chunk in flow.stream({"query": "你好"}, workflow_session, workflow_context):
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                interaction_output_schema.append(chunk)

        mock_extraction.return_value = dict(location="hangzhou")

        if interaction_output_schema:
            user_input = InteractiveInput()
            for item in interaction_output_schema:
                component_id = item.payload.id
                user_input.update(component_id, "杭州")
            workflow_session = Session(session_id=session_id).create_workflow_session()
            async for chunk in flow.stream(user_input, workflow_session, workflow_context):
                print(f"stream output >>> {chunk}")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp."
           "QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerExecutable._init_prompt")
    @patch("openjiuwen.core.foundation.llm.model.Model")
    async def test_questioner_state_reset_on_second_workflow_invocation(
            self, mock_get_model, mock_init_prompt, mock_llm_inputs, mock_extraction
    ):
        """
        测试直接调用 Workflow 时，questioner 组件状态在第二次调用时正确重置。
        
        验证场景：
        1. 第一次调用 workflow: 提问 -> 回答"张三" -> 完成
        2. 第二次调用 workflow: 应该重新提问（而不是使用第一次的状态）-> 回答"李四" -> 完成
        3. 验证第二次结果是"李四"，没有残留"张三"
        
        这个测试验证了底层 workflow session 和组件状态管理的正确性，
        不依赖于 WorkflowAgent 层。
        """
        # Mock setup
        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_init_prompt.return_value = PromptTemplate(name="test", content=mock_prompt_template)
        mock_llm_inputs.return_value = mock_prompt_template
        
        # 第一次调用时返回空字典（触发交互），第二次调用返回 "张三"
        mock_extraction.side_effect = [{}, {"name": "张三"}]
        
        # 构建 workflow: start -> questioner -> end
        flow = Workflow()
        
        key_fields = [
            FieldInfo(field_name="name", description="用户姓名", required=True),
        ]
        
        start_component = Start()
        
        end_component = End({"responseTemplate": "{{name}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        
        questioner_comp = QuestionerComponent(questioner_config)
        
        # 注册组件
        flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("questioner", questioner_comp, inputs_schema={"query": "${start.query}"})
        flow.set_end_comp("end", end_component, inputs_schema={"name": "${questioner.name}"})
        
        # 连接拓扑
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")
        
        # ========== 第一次调用 workflow ==========
        session_id = "test_questioner_state_reset"
        workflow_session_1 = Session(session_id=session_id).create_workflow_session()
        
        # 第一次调用：触发中断
        workflow_result_1 = await flow.invoke({"query": "请收集用户信息"}, workflow_session_1)
        
        # 验证：应该触发交互
        assert workflow_result_1.state == WorkflowExecutionState.INPUT_REQUIRED
        component_id_1 = workflow_result_1.result[0].payload.id
        assert component_id_1 == "questioner"
        
        # 第一次回答："张三"（需要创建新的 session）
        workflow_session_1_resume = Session(session_id=session_id).create_workflow_session()
        user_feedback_1 = InteractiveInput()
        user_feedback_1.update(component_id_1, "张三")
        
        workflow_result_2 = await flow.invoke(user_feedback_1, workflow_session_1_resume)
        
        # 验证：第一次应该完成并返回 "张三"
        assert workflow_result_2.state == WorkflowExecutionState.COMPLETED
        response_content_1 = workflow_result_2.result.get("response", "")
        assert "张三" in response_content_1
        print(f"[OK] 第一次调用完成，返回结果: {response_content_1}")
        
        # ========== 第二次调用 workflow（新的 session）==========
        # 重置 mock side_effect：第三次返回空（触发交互），第四次返回 "李四"
        mock_extraction.side_effect = [{}, {"name": "李四"}]
        
        # 使用新的 workflow session
        workflow_session_2 = Session(session_id=session_id).create_workflow_session()
        
        # 第二次调用：应该重新触发中断（而不是使用第一次的状态）
        workflow_result_3 = await flow.invoke({"query": "再次收集用户信息"}, workflow_session_2)
        
        # 关键验证：第二次调用应该再次触发交互（重新提问）
        assert workflow_result_3.state == WorkflowExecutionState.INPUT_REQUIRED, \
            "第二次调用应该重新触发 questioner 提问，而不是直接使用上次的状态"
        component_id_2 = workflow_result_3.result[0].payload.id
        assert component_id_2 == "questioner"
        print(f"[OK] 第二次调用成功触发中断（重新提问）")
        
        # 第二次回答："李四"（需要创建新的 session）
        workflow_session_2_resume = Session(session_id=session_id).create_workflow_session()
        user_feedback_2 = InteractiveInput()
        user_feedback_2.update(component_id_2, "李四")
        
        workflow_result_4 = await flow.invoke(user_feedback_2, workflow_session_2_resume)
        
        # 验证：第二次应该完成并返回 "李四"
        assert workflow_result_4.state == WorkflowExecutionState.COMPLETED
        response_content_2 = workflow_result_4.result.get("response", "")
        assert "李四" in response_content_2
        
        # 关键验证：第二次结果不应该包含第一次的数据
        assert "张三" not in response_content_2, \
            "第二次调用返回的结果不应该包含第一次的数据（张三）"
        
        print(f"[OK] 第二次调用完成，返回结果: {response_content_2}")
        print("✅ 测试通过：Questioner 组件状态在第二次 workflow 调用时正确重置！")
        print("   - 第一次调用：张三 ✓")
        print("   - 第二次调用：李四 ✓（未残留张三）")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerExecutable._create_llm_instance")
    async def test_questioner_type_conversion_with_multiple_types(self, mock_create_llm):
        """
        Test questioner correctly validates and converts multiple basic data types.

        Scenario:
        - LLM returns string representations for various types
        - string "25" for integer field -> should convert to 25
        - string "98.5" for number field -> should convert to 98.5
        - string "true" for boolean field -> should convert to True
        - "Alice" for string field -> should keep as "Alice"

        This test covers the type validation and conversion logic in _validate_and_convert_fields.
        """
        from unittest.mock import AsyncMock, MagicMock

        # Create mock LLM that returns JSON string with various types
        mock_llm = MagicMock()
        mock_response = MagicMock()
        # LLM returns string representations that need type conversion
        mock_response.content = '''```json
{
    "name": "Alice",
    "age": "25",
    "score": "98.5",
    "is_vip": "true"
}
```'''
        mock_llm.invoke = AsyncMock(return_value=mock_response)
        mock_create_llm.return_value = mock_llm

        # Build workflow: start -> questioner -> end
        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="name", description="User name", type="string", required=True),
            FieldInfo(field_name="age", description="User age", type="integer", required=True),
            FieldInfo(field_name="score", description="User score", type="number", required=True),
            FieldInfo(field_name="is_vip", description="VIP status", type="boolean", required=True),
        ]
        
        start_component = Start()
        
        # Use responseTemplate to output all extracted fields
        end_component = End({"responseTemplate": "name={{name}},age={{age}},score={{score}},is_vip={{is_vip}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )

        questioner_comp = QuestionerComponent(questioner_config)

        # Register components
        flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("questioner", questioner_comp, inputs_schema={"query": "${start.query}"})
        flow.set_end_comp("end", end_component, inputs_schema={
            "name": "${questioner.name}",
            "age": "${questioner.age}",
            "score": "${questioner.score}",
            "is_vip": "${questioner.is_vip}"
        })

        # Connect topology
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        # Execute workflow
        session_id = "test_type_conversion"
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()

        result = await flow.invoke({"query": "Extract user info with multiple types"}, workflow_session)

        # Verify workflow completed successfully
        assert result.state == WorkflowExecutionState.COMPLETED

        response_content = result.result.get("response", "")
        print(f"[INFO] Response content: {response_content}")

        # Verify all fields are present and correctly converted
        # string field: "Alice" -> "Alice"
        assert "name=Alice" in response_content

        # integer field: "25" -> 25 (converted from string)
        assert "age=25" in response_content

        # number field: "98.5" -> 98.5 (converted from string)
        assert "score=98.5" in response_content

        # boolean field: "true" -> True (converted from string)
        assert "is_vip=True" in response_content

        print("✅ Test passed: Questioner correctly converts multiple basic data types!")
        print("   - string: 'Alice' -> 'Alice' ✓")
        print("   - integer: '25' -> 25 ✓")
        print("   - number: '98.5' -> 98.5 ✓")
        print("   - boolean: 'true' -> True ✓")

    @pytest.mark.asyncio
    @patch("openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerExecutable._create_llm_instance")
    async def test_questioner_type_validation_failure_triggers_continue_ask(self, mock_create_llm):
        """
        Test that type validation failure triggers continue-ask for required fields.

        Scenario:
        - LLM returns float 3.14 for an integer field "age"
        - Float 3.14 cannot be converted to integer (not a whole number)
        - The "age" field is treated as not extracted
        - Since "age" is required, questioner should trigger continue-ask

        This test verifies that invalid type values are properly rejected
        and the workflow enters INPUT_REQUIRED state to ask user again.
        """
        from unittest.mock import AsyncMock, MagicMock

        # Create mock LLM that returns invalid type for integer field
        mock_llm = MagicMock()
        mock_response = MagicMock()
        # LLM returns float for integer field - this should fail validation
        mock_response.content = '''```json
{
    "name": "Bob",
    "age": 3.14
}
```'''
        mock_llm.invoke = AsyncMock(return_value=mock_response)
        mock_create_llm.return_value = mock_llm

        # Build workflow: start -> questioner -> end
        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="name", description="User name", type="string", required=True),
            FieldInfo(field_name="age", description="User age", type="integer", required=True),
        ]
        
        start_component = Start()
        
        end_component = End({"responseTemplate": "name={{name}},age={{age}}"})

        questioner_config = QuestionerConfig(
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config(),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )

        questioner_comp = QuestionerComponent(questioner_config)

        # Register components
        flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("questioner", questioner_comp, inputs_schema={"query": "${start.query}"})
        flow.set_end_comp("end", end_component, inputs_schema={
            "name": "${questioner.name}",
            "age": "${questioner.age}"
        })

        # Connect topology
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        # Execute workflow
        session_id = "test_type_validation_failure"
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()

        result = await flow.invoke({"query": "My name is Bob, I am 3.14 years old"}, workflow_session)

        # Verify workflow requires user input because age validation failed
        assert result.state == WorkflowExecutionState.INPUT_REQUIRED, \
            "Workflow should require input because integer field 'age' received float 3.14"

        # Verify the interaction is triggered by questioner component
        component_id = result.result[0].payload.id
        assert component_id == "questioner"

        print("✅ Test passed: Type validation failure correctly triggers continue-ask!")
        print("   - Expected type: integer")
        print("   - LLM returned: 3.14 (float)")
        print("   - Result: Field rejected, workflow asks user again ✓")
