import os
import sys
import types
import unittest
from typing import Any, Union, List, Dict, AsyncIterator

import pytest
from unittest.mock import Mock


from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.workflow import ComponentAbility, End, WorkflowCard
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ContextEngineConfig, ContextEngine
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow.components.llm.llm_comp import LLMExecutable

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base
sys.modules["openjiuwen.core.common.exception.base"] = fake_exception_module

from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode

from unittest.mock import patch, AsyncMock

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.workflow import LLMCompConfig, LLMComponent
from openjiuwen.core.session import WorkflowSession, NodeSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig

USER_FIELDS = "userFields"
os.environ["LLM_SSL_VERIFY"] = "false"
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


@pytest.fixture
def fake_node_ctx():
    return Session(NodeSession(WorkflowSession(), "test"))


@pytest.fixture
def fake_input():
    return lambda **kw: {USER_FIELDS: kw}


@pytest.fixture
def fake_model_config() -> ModelRequestConfig:
    """构造一个最小可用的 ModelConfig"""
    return ModelRequestConfig(
        model="gpt-3.5-turbo",
        temperature=0.8,
        top_p=0.9,
    )


@pytest.fixture
def fake_model_client_config() -> ModelClientConfig:
    """构造一个最小可用的 ModelConfig"""
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        timeout=30,
        max_retries=3,
        verify_ssl=False
    )


class FakeModel(Model):
    def __init__(self, api_key=None, api_base=None):
        # 创建假的配置以满足 Model 的初始化要求
        model_client_config = ModelClientConfig(
            client_id="fake",
            client_provider="OpenAI",
            api_key=api_key or "fake-key",
            api_base=api_base or "http://fake.api.com",
            timeout=60,
            max_retries=3,
            verify_ssl=False,
            ssl_cert=None
        )
        model_config = ModelRequestConfig(
            model="fake-model",
            temperature=0.7,
            top_p=0.9
        )
        super().__init__(model_client_config=model_client_config, model_config=model_config)
        # Override the _client to avoid actual API calls
        self._client = self
    
    async def invoke(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any):
        return AssistantMessage(role="assistant", content="mocked response")

    async def stream(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any) -> AsyncIterator[
        AssistantMessageChunk]:
        yield AssistantMessageChunk(role="assistant", content="mocked response")


