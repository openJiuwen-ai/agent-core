# coding: utf-8

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_span_in_context

from openjiuwen.extensions.observability import metrics as metrics_mod
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.semconv import OJ_GEN_AI_USAGE_TOTAL_COST


@pytest.fixture(autouse=True)
def _reset_usage_accumulator():
    from openjiuwen.extensions.observability import usage_aggregation as usage_mod

    saved = usage_mod._ACCUMULATOR
    usage_mod._ACCUMULATOR = None
    yield
    usage_mod._ACCUMULATOR = saved


class _FakeMetricsRecorder:
    def __init__(self) -> None:
        self.llm_usage_calls: list[tuple] = []
        self.llm_duration_calls: list[tuple] = []
        self.tool_duration_calls: list[tuple] = []
        self.tool_error_calls: list[tuple] = []

    def record_llm_usage(self, agent_id, model, prompt, completion) -> None:
        self.llm_usage_calls.append((agent_id, model, prompt, completion))

    def record_llm_duration(self, agent_id, model, duration_ms) -> None:
        self.llm_duration_calls.append((agent_id, model, duration_ms))

    def record_tool_duration(self, tool_name, agent_id, duration_ms) -> None:
        self.tool_duration_calls.append((tool_name, agent_id, duration_ms))

    def record_tool_error(self, tool_name, agent_id) -> None:
        self.tool_error_calls.append((tool_name, agent_id))


def _handler(monkeypatch, recorder):
    provider = TracerProvider()
    tracer = provider.get_tracer("metrics-test")
    root = tracer.start_span("agent.root")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="metrics-test"),
        tracer=tracer,
    )
    monkeypatch.setattr(handler, "_get_parent_context_for_llm_tool", lambda: set_span_in_context(root))
    monkeypatch.setattr(metrics_mod, "get_metrics_recorder", lambda: recorder)
    return provider, root, handler


def test_llm_close_emits_metrics_when_enabled(monkeypatch):
    rec = _FakeMetricsRecorder()
    provider, _root, handler = _handler(monkeypatch, rec)

    span = handler._open_llm_span({"messages": [], "model": "gpt-4o"})
    assert span is not None
    span.set_attribute("gen_ai.usage.input_tokens", 10)
    span.set_attribute("gen_ai.usage.output_tokens", 20)
    span.set_attribute("gen_ai.response.model", "gpt-4o")
    span.set_attribute("gen_ai.agent.name", "agent-1")
    state = getattr(span, "otel_llm_state")

    handler._close_llm_span(state, SimpleNamespace())

    assert rec.llm_usage_calls == [("agent-1", "gpt-4o", 10, 20)]
    assert len(rec.llm_duration_calls) == 1
    provider.shutdown()


@pytest.mark.asyncio
async def test_tool_error_emits_error_metric(monkeypatch):
    rec = _FakeMetricsRecorder()
    provider, _root, handler = _handler(monkeypatch, rec)
    monkeypatch.setattr(handler, "_metrics_agent_id", lambda span: "agent-1")

    await handler.on_tool_call_started(tool_name="bash", tool_id=None, inputs=None)
    await handler.on_tool_call_error(tool_name="bash", error=RuntimeError("boom"), tool_id=None)

    assert ("bash", "agent-1") in rec.tool_error_calls
    assert rec.tool_duration_calls
    provider.shutdown()


def test_cost_estimated_when_provider_omits_it(monkeypatch):
    import openjiuwen.extensions.observability.cost_tracker as ct
    from openjiuwen.extensions.observability.cost_tracker import ModelPrice, register_model_prices

    saved_prices = ct._PRICING
    saved_version = ct._VERSION
    register_model_prices("test", {"my-model": ModelPrice(1.0, 2.0)})
    try:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("cost-test")
        root = tracer.start_span("agent.root")
        handler = OtelCallbackHandler(
            ObservabilityConfig(enabled=True, service_name="cost-test"),
            tracer=tracer,
        )
        monkeypatch.setattr(
            handler,
            "_get_parent_context_for_llm_tool",
            lambda: set_span_in_context(root),
        )
        monkeypatch.setattr(metrics_mod, "get_metrics_recorder", lambda: None)

        span = handler._open_llm_span({"messages": [], "model": "my-model"})
        assert span is not None
        state = getattr(span, "otel_llm_state")
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            model_name="my-model",
        )
        response = SimpleNamespace(
            content="done",
            reasoning_content="",
            finish_reason="stop",
            tool_calls=None,
            usage_metadata=usage,
        )

        handler._close_llm_span(state, response)

        finished = [s for s in exporter.get_finished_spans() if s.name == "llm.call"]
        assert finished
        span_attrs = finished[0].attributes
        assert abs(span_attrs[OJ_GEN_AI_USAGE_TOTAL_COST] - 0.002) < 1e-9
        provider.shutdown()
    finally:
        ct._PRICING = saved_prices
        ct._VERSION = saved_version


def test_llm_close_accumulates_usage_into_trace_rollup(monkeypatch):
    from openjiuwen.extensions.observability.usage_aggregation import get_accumulator

    rec = _FakeMetricsRecorder()
    provider, _root, handler = _handler(monkeypatch, rec)
    accumulator = get_accumulator()
    try:
        span = handler._open_llm_span({"messages": [], "model": "gpt-4o"})
        assert span is not None
        span.set_attribute("gen_ai.usage.input_tokens", 1000)
        span.set_attribute("gen_ai.usage.output_tokens", 500)
        state = getattr(span, "otel_llm_state")
        trace_id = span.context.trace_id

        handler._close_llm_span(state, SimpleNamespace())

        snap = accumulator.snapshot(trace_id)
        assert snap["prompt_tokens"] == 1000
        assert snap["completion_tokens"] == 500
        assert snap["tool_calls"] == 0
        accumulator.clear(trace_id)
    finally:
        provider.shutdown()


@pytest.mark.asyncio
async def test_tool_close_accumulates_outcome_into_trace_rollup(monkeypatch):
    from openjiuwen.extensions.observability import span_context as shared_span_context
    from openjiuwen.extensions.observability.usage_aggregation import get_accumulator

    rec = _FakeMetricsRecorder()
    provider, _root, handler = _handler(monkeypatch, rec)
    monkeypatch.setattr(handler, "_metrics_agent_id", lambda span: "agent-1")
    accumulator = get_accumulator()
    try:
        await handler.on_tool_call_started(tool_name="bash", tool_id=None, inputs=None)
        tool_span = shared_span_context.get_current_tool_span()
        assert tool_span is not None
        trace_id = tool_span.context.trace_id
        await handler.on_tool_call_error(tool_name="bash", error=RuntimeError("boom"), tool_id=None)

        snap = accumulator.snapshot(trace_id)
        assert snap["tool_calls"] == 1
        assert snap["tool_errors"] == 1
        accumulator.clear(trace_id)
    finally:
        provider.shutdown()
