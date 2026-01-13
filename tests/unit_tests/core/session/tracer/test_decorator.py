from typing import Dict, List, Any, Union
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.workflow import LLMCompConfig, WorkflowCard
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.stream import StreamMode, BaseStreamMode
from openjiuwen.core.session.tracer import decorate_tool_with_trace, decorate_workflow_with_trace, \
    decorate_model_with_trace
from openjiuwen.core.foundation.llm import BaseModelClient, BaseModelInfo
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.tool import Tool, ToolInfo, ToolCard, Input, Output
from openjiuwen.core.workflow.components.llm_related.llm_comp import LLMExecutable

pytestmark = pytest.mark.asyncio


def get_llm_config():
    model_config = ModelConfig(model_provider="SiliconFlow",
                               model_info=BaseModelInfo(
                                   model="Qwen/Qwen3-32B",
                                   api_base="sk",
                                   api_key="http://",
                                   temperature=0.7,
                                   top_p=0.9,
                                   timeout=30
                               ))

    return LLMCompConfig(
        model=model_config,
        template_content=[{"role": "user", "content": "hello"}],
        response_format={"type": "json"},
        output_config={
            "location": {"type": "string", "description": "地点（英文）", "required": True},
            "date": {"type": "string", "description": "日期（YYYY-MM-DD）", "required": True},
            "query": {"type": "string", "description": "改写后的query", "required": True}
        },
    )


class MockTool(Tool):

    def __init__(self, card: ToolCard):
        super().__init__(card)
        self.name = "mock tool"

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        logger.info(inputs)
        logger.info(f"begin to invoke , inputs={inputs}")
        return {}

    def get_tool_info(self) -> ToolInfo:
        pass


class MockWorkflow:
    async def invoke(self, inputs: Input, session: BaseSession, context: ModelContext = None):
        logger.info(inputs)
        async for item in self.stream(inputs, session, context=context, stream_modes=[BaseStreamMode.CUSTOM]):
            continue
        return inputs

    async def stream(self, inputs: Input, session: BaseSession, context: ModelContext = None,
                     stream_modes: list[StreamMode] = None):
        logger.info(inputs)
        logger.info(f"begin to ainvoke , inputs={inputs}")
        yield inputs

    @property
    def card(self):
        return WorkflowCard(
            name="weather",
            id="test_weather_agent",
            version="1.0",
        )


class MockModel(BaseModelClient):
    def __init__(self):
        self.api_key = 'api_key'
        self.api_base = 'api_base'
        self.max_retrie = 'max_retrie'
        self.timeout = 2
        self._config = get_llm_config()
        model_client_config = ModelClientConfig(
            client_provider="SiliconFlow",
            api_key="api_key",
            api_base="http://api_base",
            verify_ssl=False
        )
        model_request_config = ModelRequestConfig(model="Qwen/Qwen3-32B")
        super().__init__(model_request_config, model_client_config)

    def _invoke(self, model_name: str, messages: Union[List[str], List[Dict], str],
                tools: Union[List[ToolInfo], List[Dict]] = None, temperature: float = 0.1,
                top_p: float = 0.1, **kwargs: Any):
        logger.info(f"begin to invoke, inputs={messages}")
        return messages

    def _stream(self, model_name: str, messages: Union[List[str], List[Dict], str],
                tools: Union[List[ToolInfo], List[Dict]] = None, temperature: float = 0.1,
                top_p: float = 0.1, **kwargs: Any):
        logger.info(messages)
        logger.info(f"begin to ainvoke , inputs={messages}")
        yield messages

    async def _astream(self, model_name: str, messages: Union[List[str], List[Dict], str],
                       tools: Union[List[ToolInfo], List[Dict]] = None, temperature: float = 0.1,
                       top_p: float = 0.1, **kwargs: Any):
        logger.info(messages)
        logger.info(f"begin to astream , inputs={messages}")
        yield messages

    async def _ainvoke(self, model_name: str, messages: Union[List[str], List[Dict], str],
                       tools: Union[List[ToolInfo], List[Dict]] = None, temperature: float = 0.1,
                       top_p: float = 0.1, **kwargs: Any):
        logger.info(messages)
        logger.info(f"begin to ainvoke , inputs={messages}")
        return messages

    async def invoke(self, model_name, messages, *, tools=None, temperature=None, top_p=None,
                     max_tokens=None, stop=None, output_parser=None, timeout=None, **kwargs):
        logger.info(f"begin to invoke, inputs={messages}")
        return messages

    async def stream(self, model_name, messages, *, tools=None, temperature=None, top_p=None,
                     max_tokens=None, stop=None, output_parser=None, timeout=None, **kwargs):
        logger.info(f"begin to stream, inputs={messages}")
        yield messages


class MockTracer:
    def __init__(self):
        self.results = []

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        self.results.append([handler_class_name, event_name, kwargs])


class TestDecator:
    async def test_decorate_tool(self):
        tool = MockTool(card=ToolCard(name="test_tool", description="test tool"))
        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        def mock_sync_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_tracer.sync_trigger = mock_sync_trigger
        mock_trigger.side_effect = mock_trigger

        mock_agent_span_manager = MagicMock()
        mock_agent_span_manager.create_agent_span = {}
        mock_trigger.tracer_agent_span_manager = mock_agent_span_manager

        mock_agent_span = MagicMock()
        mock_session = MagicMock()
        mock_session.tracer.return_value = mock_tracer
        mock_session.span.return_value = mock_agent_span

        wrapped_tool = decorate_tool_with_trace(tool, mock_session)
        await wrapped_tool.invoke({"a": "a"}, context=3)
        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "mock tool"

    async def test_decorate_workflow(self):
        workflow = MockWorkflow()

        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        def mock_sync_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_tracer.sync_trigger = mock_sync_trigger
        mock_trigger.side_effect = mock_trigger

        mock_agent_span_manager = MagicMock()
        mock_agent_span_manager.create_agent_span = {}
        mock_trigger.tracer_agent_span_manager = mock_agent_span_manager

        mock_agent_span = MagicMock()
        mock_session = MagicMock()
        mock_session.tracer.return_value = mock_tracer
        mock_session.span.return_value = mock_agent_span

        wrapped_workflow = decorate_workflow_with_trace(workflow, mock_session)

        await wrapped_workflow.invoke({"a": "a"}, MagicMock(), context=None)

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "weather"

    async def test_decorate_model(self):
        model = MagicMock(LLMExecutable)
        type(model).llm = property(lambda self: self._llm, lambda self, val: setattr(self, '_llm', val))
        type(model).config = property(lambda self: self._config, lambda self, val: setattr(self, '_config', val))
        model.llm = MockModel()
        model.config = get_llm_config()
        model.invoke = model.llm.invoke
        model.stream = model.llm.stream
        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        def mock_sync_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_tracer.sync_trigger = mock_sync_trigger
        mock_trigger.side_effect = mock_trigger

        mock_agent_span_manager = MagicMock()
        mock_agent_span_manager.create_agent_span = {}
        mock_trigger.tracer_agent_span_manager = mock_agent_span_manager

        mock_agent_span = MagicMock()
        mock_session = MagicMock()
        mock_session.tracer.return_value = mock_tracer
        mock_session.span.return_value = mock_agent_span

        mocked_model = decorate_model_with_trace(model, mock_session)

        await mocked_model.invoke("a", [BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"

        results.clear()
        async for item in mocked_model.stream("a", [BaseMessage(role="aa")]):
            print(item)

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"

        results.clear()
        await mocked_model.invoke("a", [BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"
