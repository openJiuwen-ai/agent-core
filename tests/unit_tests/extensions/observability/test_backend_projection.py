# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The backend-shaped view derived from standard GenAI attributes at export."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanContext, TraceFlags

from openjiuwen.extensions.observability.backend_projection import (
    BackendProjectingSpanExporter,
    project_for_backend,
    project_span_for_langfuse,
)


def _span(**attributes) -> ReadableSpan:
    return ReadableSpan(
        name="llm.call",
        context=SpanContext(1, 2, False, trace_flags=TraceFlags(1)),
        attributes=attributes,
    )


def _standard_span() -> ReadableSpan:
    return _span(
        **{
            "gen_ai.system_instructions": json.dumps(
                [{"type": "text", "content": "FIXED PROMPT"}]
            ),
            "gen_ai.input.messages": json.dumps([
                {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": ""}],
                    "tool_calls": [{"id": "t1", "name": "bash"}],
                },
                {"role": "system", "parts": [{"type": "text", "content": "DELTA-1"}]},
            ]),
            "gen_ai.output.messages": json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": "done"}]}]
            ),
            "openjiuwen.request.number": 7,
        }
    )


class _RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.exported: list[ReadableSpan] = []
        self.shutdown_calls = 0

    def export(self, spans) -> SpanExportResult:
        self.exported.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def test_only_the_langfuse_backend_gets_a_projection():
    """Backends that read the standard shape must not be wrapped at all."""
    inner = _RecordingExporter()

    assert project_for_backend(inner, "otlp") is inner
    assert project_for_backend(inner, "") is inner
    assert isinstance(project_for_backend(inner, "langfuse"), BackendProjectingSpanExporter)
    assert isinstance(project_for_backend(inner, "  Langfuse "), BackendProjectingSpanExporter)


def test_indices_are_zero_based_and_contiguous_with_instructions_first():
    """Langfuse's mapper reads indices positionally, so the order has to be the model's."""
    projected = project_span_for_langfuse(_standard_span())

    assert projected.attributes["gen_ai.prompt.0.role"] == "system"
    assert projected.attributes["gen_ai.prompt.0.content"] == "FIXED PROMPT"
    assert projected.attributes["gen_ai.prompt.1.role"] == "user"
    assert projected.attributes["gen_ai.prompt.1.content"] == "hi"
    assert projected.attributes["gen_ai.prompt.2.role"] == "assistant"
    assert projected.attributes["gen_ai.prompt.3.role"] == "system"
    assert projected.attributes["gen_ai.prompt.3.content"] == "DELTA-1"
    assert "gen_ai.prompt.4.role" not in projected.attributes


def test_tool_calls_survive_the_projection():
    projected = project_span_for_langfuse(_standard_span())

    assert json.loads(projected.attributes["gen_ai.prompt.2.tool_calls"]) == [
        {"id": "t1", "name": "bash"},
    ]


def test_the_completion_is_derived_from_the_standard_output():
    projected = project_span_for_langfuse(_standard_span())

    assert projected.attributes["gen_ai.completion.0.role"] == "assistant"
    assert projected.attributes["gen_ai.completion.0.content"] == "done"


def test_a_reasoning_output_is_flagged_as_such():
    span = _span(
        **{
            "gen_ai.output.messages": json.dumps(
                [{"role": "reasoning", "parts": [{"type": "text", "content": "thinking"}]}]
            ),
        }
    )

    projected = project_span_for_langfuse(span)

    assert projected.attributes["gen_ai.completion.0.role"] == "reasoning"
    assert projected.attributes["gen_ai.completion.0.is_reasoning"] is True


def test_the_standard_attributes_are_kept_and_the_source_span_is_untouched():
    """One exported span serves both readers; the recorded span is immutable."""
    span = _standard_span()

    projected = project_span_for_langfuse(span)

    for key in (
        "gen_ai.input.messages",
        "gen_ai.system_instructions",
        "gen_ai.output.messages",
        "openjiuwen.request.number",
    ):
        assert key in projected.attributes
    assert "gen_ai.prompt.0.role" not in span.attributes


def test_span_identity_and_timing_survive_the_projection():
    span = ReadableSpan(
        name="llm.call",
        context=SpanContext(1, 2, False, trace_flags=TraceFlags(1)),
        attributes={"gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": []}])},
        start_time=111,
        end_time=222,
    )

    projected = project_span_for_langfuse(span)

    assert projected.name == span.name
    assert projected.get_span_context() == span.get_span_context()
    assert projected.start_time == 111
    assert projected.end_time == 222


def test_a_span_without_standard_message_attributes_is_returned_unchanged():
    """Non-LLM spans carry nothing to derive from and must not be rebuilt."""
    span = _span(**{"gen_ai.tool.name": "bash"})

    assert project_span_for_langfuse(span) is span


def test_a_message_carrying_plain_content_is_read_too():
    """Older records store content directly rather than as structured parts."""
    span = _span(
        **{"gen_ai.input.messages": json.dumps([{"role": "user", "content": "plain"}])}
    )

    projected = project_span_for_langfuse(span)

    assert projected.attributes["gen_ai.prompt.0.content"] == "plain"


def test_the_exporter_delegates_projected_spans():
    inner = _RecordingExporter()
    exporter = BackendProjectingSpanExporter(inner)

    assert exporter.export([_standard_span()]) is SpanExportResult.SUCCESS
    assert len(inner.exported) == 1
    assert inner.exported[0].attributes["gen_ai.prompt.0.content"] == "FIXED PROMPT"


def test_a_failed_projection_still_exports_the_original_span(monkeypatch):
    """Telemetry shaping must never cost the export itself."""
    inner = _RecordingExporter()
    exporter = BackendProjectingSpanExporter(inner)
    monkeypatch.setattr(
        "openjiuwen.extensions.observability.backend_projection.project_span_for_langfuse",
        lambda span: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    span = _standard_span()

    assert exporter.export([span]) is SpanExportResult.SUCCESS
    assert inner.exported == [span]


def test_shutdown_and_flush_reach_the_wrapped_exporter():
    inner = _RecordingExporter()
    exporter = BackendProjectingSpanExporter(inner)

    exporter.shutdown()
    assert exporter.force_flush(1000) is True
    assert inner.shutdown_calls == 1