@patch(
    "openjiuwen.core.workflow.components.llm.llm_comp.Model",
    autospec=True,
)
class TestLLMExecutableInvoke:
    @pytest.mark.asyncio
    async def test_invoke_success(
            self,
            mock_model,  # 这就是补丁
            fake_node_ctx,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )
        
        fake_llm = FakeModel(api_base="http://fake.api.com", api_key="fake-key")
        mock_model.return_value = fake_llm
        
        exe = LLMExecutable(config)

        output = await exe.invoke(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock())

        assert output == {'result': 'mocked response'}

    @pytest.mark.asyncio
    async def test_stream_success(
            self,
            mock_model,  # 这就是补丁
            fake_node_ctx,
            fake_input,
            fake_model_config,
            fake_model_client_config
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        fake_llm = FakeModel(api_base="http://fake.api.com", api_key="fake-key")
        mock_model.return_value = fake_llm
        
        exe = LLMExecutable(config)

        # 调用 stream 方法，异步迭代所有 chunk
        chunks = []
        async for chunk in exe.stream(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock()):
            chunks.append(chunk)

        # FakeModel.astream 只产生一个 chunk
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_invoke_llm_exception(
            self,
            mock_model,
            fake_node_ctx,
            fake_input,
            fake_model_config,
            fake_model_client_config
    ):
        config = LLMCompConfig(model_client_config=fake_model_client_config,
                               model_config=fake_model_config,
                               template_content=[{"role": "user", "content": "Hello {name}"}],
                               response_format={"type": "text"},)
        try:
            exe = LLMExecutable(config)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.COMPONENT_LLM_CONFIG_INVALID.code

    @pytest.mark.asyncio  # 新增
    async def test_llm_in_workflow(
            self,
            mock_model,
            fake_model_config,
            fake_model_client_config
    ):
        """LLM 节点在完整工作流中的异步测试"""
        session = create_workflow_session()

        # 1. 打桩 LLM
        fake_llm = FakeModel(api_key="fake-key", api_base="http://fake.api.com")
        mock_model.return_value = fake_llm

        # 2. 构造工作流
        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${user.inputs.a}",
                                "b": "${user.inputs.b}"})

        flow.set_end_comp(
            "end",
            MockEndNode("end"),
            inputs_schema={"a": "${a.result}", "b": "${b.result}"},
        )

        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {name}"}],
            response_format={"type": "text"},
            output_config={"result": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)
        flow.add_workflow_comp("llm", llm_comp,
                               inputs_schema={"a": "${start.a}",
                                              "userFields": {"query": "${start.query}"}})

        flow.add_connection("start", "llm")
        flow.add_connection("llm", "end")

        # 3. 直接异步调用
        result = await flow.invoke(inputs={"a": 2, "userFields": dict(query="pytest")}, session=session)
        assert result is not None

    @pytest.mark.asyncio  # 新增
    async def test_start_llm_end_in_workflow(self, mock_model,
                                             fake_model_config, fake_model_client_config):
        fake_llm = FakeModel(api_key="fake-key", api_base="http://fake.api.com")
        mock_model.return_value = fake_llm

        flow = Workflow()

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {name}"}],
            response_format={"type": "text"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${llm.output}"})
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        context = create_workflow_session()
        result = await flow.invoke(inputs={"query": "yzq test query"}, session=context)
        print(f"This is invoke result:{result}")

class TestLLMExecutableInvokeNew:
    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_agent_stream_start_llm_end_with_stream_writer(self,
                                            fake_model_config, fake_model_client_config):
        id = "write_poem_workflow"
        version = "1.0"
        name = "poem"
        flow = Workflow(card=WorkflowCard(name=name, id=id, version=version))

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))

        config = LLMCompConfig(
            model_config=fake_model_config,
            model_client_config=fake_model_client_config,
            template_content=[{"role": "system", "content": "我的系统提示词"}, {"role": "user", "content": "Hello {{query}}"}],
            response_format={"type": "text"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${llm.output}"})
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        """根据 workflow 实例化 WorkflowAgent。"""
        from openjiuwen.core.application.workflow_agent import WorkflowAgent
        workflow_id = flow.card.id
        workflow_name = flow.card.name
        workflow_version = flow.card.version
        schema = WorkflowSchema(id=workflow_id,
                                name=workflow_name,
                                description="写诗工作流",
                                version=workflow_version,
                                inputs={"query": {
                                    "type": "string",
                                }})
        config = WorkflowAgentConfig(
            id="write_poem_agent",
            version="0.1.0",
            description="写诗 single_agent",
            workflows=[schema],
            controller_type=ControllerType.WorkflowController,
        )

        agent = WorkflowAgent(config)
        agent.bind_workflows([flow])

        async for result in agent.stream({"query": "please write a 3-line poem", "conversation_id": "c123"}):
            print(f"async chunk >>> {result}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_invoke_start_llm_end_with_stream_writer(self):
        flow = Workflow()

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))

        config = LLMCompConfig(
            model=model_config,
            template_content=[{"role": "system", "content": "我的系统提示词"}, {"role": "user", "content": "Hello {{query}}"}],
            response_format={"type": "markdown"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${llm.output}"})
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        session_id = "test_llm"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="llm_workflow")
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()
        result = await flow.invoke(inputs={"query": "please write a 3-line poem"},
                                   session=workflow_session, context=workflow_context)
        print(f"invoke result >>> {result}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_invoke_start_llm_end_with_json_output(self,
                                                fake_model_config, fake_model_client_config):
        flow = Workflow()

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "system", "content": "你是一个AI助手，能够帮我提取参数信息"},
                              {"role": "user", "content": "{{query}}"}],
            response_format={"type": "json"},
            output_config={"output": {"type": "array",
                                    "description": "个人信息列表",
                                    "items": {"type": "object",
                                              "properties": {
                                                "name": {"type": "string", "description": "姓名"},
                                                "age": {"type": "integer", "description": "年龄"}
                                                },
                                              "required": ["name", "age"]
                                              },
                                    "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${llm.output}"})
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        session_id = "test_llm"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="llm_workflow")
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()
        result = await flow.invoke(
            inputs={"query": "收集到的个人信息包括：姓名为张三，年龄为18；姓名为李四，年龄20"},
            session=workflow_session, context=workflow_context)
        print(f"invoke result >>> {result}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_stream_start_llm_end_with_component_streaming(self,
                                                fake_model_config, fake_model_client_config):
        flow = Workflow()

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        config = LLMCompConfig(
            model_config=fake_model_config,
            model_client_config=fake_model_client_config,
            template_content=[{"role": "system", "content": "你是一个AI助手，能够帮我完成任务。\n注意：请不要推理，直接输出结果就好了！"},
                              {"role": "user", "content": "Hello {{query}}"}],
            response_format={"type": "markdown"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          stream_inputs_schema={"output": "${llm.output}"}, response_mode="streaming")
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

        flow.add_connection("s", "llm")
        flow.add_stream_connection("llm", "e")

        session_id = "test_llm"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="llm_workflow")
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()
        async for chunk in flow.stream(inputs={"query": "please write a 3-line poem"}, session=workflow_session, context=workflow_context):
            print(f"stream chunk >>> {chunk}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_stream_start_llm_end_with_component_streaming_with_json_output_schema(self,
                                                                        fake_model_config, fake_model_client_config):
        flow = Workflow()

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        config = LLMCompConfig(
            model_config=fake_model_config,
            model_client_config=fake_model_client_config,
            template_content=[{"role": "system", "content": "你是一个AI助手，能够帮我完成任务。\n注意：请不要推理，直接输出结果就好了！"},
                              {"role": "user", "content": "{{query}}"}],
            response_format={"type": "json"},
            output_config={"output": {"type": "array",
                                    "description": "个人信息列表",
                                    "items": {"type": "object",
                                              "properties": {
                                                  "name": {"type": "string", "description": "姓名"},
                                                  "age": {"type": "integer", "description": "年龄"}
                                              },
                                              "required": ["name", "age"]
                                              },
                                    "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          stream_inputs_schema={"output": "${llm.output}"}, response_mode="streaming")
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

        flow.add_connection("s", "llm")
        flow.add_stream_connection("llm", "e")

        session_id = "test_llm"
        config = ContextEngineConfig()
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="llm_workflow")
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()
        async for chunk in flow.stream(inputs={"query": "收集到的个人信息包括：姓名为张三，年龄为18；姓名为李四，年龄20"}, session=workflow_session,
                                       context=workflow_context):
            print(f"stream chunk >>> {chunk}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_agent_invoke_start_llm_end_with_stream_writer(self,
                                            fake_model_config, fake_model_client_config):
        id = "write_poem_workflow"
        version = "1.0"
        name = "poem"
        flow = Workflow(card=WorkflowCard(name=name, id=id, version=version))

        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        config = LLMCompConfig(
            model_config=fake_model_config,
            model_client_config=fake_model_client_config,
            template_content=[{"role": "system", "content": "我的系统提示词"}, {"role": "user", "content": "Hello {{query}}"}],
            response_format={"type": "text"},
            output_config={"output": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"output": "${llm.output}"})
        flow.add_workflow_comp("llm", llm_comp, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "llm")
        flow.add_connection("llm", "e")

        """根据 workflow 实例化 WorkflowAgent。"""
        from openjiuwen.core.application.workflow_agent import WorkflowAgent
        workflow_id = flow.card.id
        workflow_name = flow.card.name
        workflow_version = flow.card.version
        schema = WorkflowSchema(id=workflow_id,
                              name=workflow_name,
                              description="写诗工作流",
                              version=workflow_version,
                              inputs={"query": {
                                  "type": "string",
                              }})
        config = WorkflowAgentConfig(
            id="write_poem_agent",
            version="0.1.0",
            description="写诗 single_agent",
            workflows=[schema],
            controller_type=ControllerType.WorkflowController,
        )

        agent = WorkflowAgent(config)
        agent.bind_workflows([flow])

        result = await agent.invoke({"query": "please write a 3-line poem", "conversation_id": "c123"})
        print(f"single_agent invoke result >>> {result}")


class TestLLMExecutable(LLMExecutable):
    async def prepare_model_inputs(self, input_):
        return await super()._prepare_model_inputs(input_)


class TestLLMModelInputs:
    @pytest.mark.asyncio
    async def test_system_exists_user_missing_appends_empty_user(
            self,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            system_prompt_template=SystemMessage(content="system prompt template"),
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        exe = TestLLMExecutable(config)
        model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))

        assert isinstance(model_inputs[0], SystemMessage)
        assert model_inputs[0].content == "system prompt template"
        assert isinstance(model_inputs[1], UserMessage)
        assert model_inputs[1].content == ""

    @pytest.mark.asyncio
    async def test_system_exists_user_exists_keeps_user(
            self,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            system_prompt_template=SystemMessage(content="system prompt template"),
            user_prompt_template=UserMessage(content="user prompt template"),
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        exe = TestLLMExecutable(config)
        model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))

        assert isinstance(model_inputs[0], SystemMessage)
        assert model_inputs[0].content == "system prompt template"
        assert isinstance(model_inputs[1], UserMessage)
        assert model_inputs[1].content == "user prompt template"

    @pytest.mark.asyncio
    async def test_system_missing_user_exists_keeps_user(
            self,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            user_prompt_template=UserMessage(content="user prompt template"),
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        exe = TestLLMExecutable(config)
        model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))
        assert isinstance(model_inputs[0], UserMessage)
        assert len(model_inputs) == 1
        assert model_inputs[0].content == "user prompt template"

    @pytest.mark.asyncio
    async def test_prepare_model_inputs_system_and_user_keeps_both(
            self,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            system_prompt_template=SystemMessage(content="system prompt template"),
            user_prompt_template=UserMessage(content="user prompt template"),
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        exe = TestLLMExecutable(config)
        model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))
        assert isinstance(model_inputs[0], SystemMessage)
        assert model_inputs[0].content == "system prompt template"
        assert isinstance(model_inputs[1], UserMessage)
        assert model_inputs[1].content == "user prompt template"

    @pytest.mark.asyncio
    async def test_prepare_model_inputs_system_missing_user_missing(
            self,
            fake_input,
            fake_model_client_config,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model_client_config=fake_model_client_config,
            model_config=fake_model_config,
            template_content=[{"role": "system", "content": "hello {query}"},
                              {"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )

        exe = TestLLMExecutable(config)
        model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))
        assert isinstance(model_inputs[0], BaseMessage)
        assert model_inputs[0].content == "hello {query}"
        assert isinstance(model_inputs[1], BaseMessage)
        assert model_inputs[1].content == "Hello {query}"

