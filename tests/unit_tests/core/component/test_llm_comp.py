import os
import sys
import types
import unittest
from typing import Any, Union, List, Dict, AsyncIterator

import pytest
from unittest.mock import Mock

from openjiuwen.agent.common.enum import ControllerType
from openjiuwen.agent.common.schema import WorkflowSchema
from openjiuwen.agent.config.workflow_config import WorkflowAgentConfig
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.context_engine.config import ContextEngineConfig
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.utils.llm.messages import AIMessage, BaseMessage
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.llm.messages_chunk import BaseMessageChunk
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, ComponentAbility, WorkflowMetadata

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base
sys.modules["openjiuwen.core.common.exception.base"] = fake_exception_module

from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode

from unittest.mock import patch, AsyncMock

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.component.llm_comp import LLMCompConfig, LLMExecutable, LLMComponent
from openjiuwen.core.runtime.workflow import WorkflowRuntime, NodeRuntime
from openjiuwen.core.runtime.wrapper import WrappedNodeRuntime, TaskRuntime
from openjiuwen.core.utils.llm.base import BaseModelInfo, BaseModelClient

USER_FIELDS = "userFields"

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


@pytest.fixture
def fake_node_ctx():
    return WrappedNodeRuntime(NodeRuntime(WorkflowRuntime(), "test"))


@pytest.fixture
def fake_input():
    return lambda **kw: {USER_FIELDS: kw}


@pytest.fixture
def fake_model_config() -> ModelConfig:
    """构造一个最小可用的 ModelConfig"""
    return ModelConfig(
        model_provider="openai",
        model_info=BaseModelInfo(
            api_key="sk-fake",
            api_base="https://api.openai.com/v1",
            model_name="gpt-3.5-turbo",
            temperature=0.8,
            top_p=0.9,
            streaming=False,
            timeout=30.0,
        ),
    )

class FakeModel(BaseModelClient):
    def __init__(self, api_key, api_base):
        super().__init__(api_key=api_key, api_base=api_base)

    async def astream(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any) -> AsyncIterator[
        BaseMessageChunk]:
        yield BaseMessageChunk(role="assistant", content="mocked response")

    async def ainvoke(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any):
        return BaseMessageChunk(role="assistant", content="mocked response")


