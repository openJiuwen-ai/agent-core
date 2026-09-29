# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.core.context_engine.schema.context_state import (
    ContextCompressionMetric,
    ContextCompressionState,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.context_compression_handler import (
    ContextCompressionObservabilityBridge,
)
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_CONVERSATION_ID,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
)
from openjiuwen.extensions.observability.span_context import (
    advance_context_window,
    reset_state,
    set_root_span,
)


def setup_function() -> None:
    reset_state()


def teardown_function() -> None:
    reset_state()


def _payload(span) -> dict:
    return json.loads(dict(span.attributes)[OJ_TRAJECTORY_PAYLOAD])


def _completed_state(operation_id: str) -> ContextCompressionState:
    return ContextCompressionState(
        operation_id=operation_id,
        status="completed",
        phase="active_compress",
        processor="RoundLevelCompressor",
        before=ContextCompressionMetric(messages=3, tokens=300),
        after=ContextCompressionMetric(messages=2, tokens=120),
        summary="Compressed 3 -> 2 messages",
        compact_summary="<memory_block_round>summary</memory_block_round>",
    )


@pytest.mark.asyncio
async def test_completed_compaction_states_its_event_and_its_output_window() -> None:
    """The bridge records the whole compaction turn: what happened, and the
    window it left behind, stated from the context engine's own messages."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("bridge-test")
    handler = OtelCallbackHandler(ObservabilityConfig(), tracer=tracer)
    bridge = ContextCompressionObservabilityBridge(
        tracer=tracer,
        window_messages=handler.context_window_messages,
    )
    session_id = "bridge-session"
    root = tracer.start_span(
        "agent.run",
        attributes={GEN_AI_CONVERSATION_ID: session_id, OJ_EXECUTION_SUBJECT_ID: "main"},
    )
    set_root_span(root, session_id=session_id)
    # The window the last model request stated, with the request system slot
    # the compaction must carry over untouched.
    advance_context_window(
        session_id=session_id,
        subject_id="main",
        window_id="window-before",
        messages=[
            {"message_id": "openjiuwen:request-system-slot:0", "role": "system", "content": "rules"},
            {"message_id": "u1", "role": "user", "content": "hello", "origin": "external_user"},
            {"message_id": "a1", "role": "assistant", "content": "hi", "origin": "harness_internal"},
            {"message_id": "u2", "role": "user", "content": "more", "origin": "external_user"},
        ],
    )
    context = SimpleNamespace(get_messages=lambda: [
        UserMessage(
            content="<memory_block_round>summary</memory_block_round>",
            metadata={"context_message_id": "summary"},
        ),
        UserMessage(content="more", metadata={"context_message_id": "u2"}),
        AssistantMessage(content="done", metadata={"context_message_id": "a2"}),
    ])
    try:
        await bridge.on_context_compression_state(
            context=context,
            session_id=session_id,
            context_id="default",
            state=_completed_state("operation-1"),
        )
    finally:
        root.end()
        provider.shutdown()

    events = [
        span for span in exporter.get_finished_spans()
        if OJ_TRAJECTORY_EVENT_KIND in dict(span.attributes)
    ]
    assert [dict(span.attributes)[OJ_TRAJECTORY_EVENT_KIND] for span in events] == [
        "compaction.completed",
        "context.window.commit",
    ]
    # Sequence 1 went to the window the model request stated above.
    assert [dict(span.attributes)[OJ_TRAJECTORY_SUBJECT_SEQUENCE] for span in events] == [2, 3]
    completed, commit = (_payload(span) for span in events)
    assert completed["operation_id"] == "operation-1"
    assert commit["transition_kind"] == "compaction"
    assert commit["caused_by_operation_id"] == "operation-1"
    assert commit["base_window_id"] == "window-before"
    assert commit["input_window_id"] == "window-before"
    assert commit["output_window_id"] == commit["window_id"]
    assert commit["model_requests"] == []
    assert [(item["op"], item["message_id"]) for item in commit["delta"]] == [
        ("remove", "u1"),
        ("remove", "a1"),
        ("insert", "summary"),
        ("move", "u2"),
        ("insert", "a2"),
    ]
    inserted = {item["message_id"]: item["message"] for item in commit["delta"] if item["op"] == "insert"}
    assert inserted["summary"]["role"] == "user"
    assert inserted["summary"]["origin"] == "harness_internal"
    assert inserted["a2"]["role"] == "assistant"


@pytest.mark.asyncio
async def test_completed_compaction_without_a_context_states_only_its_event() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("bridge-no-context-test")
    bridge = ContextCompressionObservabilityBridge(
        tracer=tracer,
        window_messages=lambda messages: [],
    )
    session_id = "bridge-no-context-session"
    root = tracer.start_span(
        "agent.run",
        attributes={GEN_AI_CONVERSATION_ID: session_id, OJ_EXECUTION_SUBJECT_ID: "main"},
    )
    set_root_span(root, session_id=session_id)
    try:
        await bridge.on_context_compression_state(
            session_id=session_id,
            context_id="default",
            state=_completed_state("operation-2"),
        )
    finally:
        root.end()
        provider.shutdown()

    kinds = [
        dict(span.attributes)[OJ_TRAJECTORY_EVENT_KIND]
        for span in exporter.get_finished_spans()
        if OJ_TRAJECTORY_EVENT_KIND in dict(span.attributes)
    ]
    assert kinds == ["compaction.completed"]
