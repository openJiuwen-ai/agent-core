from typing import Dict, List, Any, Union
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.llm_comp import LLMCompConfig, LLMExecutable
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.stream.base import StreamMode, BaseStreamMode
from openjiuwen.core.tracer.decorator import decorate_tool_with_trace, decorate_workflow_with_trace, \
    decorate_model_with_trace
from openjiuwen.core.utils.llm.base import BaseModelClient, BaseModelInfo
from openjiuwen.core.utils.llm.messages import BaseMessage
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.constant import Input, Output
from openjiuwen.core.workflow.workflow_config import WorkflowMetadata, WorkflowConfig

pytestmark = pytest.mark.asyncio


def get_llm_config():
    model_config = ModelConfig(model_provider="siliconflow",
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
    def __init__(self):
        super().__init__()
        self.name = "mock tool"

    async def ainvoke(self, inputs: Input, **kwargs) -> Output:
        logger.info(inputs)
        logger.info(f"begin to ainvoke , inputs={inputs}")
        return {}

    def invoke(self, inputs: Input, **kwargs) -> Output:
        logger.info(f"begin to invoke, inputs={inputs}")
        return inputs

    def get_tool_info(self) -> ToolInfo:
        pass


class MockWorkflow:
    async def invoke(self, inputs: Input, runtime: BaseRuntime, context: Context = None):
        logger.info(inputs)
        async for item in self.stream(inputs, runtime, context=context, stream_modes=[BaseStreamMode.CUSTOM]):
            continue
        return inputs

    async def stream(self, inputs: Input, runtime: BaseRuntime, context: Context = None,
                     stream_modes: list[StreamMode] = None):
        logger.info(inputs)
        logger.info(f"begin to ainvoke , inputs={inputs}")
        yield inputs

    def config(self):
        return WorkflowConfig(metadata=WorkflowMetadata(
            name="weather",
            id="test_weather_agent",
            version="1.0",
        ))


class MockModel(BaseModelClient):
    def __init__(self):
        self.api_key = 'api_key'
        self.api_base = 'api_base'
        self.max_retrie = 'max_retrie'
        self.timeout = 2
        self._config = get_llm_config()
        super().__init__("api_key", "")

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


class MockTracer:
    def __init__(self):
        self.results = []

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        self.results.append([handler_class_name, event_name, kwargs])


class TestDecator:
    async def test_decorate_tool(self):
        tool = MockTool()
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
        mock_runtime = MagicMock()
        mock_runtime.tracer.return_value = mock_tracer
        mock_runtime.span.return_value = mock_agent_span

        wrapped_tool = decorate_tool_with_trace(tool, mock_runtime)
        wrapped_tool.invoke({"a": "a"}, context=3)
        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "mock tool"

        results.clear()
        await wrapped_tool.ainvoke({"a": "a"}, context=3)
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
        mock_runtime = MagicMock()
        mock_runtime.tracer.return_value = mock_tracer
        mock_runtime.span.return_value = mock_agent_span

        wrapped_workflow = decorate_workflow_with_trace(workflow, mock_runtime)

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
        model.ainvoke = model.llm.ainvoke
        model.astream = model.llm.astream
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
        mock_runtime = MagicMock()
        mock_runtime.tracer.return_value = mock_tracer
        mock_runtime.span.return_value = mock_agent_span

        mocked_model = decorate_model_with_trace(model, mock_runtime)

        mocked_model.invoke("a", [BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"

        results.clear()
        for item in mocked_model.stream("a", [BaseMessage(role="aa")]):
            print(item)

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"

        results.clear()
        await mocked_model.ainvoke("a", [BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"