@patch(
    "openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model",
    autospec=True,
)
class TestLLMExecutableInvoke:

    @pytest.mark.asyncio
    async def test_invoke_success(
            self,
            mock_get_model,  # 这就是补丁
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )
        exe = LLMExecutable(config)

        fake_llm = FakeModel(api_base="1111", api_key="ssss")

        mock_get_model.return_value = fake_llm

        output = await exe.invoke(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock())

        assert output == {'result': 'mocked response'}

    @pytest.mark.asyncio
    async def test_stream_success(
            self,
            mock_get_model,  # 这就是补丁
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )
        exe = LLMExecutable(config)

        #
        # fake_llm = FakeModel(api_base="1111", api_key="ssss")
        #
        # # 模拟异步生成器，返回多个 AIMessage chunk
        # async def mock_stream_response(model_name, messa: Any):
        #     for chunk in ["mocked ", "response"]:
        #         yield AIMessage(content=chunk)

        fake_llm = AsyncMock()

        async def mock_stream_response(*, model_name: str, messages: list, **kwargs):
            # yield whatever chunks you want
            for chunk in ["mocked ", "response"]:
                yield AIMessage(content=chunk)

        fake_llm.astream = mock_stream_response
        mock_get_model.return_value = fake_llm

        # 调用 stream 方法，异步迭代所有 chunk
        chunks = []
        async for chunk in exe.stream(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock()):
            chunks.append(chunk)

        # 假设 LLMExecutable.stream 会把每个 AIMessage.content 直接 yield 出来
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_invoke_llm_exception(
            self,
            mock_get_model,
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(model=fake_model_config, template_content=[{"role": "user", "content": "Hello {name}"}], response_format={"type": "text"},)
        try:
            exe = LLMExecutable(config)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR.code

    @pytest.mark.asyncio  # 新增
    async def test_llm_in_workflow(
            self,
            mock_get_model,
            fake_model_config,
    ):
        """LLM 节点在完整工作流中的异步测试"""
        runtime = WorkflowRuntime()

        # 1. 打桩 LLM
        fake_llm = FakeModel(api_key="111", api_base="ssss")
        mock_get_model.return_value = fake_llm

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
            model=fake_model_config,
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
        result = await flow.invoke(inputs={"a": 2, "userFields": dict(query="pytest")}, runtime=runtime)
        assert result is not None

    @pytest.mark.asyncio  # 新增
    async def test_start_llm_end_in_workflow(self, mock_get_model,
                                             fake_model_config):
        fake_llm = FakeModel(api_key="1111", api_base="ssss")
        mock_get_model.return_value = fake_llm

        flow = Workflow()

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{output}}"})

        # when run, use a real model config instead
        fake_model_config = ModelConfig(
            model_provider="openai",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="https://api.openai.com/v1",
                model_name="gpt-3.5-turbo",
                temperature=0.8,
                top_p=0.9,
                streaming=False,
                timeout=30.0,
            ),
        )

        config = LLMCompConfig(
            model=fake_model_config,
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

        context = WorkflowRuntime()
        result = await flow.invoke(inputs={"query": "yzq test query"}, runtime=context)
        print(f"This is invoke result:{result}")

class TestLLMExecutableInvokeNew:
    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_agent_stream_start_llm_end_with_stream_writer(self):
        id = "write_poem_workflow"
        version = "1.0"
        name = "poem"
        flow = Workflow(workflow_config=WorkflowConfig(metadata=WorkflowMetadata(name=name, id=id, version=version, )))

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        from openjiuwen.agent.workflow_agent import WorkflowAgent
        workflow_id = flow.config().metadata.id
        workflow_name = flow.config().metadata.name
        workflow_version = flow.config().metadata.version
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
            description="写诗 agent",
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
        flow = Workflow(workflow_config=WorkflowConfig())

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="llm_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        result = await flow.invoke(inputs={"query": "please write a 3-line poem"}, runtime=workflow_runtime, context=workflow_context)
        print(f"invoke result >>> {result}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_invoke_start_llm_end_with_json_output(self):
        flow = Workflow(workflow_config=WorkflowConfig())

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="llm_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        result = await flow.invoke(
            inputs={"query": "收集到的个人信息包括：姓名为张三，年龄为18；姓名为李四，年龄20"},
            runtime=workflow_runtime, context=workflow_context)
        print(f"invoke result >>> {result}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_stream_start_llm_end_with_component_streaming(self):
        flow = Workflow(workflow_config=WorkflowConfig())

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="llm_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        async for chunk in flow.stream(inputs={"query": "please write a 3-line poem"}, runtime=workflow_runtime, context=workflow_context):
            print(f"stream chunk >>> {chunk}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_stream_start_llm_end_with_component_streaming_with_json_output_schema(self):
        flow = Workflow(workflow_config=WorkflowConfig())

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="llm_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        async for chunk in flow.stream(inputs={"query": "收集到的个人信息包括：姓名为张三，年龄为18；姓名为李四，年龄20"}, runtime=workflow_runtime,
                                       context=workflow_context):
            print(f"stream chunk >>> {chunk}")

    @unittest.skip("skip system test")
    @pytest.mark.asyncio  # 新增
    async def test_real_workflow_agent_invoke_start_llm_end_with_stream_writer(self):
        id = "write_poem_workflow"
        version = "1.0"
        name = "poem"
        flow = Workflow(workflow_config=WorkflowConfig(metadata=WorkflowMetadata(name=name, id=id, version=version,)))

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
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
        from openjiuwen.agent.workflow_agent import WorkflowAgent
        workflow_id = flow.config().metadata.id
        workflow_name = flow.config().metadata.name
        workflow_version = flow.config().metadata.version
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
            description="写诗 agent",
            workflows=[schema],
            controller_type=ControllerType.WorkflowController,
        )

        agent = WorkflowAgent(config)
        agent.bind_workflows([flow])

        result = await agent.invoke({"query": "please write a 3-line poem", "conversation_id": "c123"})
        print(f"agent invoke result >>> {result}")