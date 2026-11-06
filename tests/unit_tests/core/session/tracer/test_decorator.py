from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Union,
)
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage,
    BaseModelClient,
    BaseOutputParser,
    ModelClientConfig,
    ModelRequestConfig,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.tool import (
    Input,
    Output,
    Tool,
    ToolCard,
    ToolInfo,
)
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.stream import (
    BaseStreamMode,
    StreamMode,
)
from openjiuwen.core.session.tracer import (
    decorate_model_with_trace,
    decorate_tool_with_trace,
    decorate_workflow_with_trace,
)
from openjiuwen.core.workflow import (
    LLMCompConfig,
    WorkflowCard,
)
from openjiuwen.core.workflow.components.llm.llm_comp import LLMExecutable

pytestmark = pytest.mark.asyncio


def get_llm_config():
    model_config = ModelRequestConfig(
        model="Qwen/Qwen3-32B",
        temperature=0.7,
        top_p=0.9,
    )

    model_client_config = ModelClientConfig(
        client_provider="SiliconFlow",
        api_key="sk",
        api_base="http://",
        timeout=30,
        max_retries=3,
        verify_ssl=False
    )

    return LLMCompConfig(
        model_config=model_config,
        model_client_config=model_client_config,
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

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass

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

    def _build_request_params(
            self,
            *,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None],
            temperature: Optional[float],
            top_p: Optional[float],
            model: Optional[str],
            stop: Union[Optional[str], None],
            max_tokens: Optional[int],
            stream: bool,
            **kwargs
    ) -> Dict[str, Any]:
        mock_params = {
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "top_p": top_p,
            "model": model,
            "stop": stop,
            "max_tokens": max_tokens,
            "stream": stream
        }
        return mock_params

    async def invoke(self,
                     messages: Union[str, List[BaseMessage], List[dict]],
                     *,
                     tools: Union[List[ToolInfo], List[dict], None] = None,
                     temperature: Optional[float] = None,
                     top_p: Optional[float] = None,
                     model: str = None,
                     max_tokens: Optional[int] = None,
                     stop: Union[Optional[str], None] = None,
                     output_parser: Optional[BaseOutputParser] = None,
                     timeout: float = None,
                     **kwargs):
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        logger.info(f"begin to invoke, inputs={messages}")
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            **kwargs
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=params)
        logger.info(f"Request params: {params}")

        return messages

    async def stream(self,
                     messages: Union[str, List[BaseMessage], List[dict]],
                     *,
                     tools: Union[List[ToolInfo], List[dict], None] = None,
                     temperature: Optional[float] = None,
                     top_p: Optional[float] = None,
                     model: str = None,
                     max_tokens: Optional[int] = None,
                     stop: Union[Optional[str], None] = None,
                     output_parser: Optional[BaseOutputParser] = None,
                     timeout: float = None,
                     **kwargs):
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        logger.info(f"begin to stream, inputs={messages}")
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            **kwargs
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=params)
        logger.info(f"Request params: {params}")
        yield messages

    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        pass

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        pass

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        pass


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
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = mock_agent_span

        wrapped_tool = decorate_tool_with_trace(tool, mock_session)
        await wrapped_tool.invoke({"a": "a"}, context=3)
        for item in results:
            print(item)
        assert len(results) == 2
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "test_tool"

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
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = mock_agent_span

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
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = mock_agent_span

        mocked_model = decorate_model_with_trace(model, mock_session)

        await mocked_model.invoke(model="a", messages=[BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 3
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"
        assert results[1][2].get("llm_params", {}) == {'max_tokens': None,
                                                       'messages': [BaseMessage(role='aa', content='', name=None)],
                                                       'model': 'a', 'stop': None, 'stream': False, 'temperature': None,
                                                       'tools': None, 'top_p': None}

        results.clear()
        async for item in mocked_model.stream(model="a", messages=[BaseMessage(role="aa")]):
            print(item)

        for item in results:
            print(item)
        assert len(results) == 3
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"
        assert results[1][2].get("llm_params", {}) == {'max_tokens': None,
                                                       'messages': [BaseMessage(role='aa', content='', name=None)],
                                                       'model': 'a', 'stop': None, 'stream': True, 'temperature': None,
                                                       'tools': None, 'top_p': None}

        results.clear()
        await mocked_model.invoke(model="a", messages=[BaseMessage(role="aa")])

        for item in results:
            print(item)
        assert len(results) == 3
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "Qwen/Qwen3-32B"
        assert results[1][2].get("llm_params", {}) == {'max_tokens': None,
                                                       'messages': [BaseMessage(role='aa', content='', name=None)],
                                                       'model': 'a', 'stop': None, 'stream': False, 'temperature': None,
                                                       'tools': None, 'top_p': None}
        results.clear()
        await mocked_model.invoke([BaseMessage(role="aa")])
        for item in results:
            print(item)
        assert results[0][2].get("inputs", {}) == {'inputs': [BaseMessage(role='aa', content='', name=None)]}

    async def test_decorate_foundation_model(self):
        # Foundation Model carries ModelRequestConfig at the top level (no
        # `.config` wrapper): the probe must fall through to it instead of the
        # class-name fallback, so shell spans get the real model name.
        class FoundationModelStub:
            def __init__(self):
                self.model_config = ModelRequestConfig(model="qwen3-max")

            async def invoke(self, **kwargs):
                return {}

            async def stream(self, **kwargs):
                if False:
                    yield {}

        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        def mock_sync_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_tracer.sync_trigger = mock_sync_trigger

        mock_session = MagicMock()
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer

        mocked_model = decorate_model_with_trace(FoundationModelStub(), mock_session)

        await mocked_model.invoke(model="a", messages=[BaseMessage(role="aa")])

        assert results[0][1] == "on_llm_start"
        assert results[0][2].get("instance_info", {}).get("class_name", "") == "qwen3-max"


    async def test_decorate_model_identity_passthrough(self):
        """identity= merges definition-scoped keys flat into instance_info.

        Empty values are filtered; without identity= the wire format stays
        exactly class_name/type (callers that pass nothing are unaffected).
        """
        model = MagicMock(LLMExecutable)
        type(model).llm = property(lambda self: self._llm, lambda self, val: setattr(self, '_llm', val))
        type(model).config = property(lambda self: self._config, lambda self, val: setattr(self, '_config', val))
        model.llm = MockModel()
        model.config = get_llm_config()
        model.invoke = model.llm.invoke
        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_session = MagicMock()
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = MagicMock()

        mocked_model = decorate_model_with_trace(
            model, mock_session,
            identity={"workflow_id": "wf-1", "component_id": "node-2",
                      "agent_id": "", "conversation_id": None},
        )
        await mocked_model.invoke(messages=[BaseMessage(role="aa")])
        instance_info = results[0][2]["instance_info"]
        assert instance_info["workflow_id"] == "wf-1"
        assert instance_info["component_id"] == "node-2"
        # Falsy identity values must not land as empty-string keys.
        assert "agent_id" not in instance_info
        assert "conversation_id" not in instance_info
        # Base keys survive the merge untouched.
        assert instance_info["class_name"] == "Qwen/Qwen3-32B"
        assert instance_info["type"] == "llm"

        # Without identity= the decorator keeps the exact pre-identity format.
        results.clear()
        plain_model = decorate_model_with_trace(model, mock_session)
        await plain_model.invoke(messages=[BaseMessage(role="aa")])
        assert results[0][2]["instance_info"] == {
            "class_name": "Qwen/Qwen3-32B", "type": "llm",
            "request_params": {},
            "message_count": 1,
        }

    async def test_decorate_model_stream_frame_selection(self):
        """Stream path picks TTFT from the first chunk and usage from the
        LAST usage-bearing frame; an engine-stamped ttft wins over the
        wrapper's own measurement."""
        from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata

        class _StreamModel:
            def __init__(self):
                self.config = MagicMock()

            async def stream(self, messages, **kwargs):
                yield AssistantMessage(content="partial-1")
                yield AssistantMessage(
                    content="partial-2",
                    usage_metadata=UsageMetadata(input_tokens=10, output_tokens=5),
                )
                yield AssistantMessage(content="partial-3")
                yield AssistantMessage(
                    content="final",
                    usage_metadata=UsageMetadata(
                        input_tokens=11, output_tokens=7, ttft_seconds=0.1
                    ),
                )

            async def invoke(self, messages, **kwargs):
                raise AssertionError("stream test must not hit invoke")

        results = []

        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_agent_span_manager = MagicMock()
        mock_tracer.tracer_agent_span_manager = mock_agent_span_manager

        mock_session = MagicMock()
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = MagicMock()

        mocked_model = decorate_model_with_trace(_StreamModel(), mock_session)

        consumed = [item async for item in mocked_model.stream([BaseMessage(role="user", content="hi")])]
        assert len(consumed) == 4  # wrapper passes every chunk through

        events = [r[1] for r in results]
        assert events == ["on_llm_start", "on_llm_end"]
        end_payload = results[1][2]["outputs"]
        usage = end_payload["usage_metadata"]
        # Usage comes from the LAST usage-bearing frame, not the first.
        assert usage["input_tokens"] == 11
        assert usage["output_tokens"] == 7
        if "ttft_seconds" in UsageMetadata.model_fields:
            # Engine-stamped ttft is authoritative, never overwritten.
            assert usage["ttft_seconds"] == pytest.approx(0.1)
        else:
            # Older schema without the engine-stamp field: the wrapper's
            # measured first-chunk time fills the ttft in.
            assert usage["ttft_seconds"] >= 0

    @staticmethod
    def _tpot_mock_env(results):
        """Shared mock tracer/session for the TPOT stream tests."""
        async def mock_trigger(handler_class_name: str, event_name: str, **kwargs):
            results.append([handler_class_name, event_name, kwargs])

        mock_tracer = MagicMock()
        mock_tracer.trigger = mock_trigger
        mock_tracer.tracer_agent_span_manager = MagicMock()
        mock_session = MagicMock()
        mock_inner_session = MagicMock()
        mock_session._inner = mock_inner_session
        mock_inner_session.tracer.return_value = mock_tracer
        mock_inner_session.span.return_value = MagicMock()
        return mock_session

    async def test_decorate_model_stream_tpot_measured(self):
        """Streamed LLM calls carry inter_token_latency_ms (TPOT): token-level
        denominator (output_tokens - 1) when usage reports tokens; a
        non-streaming invoke has no first-token event and stays honestly
        absent."""
        import asyncio

        from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata

        class _StreamModel:
            def __init__(self):
                self.config = MagicMock()

            async def stream(self, messages, **kwargs):
                yield AssistantMessage(content="partial-1")
                await asyncio.sleep(0.02)
                yield AssistantMessage(
                    content="final",
                    usage_metadata=UsageMetadata(input_tokens=10, output_tokens=7),
                )

            async def invoke(self, messages, **kwargs):
                return AssistantMessage(
                    content="all-at-once",
                    usage_metadata=UsageMetadata(input_tokens=3, output_tokens=5),
                )

        results = []
        mocked_model = decorate_model_with_trace(
            _StreamModel(), self._tpot_mock_env(results)
        )

        consumed = [
            item
            async for item in mocked_model.stream([BaseMessage(role="user", content="hi")])
        ]
        assert len(consumed) == 2
        usage = results[1][2]["outputs"]["usage_metadata"]
        # Measured span (~20 ms) over output_tokens - 1 == 6: a small positive
        # token-level TPOT, never 0 (that would mean "instant generation").
        assert 0 < usage["inter_token_latency_ms"] < 100

        results.clear()
        await mocked_model.invoke([BaseMessage(role="user", content="hi")])
        usage = results[1][2]["outputs"]["usage_metadata"]
        # Non-streaming: TPOT is undefined, absent rather than fabricated.
        # model_dump may carry the key as None on schemas that declare it.
        assert usage.get("inter_token_latency_ms") is None

    async def test_decorate_model_stream_tpot_precedence_and_absence(self):
        """A TPOT already stamped by a streaming producer wins (same rule as
        ttft); a single-chunk stream has no inter-token interval and stays
        honestly absent."""
        import asyncio

        from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata

        stamp_kwargs = {"input_tokens": 10, "output_tokens": 7}
        if "inter_token_latency_ms" in UsageMetadata.model_fields:
            stamp_kwargs["inter_token_latency_ms"] = 42.0

        class _StampedStreamModel:
            def __init__(self):
                self.config = MagicMock()

            async def stream(self, messages, **kwargs):
                yield AssistantMessage(content="partial")
                await asyncio.sleep(0.02)
                yield AssistantMessage(
                    content="final",
                    usage_metadata=UsageMetadata(**stamp_kwargs),
                )

            async def invoke(self, messages, **kwargs):
                raise AssertionError("stamp test must not hit invoke")

        class _SingleChunkModel:
            def __init__(self):
                self.config = MagicMock()

            async def stream(self, messages, **kwargs):
                yield AssistantMessage(
                    content="all-in-one",
                    usage_metadata=UsageMetadata(input_tokens=3, output_tokens=5),
                )

            async def invoke(self, messages, **kwargs):
                raise AssertionError("single-chunk test must not hit invoke")

        results = []
        mock_session = self._tpot_mock_env(results)

        stamped = decorate_model_with_trace(_StampedStreamModel(), mock_session)
        consumed = [
            item
            async for item in stamped.stream([BaseMessage(role="user", content="hi")])
        ]
        assert len(consumed) == 2
        usage = results[1][2]["outputs"]["usage_metadata"]
        if "inter_token_latency_ms" in UsageMetadata.model_fields:
            # Engine-stamped TPOT is authoritative, never overwritten.
            assert usage["inter_token_latency_ms"] == pytest.approx(42.0)
        else:
            # Older schema without the field: the wrapper's own measurement
            # fills the gap in.
            assert 0 < usage["inter_token_latency_ms"] < 100

        results.clear()
        single = decorate_model_with_trace(_SingleChunkModel(), mock_session)
        consumed = [
            item
            async for item in single.stream([BaseMessage(role="user", content="hi")])
        ]
        assert len(consumed) == 1
        usage = results[1][2]["outputs"]["usage_metadata"]
        # One chunk: first == last timestamp, no interval to average — absent.
        assert usage.get("inter_token_latency_ms") is None

    async def test_decorate_model_stream_abandoned_fires_end(self):
        """Mid-stream abandonment must not leave the llm span half open.

        GeneratorExit is a BaseException: it bypasses the except-Exception
        branch, and without the finally compensation the span's ambient
        context leaks and later spans mis-parent onto it. Abandonment here =
        caller aclose() after one chunk; the end event must still fire with
        whatever was seen (partial chunks + their usage).

        Drives the trace handler directly: through the full proxy chain the
        inner generator's finalization is scheduled by asyncio's asyncgen
        hooks (GC timing), which is best-effort-by-design in production but
        non-deterministic in a test. The handler's own finally is the unit
        under contract.
        """
        from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
        from openjiuwen.core.session.tracer.data import InvokeType
        from openjiuwen.core.session.tracer.decorator import _make_trace_stream_wrap_handler

        class _StreamModel:
            def __init__(self):
                self.config = MagicMock()

            async def stream(self, messages, **kwargs):
                yield AssistantMessage(
                    content="partial-1",
                    usage_metadata=UsageMetadata(input_tokens=10, output_tokens=5),
                )
                yield AssistantMessage(content="partial-2")
                yield AssistantMessage(content="never-reaches-caller")

            async def invoke(self, messages, **kwargs):
                raise AssertionError("abandonment test must not hit invoke")

        results = []
        mock_session = self._tpot_mock_env(results)
        handler = _make_trace_stream_wrap_handler(
            mock_session._inner, InvokeType.LLM, {"class_name": "abandoned", "type": "llm"}
        )
        model = _StreamModel()

        async def call_next(*args, **kwargs):
            async for item in model.stream(*args, **kwargs):
                yield item

        agen = handler(call_next, [BaseMessage(role="user", content="hi")])
        first = await agen.__anext__()
        assert first.content == "partial-1"
        await agen.aclose()

        events = [r[1] for r in results]
        # Compensation fires on_llm_end — not on_llm_error: abandonment is
        # the caller's choice, not a model failure.
        assert events == ["on_llm_start", "on_llm_end"]
        end_payload = results[1][2]["outputs"]
        assert [m.content for m in end_payload["outputs"]] == ["partial-1"]
        # Usage seen before abandonment travels with the closing event.
        assert end_payload["usage_metadata"]["input_tokens"] == 10

    async def test_decorate_model_invoke_cancelled_fires_end(self):
        """CancelledError bypasses except Exception; the finally compensation
        must still close the span instead of leaving it half open, and the
        cancellation itself must propagate unchanged."""
        import asyncio

        class _CancelledModel:
            def __init__(self):
                self.config = MagicMock()

            async def invoke(self, messages, **kwargs):
                raise asyncio.CancelledError()

            async def stream(self, messages, **kwargs):
                raise AssertionError("cancel test must not hit stream")

        results = []
        mocked_model = decorate_model_with_trace(
            _CancelledModel(), self._tpot_mock_env(results)
        )

        with pytest.raises(asyncio.CancelledError):
            await mocked_model.invoke([BaseMessage(role="user", content="hi")])

        events = [r[1] for r in results]
        assert events == ["on_llm_start", "on_llm_end"]
        # No partial result existed: the closing event says so honestly.
        assert results[1][2]["outputs"] == {"outputs": None}
