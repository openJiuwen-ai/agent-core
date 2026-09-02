# coding: utf-8

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_span_in_context

from openjiuwen.extensions.observability import metrics as metrics_mod
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig


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
