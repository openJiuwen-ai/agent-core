# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, StatusCode, set_span_in_context
from pydantic import BaseModel

from openjiuwen.core.runner import Runner
from openjiuwen.core.foundation.llm import (
    OPENJIUWEN_MESSAGE_PROVENANCE_METADATA,
    AssistantMessage,
    AssistantMessageChunk,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
    UsageMetadata,
)
from openjiuwen.core.foundation.llm.call_scope import (
    LlmCallScope,
    LlmObservationSuppression,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback.events import AgentEvents, LLMCallEvents, ToolCallEvents
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability import demand as demand_module
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_ID,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_STREAM,
    GEN_AI_RESPONSE_FINISH_REASON,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_TTFC,
    GEN_AI_RESPONSE_TTFT_MS,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_TYPE,
    OJ_EVENT_SEQUENCE,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
    OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE,
    OJ_GEN_AI_RESPONSE_COMPLETION_TOKEN_IDS,
    OJ_GEN_AI_RESPONSE_PROVIDER_CONTENT,
    OJ_GEN_AI_RESPONSE_PROVIDER_METADATA,
    OJ_GEN_AI_RESPONSE_PROMPT_TOKEN_IDS,
    OJ_INFERENCE_ID,
    OJ_REQUEST_ID,
    OJ_REQUEST_NUMBER,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_SPAN_FORCED_CLOSE,
    OJ_STREAM_KIND,
    OJ_TRACE_COMPLETE,
    OJ_TRACE_FORCED_CLOSE,
    OJ_TRACE_ROOT,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_RECORD_KIND,
)
from openjiuwen.extensions.observability.span_context import (
    ActiveSpanTracker,
    clear_root_span,
    reset_state,
    set_active_span_tracker,
    set_current_agent_span,
    set_root_span,
)
from openjiuwen.extensions.observability.span_record_processor import (
    OtlpSpanRecord,
    OtlpSpanSnapshotRecord,
    SpanRecordProcessor,
)
from openjiuwen.harness.observability.run_span import close_agent_run_span


class _LiveRecordConsumer:
    def __init__(self) -> None:
        self.records: list[OtlpSpanRecord] = []
        self.snapshots: list[OtlpSpanSnapshotRecord] = []

    def consume(self, record: OtlpSpanRecord) -> None:
        self.records.append(record)

    def consume_snapshot(self, record: OtlpSpanSnapshotRecord) -> None:
        self.snapshots.append(record)


def test_llm_span_omits_unknown_request_model(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("unknown-model-test")
    root = tracer.start_span("agent.root")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="unknown-model-test"),
        tracer=tracer,
    )
    monkeypatch.setattr(
        handler,
        "_get_parent_context_for_llm_tool",
        lambda: set_span_in_context(root),
    )

    span = handler._open_llm_span({"messages": [], "model": "unknown"})

    assert span is not None
    assert GEN_AI_REQUEST_MODEL not in span.attributes
    span.end()
    root.end()
    provider.shutdown()


def test_usage_does_not_fallback_to_legacy_cache_fields() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("standard-cache-test")
    span = tracer.start_span("llm.call")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="standard-cache-test"),
        tracer=tracer,
    )
    state = SimpleNamespace(
        span=span,
        first_chunk_ns=None,
        last_chunk_ns=None,
    )
    usage = UsageMetadata(
        input_tokens=100,
        output_tokens=10,
        total_tokens=110,
        cache_tokens=90,
        cache_creation_input_tokens=80,
    )

    handler._record_usage_attrs(state, usage)

    assert GEN_AI_USAGE_CACHE_TOKENS not in span.attributes
    assert GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS not in span.attributes
    assert GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS not in span.attributes
    span.end()
    provider.shutdown()


async def _emit_callback_flow(framework, session) -> None:
    await framework.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"user_input": "hello"},
        session=session,
    )
    await framework.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=[{"role": "user", "content": "hello"}],
        model="fake",
    )
    await framework.trigger(
        ToolCallEvents.TOOL_CALL_STARTED,
        tool_name="search",
        tool_id="tool-1",
        inputs=((), {"q": "hello"}),
    )
    await framework.trigger(
        ToolCallEvents.TOOL_CALL_FINISHED,
        tool_name="search",
        tool_id="tool-1",
        result={"ok": True},
    )
    await framework.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=[{"role": "user", "content": "hello"}],
        result=SimpleNamespace(
            content="done",
            reasoning_content="",
            finish_reason="stop",
            tool_calls=None,
            usage_metadata=None,
        ),
    )
    await framework.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "agent"},
        session=session,
        result="done",
    )


@pytest.mark.asyncio
async def test_runtime_initialize_wires_global_callback_framework() -> None:
    exporters = [InMemorySpanExporter(), InMemorySpanExporter()]
    runtime = ObservabilityRuntime()
    config = ObservabilityConfig(enabled=True, service_name="callback-test", sample_rate=1.0)
    framework = Runner.callback_framework

    try:
        for exporter in exporters:
            runtime.initialize(config, span_exporter_override=exporter)
            root = runtime.get_tracer("callback-test").start_span("agent.root")
            set_root_span(root, session_id="session-1")
            session = SimpleNamespace(get_session_id=lambda: "session-1")
            try:
                await _emit_callback_flow(framework, session)
            finally:
                if root.is_recording():
                    root.end()
                clear_root_span(session_id="session-1", expected_span=root)
                runtime.shutdown()
                runtime.shutdown()
                reset_state()
    finally:
        runtime.shutdown()
        reset_state()

    names = [span.name for exporter in exporters for span in exporter.get_finished_spans()]
    assert names.count("llm.call") == 2
    assert names.count("tool.search") == 2
    assert names.count("agent.root") == 2


def test_request_numbers_are_additive_and_subject_local_across_turn_roots() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("subject-request-number-test")
    handler = OtelCallbackHandler(
        ObservabilityConfig(
            enabled=True,
            service_name="subject-request-number-test",
        ),
        tracer=tracer,
    )
    opened_spans: list[Span] = []

    def open_root(session_id: str) -> Span:
        root = tracer.start_span(
            "agent.root",
            attributes={
                OJ_SESSION_ID: session_id,
                OJ_EXECUTION_SUBJECT_ID: "main",
            },
        )
        set_root_span(root, session_id=session_id)
        opened_spans.append(root)
        return root

    def open_request() -> Span:
        span = handler._open_llm_span(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "model": "test-model",
            }
        )
        assert span is not None
        opened_spans.append(span)
        return span

    reset_state()
    first_root = open_root("session-a")
    first_main = open_request()
    first_main.end()
    first_root.end()
    clear_root_span(session_id="session-a", expected_span=first_root)

    second_root = open_root("session-a")
    second_main = open_request()
    second_main.end()

    subagent_span = tracer.start_span(
        "agent.explore_agent",
        attributes={OJ_EXECUTION_SUBJECT_ID: "subagent:one"},
    )
    opened_spans.append(subagent_span)
    set_current_agent_span(subagent_span)
    first_subagent = open_request()
    first_subagent.end()
    second_subagent = open_request()
    second_subagent.end()
    set_current_agent_span(None)

    third_main = open_request()
    third_main.end()
    second_root.end()
    clear_root_span(session_id="session-a", expected_span=second_root)

    other_root = open_root("session-b")
    other_main = open_request()

    try:
        assert first_main.attributes[OJ_REQUEST_NUMBER] == 1
        assert second_main.attributes[OJ_REQUEST_NUMBER] == 1
        assert first_subagent.attributes[OJ_REQUEST_NUMBER] == 2
        assert second_subagent.attributes[OJ_REQUEST_NUMBER] == 3
        assert third_main.attributes[OJ_REQUEST_NUMBER] == 4
        assert other_main.attributes[OJ_REQUEST_NUMBER] == 1
        assert first_main.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 1
        assert second_main.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 2
        assert first_subagent.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 1
        assert second_subagent.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 2
        assert third_main.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 3
        assert other_main.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] == 1
    finally:
        set_current_agent_span(None)
        for span in reversed(opened_spans):
            if span.is_recording():
                span.end()
        clear_root_span(session_id="session-b", expected_span=other_root)
        reset_state()
        provider.shutdown()


@pytest.mark.asyncio
async def test_stream_completion_records_the_standard_structured_fields() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    config = ObservabilityConfig(
        enabled=True,
        service_name="stream-contract-test",
        sample_rate=1.0,
        backend="langfuse",
    )
    framework = Runner.callback_framework
    runtime.initialize(config, span_exporter_override=exporter)
    root = runtime.get_tracer("stream-contract-test").start_span("agent.root")
    root.set_attribute("gen_ai.conversation.id", "session-1")
    root.set_attribute("openjiuwen.session.id", "session-1")
    root.set_attribute(OJ_REQUEST_ID, "request-1")
    root.set_attribute(OJ_RUN_ID, "run-1")
    set_root_span(root, session_id="session-1")

    usage = UsageMetadata(
        model_name="provider-model",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        cache_tokens=9,
        cache_read_tokens=3,
        cache_write_tokens=2,
        cache_creation_input_tokens=8,
        reasoning_tokens=2,
        input_cost=0.1,
        output_cost=0.2,
        total_cost=0.3,
    )
    try:
        await framework.trigger(
            LLMCallEvents.LLM_STREAM_INPUT,
            messages=[
                {
                    "role": "system",
                    "content": "Be precise",
                    "metadata": {
                        "_openjiuwen_prompt_attachment_history": True,
                        "mode": "snapshot",
                    },
                },
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-0", "name": "lookup", "arguments": '{"q":"x"}'}
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"value":1}',
                    "tool_call_id": "call-0",
                    "name": "lookup",
                },
            ],
            model="requested-model",
        )
        await framework.trigger(
            LLMCallEvents.LLM_INPUT,
            messages=[
                {
                    "role": "system",
                    "content": "Be precise",
                    "metadata": {
                        "_openjiuwen_prompt_attachment_history": True,
                        "mode": "snapshot",
                    },
                },
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-0", "name": "lookup", "arguments": '{"q":"x"}'}
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"value":1}',
                    "tool_call_id": "call-0",
                    "name": "lookup",
                },
            ],
            is_stream=True,
        )
        await framework.trigger(
            LLMCallEvents.LLM_STREAM_OUTPUT,
            result=AssistantMessageChunk(content="hel", response_id="resp-1"),
        )
        await framework.trigger(
            LLMCallEvents.LLM_STREAM_OUTPUT,
            result=AssistantMessageChunk(
                content="lo",
                reasoning_content="think",
                usage_metadata=usage,
                finish_reason="stop",
                prompt_token_ids=[1, 2],
                completion_token_ids=[3, 4],
                response_id="resp-1",
                response_model="provider-model",
                provider_metadata={"system_fingerprint": "fp", "secret": "drop"},
                provider_content="raw provider answer",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        type="function",
                        name="search",
                        arguments='{"q":"next"}',
                    )
                ],
            ),
        )
        with LlmCallScope(unified_completion=True):
            await framework.trigger(
                LLMCallEvents.LLM_OUTPUT,
                is_stream=True,
                response="hello",
                usage=usage,
            )
        assert not [
            span for span in exporter.get_finished_spans() if span.name == "llm.call"
        ], "provider enrichment must not close a streaming span"

        await framework.trigger(
            LLMCallEvents.LLM_STREAM_COMPLETED,
            result=AssistantMessage(
                content="hello",
                reasoning_content="think",
                usage_metadata=usage,
                finish_reason="stop",
                prompt_token_ids=[1, 2],
                completion_token_ids=[3, 4],
                response_id="resp-1",
                response_model="provider-model",
                provider_metadata={"system_fingerprint": "fp", "secret": "drop"},
                provider_content="raw provider answer",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        type="function",
                        name="search",
                        arguments='{"q":"next"}',
                    )
                ],
            ),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="session-1", expected_span=root)
        runtime.shutdown()
        reset_state()

    llm_span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    attrs = llm_span.attributes
    # The only system turn here is injected prompt-attachment history, which
    # belongs to the chat history rather than to the instructions given
    # alongside it, so it stays in gen_ai.input.messages.
    assert GEN_AI_SYSTEM_INSTRUCTIONS not in attrs
    input_messages = json.loads(attrs[GEN_AI_INPUT_MESSAGES])
    assert input_messages[0]["role"] == "system"
    assert input_messages[0]["openjiuwen"] == {
        "kind": "prompt_attachment_history",
        "mode": "snapshot",
    }
    assert input_messages[1]["parts"][0]["content"] == "hello"
    assert input_messages[2]["parts"][0] == {
        "type": "tool_call",
        "id": "call-0",
        "name": "lookup",
        "arguments": {"q": "x"},
    }
    assert input_messages[3]["parts"][0] == {
        "type": "tool_call_response",
        "id": "call-0",
        "name": "lookup",
        "response": {"value": 1},
    }
    output = json.loads(attrs[GEN_AI_OUTPUT_MESSAGES])[0]
    assert output["parts"][0] == {"type": "reasoning", "content": "think"}
    assert output["parts"][1] == {"type": "text", "content": "hello"}
    assert output["parts"][2] == {
        "type": "tool_call",
        "id": "call-1",
        "name": "search",
        "arguments": {"q": "next"},
    }

    # Existing Langfuse carve-out remains unchanged; additive totals stay raw.
    assert attrs[GEN_AI_USAGE_PROMPT_TOKENS] == 8
    assert attrs[GEN_AI_USAGE_COMPLETION_TOKENS] == 5
    assert attrs[GEN_AI_USAGE_INPUT_TOKENS] == 11
    assert attrs[GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    assert GEN_AI_USAGE_CACHE_TOKENS not in attrs
    assert attrs[GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 3
    assert attrs[GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] == 2
    assert attrs[GEN_AI_USAGE_REASONING_OUTPUT_TOKENS] == 2
    assert attrs[GEN_AI_RESPONSE_FINISH_REASON] == "stop"
    assert list(attrs[GEN_AI_RESPONSE_FINISH_REASONS]) == ["stop"]
    assert attrs[GEN_AI_RESPONSE_ID] == "resp-1"
    assert attrs[GEN_AI_RESPONSE_TTFC] == pytest.approx(
        attrs[GEN_AI_RESPONSE_TTFT_MS] / 1000.0
    )
    assert json.loads(attrs[OJ_GEN_AI_RESPONSE_PROMPT_TOKEN_IDS]) == [1, 2]
    assert json.loads(attrs[OJ_GEN_AI_RESPONSE_COMPLETION_TOKEN_IDS]) == [3, 4]
    assert json.loads(attrs[OJ_GEN_AI_RESPONSE_PROVIDER_METADATA]) == {
        "system_fingerprint": "fp"
    }
    assert attrs[OJ_GEN_AI_RESPONSE_PROVIDER_CONTENT] == "raw provider answer"
    assert attrs[OJ_REQUEST_ID] == "request-1"
    assert attrs[OJ_RUN_ID] == "run-1"

    stream_events = [
        event for event in llm_span.events if event.name == "openjiuwen.stream.chunk"
    ]
    assert [event.attributes[OJ_EVENT_SEQUENCE] for event in stream_events] == [0, 1]
    assert [event.attributes[OJ_STREAM_KIND] for event in stream_events] == [
        "text-delta",
        "reasoning-delta",
    ]


@pytest.mark.asyncio
async def test_structured_input_and_output_share_redaction_decisions() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="redaction-contract-test",
            sample_rate=1.0,
            backend="otlp",
            redact_prompts=True,
            redact_completions=True,
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("redaction-contract-test").start_span("agent.root")
    set_root_span(root, session_id="redaction-session")
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=[
                {"role": "system", "content": "system secret"},
                {"role": "user", "content": "prompt secret"},
            ],
            model="fake",
        )
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="completion secret", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="redaction-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    input_text = json.loads(span.attributes[GEN_AI_INPUT_MESSAGES])[0]["parts"][0]["content"]
    output_text = json.loads(span.attributes[GEN_AI_OUTPUT_MESSAGES])[0]["parts"][0]["content"]
    assert input_text.startswith("sha256:")
    assert output_text.startswith("sha256:")


@pytest.mark.asyncio
async def test_prompt_attachment_provenance_is_additive_and_positioned() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="input-provenance-contract-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("input-provenance-contract-test").start_span("agent.root")
    set_root_span(root, session_id="input-provenance-session")
    repeated_content = "<system-reminder>same reminder</system-reminder>"
    attachment = UserMessage(
        content=repeated_content,
        metadata={
            OPENJIUWEN_MESSAGE_PROVENANCE_METADATA: {
                "kind": "prompt_attachment",
                "scope": "request",
                "private": "must-not-leak",
                "items": [
                    {
                        "id": "session.input-provenance-session.memory",
                        "section": "memory",
                        "kind": "memory",
                        "source": "rail.memory",
                        "priority": 20,
                        "private": "must-not-leak",
                    }
                ],
            }
        },
    )
    messages = [
        {"role": "system", "content": "system baseline"},
        UserMessage(content=repeated_content),
        attachment,
        UserMessage(content="preserved tail"),
    ]
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=messages,
            model="fake",
        )
        await framework.trigger(
            LLMCallEvents.LLM_INPUT,
            messages=[
                {
                    "role": message.get("role") if isinstance(message, dict) else message.role,
                    "content": message.get("content") if isinstance(message, dict) else message.content,
                }
                for message in messages
            ],
        )
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="input-provenance-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    provenance = json.loads(span.attributes[OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE])
    assert provenance == [
        {
            "request_message_index": 2,
            "input_message_index": 1,
            "kind": "prompt_attachment",
            "scope": "request",
            "items": [
                {
                    "id": "session.input-provenance-session.memory",
                    "section": "memory",
                    "kind": "memory",
                    "source": "rail.memory",
                    "priority": 20,
                }
            ],
        }
    ]
    structured = json.loads(span.attributes[GEN_AI_INPUT_MESSAGES])
    assert [message["parts"][0]["content"] for message in structured] == [
        repeated_content,
        repeated_content,
        "preserved tail",
    ]
    assert all("metadata" not in message for message in structured)
    langfuse_input = json.loads(span.attributes[LANGFUSE_OBSERVATION_INPUT])
    assert [message["content"] for message in langfuse_input] == [
        repeated_content,
        repeated_content,
        "preserved tail",
    ]
    assert "private" not in json.dumps(provenance)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_event", "terminal_event"),
    [
        (LLMCallEvents.LLM_INVOKE_INPUT, LLMCallEvents.LLM_INVOKE_OUTPUT),
        (LLMCallEvents.LLM_STREAM_INPUT, LLMCallEvents.LLM_STREAM_COMPLETED),
    ],
)
async def test_prompt_attachment_provenance_survives_attribute_pressure_and_redaction(
    input_event: Any,
    terminal_event: Any,
) -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="input-provenance-pressure-test",
            sample_rate=1.0,
            backend="otlp",
            redact_prompts=True,
            max_attributes=35,
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("input-provenance-pressure-test").start_span("agent.root")
    set_root_span(root, session_id="input-provenance-pressure-session")
    messages = [
        {"role": "system", "content": "system secret"},
        UserMessage(content="first secret"),
        UserMessage(content="second secret"),
        UserMessage(
            content="attachment secret",
            metadata={
                OPENJIUWEN_MESSAGE_PROVENANCE_METADATA: {
                    "kind": "prompt_attachment",
                    "scope": "request",
                    "items": [
                        {
                            "id": "session.pressure.runtime",
                            "section": "runtime",
                            "kind": "runtime",
                            "source": "rail.runtime",
                            "priority": 10,
                        }
                    ],
                }
            },
        ),
    ]
    try:
        await framework.trigger(input_event, messages=messages, model="fake")
        await framework.trigger(
            terminal_event,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(
            session_id="input-provenance-pressure-session",
            expected_span=root,
        )
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    # Every message is recorded now: the per-message expansion that used to
    # crowd this attribute out of the span's budget is gone.
    assert GEN_AI_INPUT_MESSAGES in span.attributes
    provenance_json = span.attributes[OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE]
    assert "secret" not in provenance_json
    assert json.loads(provenance_json) == [
        {
            "request_message_index": 3,
            "input_message_index": 2,
            "kind": "prompt_attachment",
            "scope": "request",
            "items": [
                {
                    "id": "session.pressure.runtime",
                    "section": "runtime",
                    "kind": "runtime",
                    "source": "rail.runtime",
                    "priority": 10,
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_llm_semantic_identity_survives_prompt_attribute_pressure() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="semantic-identity-pressure-test",
            sample_rate=1.0,
            backend="langfuse",
            max_attributes=80,
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("semantic-identity-pressure-test").start_span(
        "agent.root"
    )
    set_root_span(root, session_id="semantic-identity-pressure-session")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system baseline"}
    ]
    for index in range(12):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "name": "lookup",
                            "arguments": json.dumps({"index": index}),
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": json.dumps({"result": index}),
                    "tool_call_id": f"call-{index}",
                    "name": "lookup",
                },
            ]
        )
    try:
        with LlmCallScope("semantic-pressure-call"):
            await framework.trigger(
                LLMCallEvents.LLM_INVOKE_INPUT,
                messages=messages,
                model="fake",
            )
            await framework.trigger(
                LLMCallEvents.LLM_INVOKE_OUTPUT,
                result=AssistantMessage(content="done", finish_reason="stop"),
            )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(
            session_id="semantic-identity-pressure-session",
            expected_span=root,
        )
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    assert span.attributes[GEN_AI_REQUEST_ID] == "semantic-pressure-call"
    assert span.attributes[OJ_INFERENCE_ID] == f"{span.context.span_id:016x}"
    assert span.attributes[GEN_AI_OPERATION_NAME] == "chat"
    assert span.attributes[OJ_TRACE_SCHEMA_VERSION] == "1"
    assert span.attributes[OJ_TRAJECTORY_RECORD_KIND] == "inference"
    assert span.attributes[LANGFUSE_OBSERVATION_TYPE] == "generation"
    assert span.attributes[GEN_AI_REQUEST_STREAM] is False
    assert GEN_AI_OUTPUT_MESSAGES in span.attributes


@pytest.mark.asyncio
async def test_structured_messages_preserve_ordered_multimodal_parts_and_name() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="multimodal-contract-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("multimodal-contract-test").start_span("agent.root")
    set_root_span(root, session_id="multimodal-session")
    input_content = [
        "before attachment",
        {"type": "input_text", "text": "inspect these sources"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc"},
            "detail": "high",
        },
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "pdf-bytes",
            },
            "title": "spec.pdf",
        },
    ]
    output_content = [
        {"type": "output_text", "text": "the sources agree"},
        {"type": "citation", "document_id": "doc-1", "page": 2},
    ]
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=[
                {
                    "role": "user",
                    "name": "named-reviewer",
                    "content": input_content,
                }
            ],
            model="fake",
        )
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(
                content=output_content,
                name="named-assistant",
                finish_reason="stop",
            ),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="multimodal-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    input_message = json.loads(span.attributes[GEN_AI_INPUT_MESSAGES])[0]
    assert input_message["name"] == "named-reviewer"
    assert [part["type"] for part in input_message["parts"]] == [
        "text",
        "input_text",
        "image_url",
        "document",
    ]
    assert input_message["parts"][0]["content"] == "before attachment"
    assert input_message["parts"][1]["content"] == "inspect these sources"
    assert json.loads(input_message["parts"][2]["content"]) == {
        "image_url": {"url": "data:image/png;base64,abc"},
        "detail": "high",
    }
    assert json.loads(input_message["parts"][3]["content"]) == {
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "pdf-bytes",
        },
        "title": "spec.pdf",
    }

    output_message = json.loads(span.attributes[GEN_AI_OUTPUT_MESSAGES])[0]
    assert output_message["name"] == "named-assistant"
    assert output_message["finish_reason"] == "stop"
    assert [part["type"] for part in output_message["parts"]] == [
        "output_text",
        "citation",
    ]
    assert output_message["parts"][0]["content"] == "the sources agree"
    assert json.loads(output_message["parts"][1]["content"]) == {
        "document_id": "doc-1",
        "page": 2,
    }



@pytest.mark.asyncio
async def test_unified_and_legacy_llm_terminals_each_end_exactly_once() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="llm-terminal-contract-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("llm-terminal-contract-test").start_span("agent.root")
    set_root_span(root, session_id="terminal-session")
    try:
        with LlmCallScope("unified-call", unified_completion=True):
            await framework.trigger(
                LLMCallEvents.LLM_STREAM_INPUT,
                messages=[{"role": "user", "content": "unified"}],
                model="fake",
            )
            await framework.trigger(
                LLMCallEvents.LLM_OUTPUT,
                is_stream=True,
                response="unified answer",
                usage=UsageMetadata(input_tokens=1, output_tokens=1, total_tokens=2),
            )
            assert not [span for span in exporter.get_finished_spans() if span.name == "llm.call"]
            await framework.trigger(
                LLMCallEvents.LLM_STREAM_COMPLETED,
                result=AssistantMessage(content="unified answer", finish_reason="stop"),
            )
            # A redundant provider output after the terminal cannot end or
            # mutate a second span.
            await framework.trigger(
                LLMCallEvents.LLM_OUTPUT,
                is_stream=True,
                response="late provider frame",
            )

        with LlmCallScope("legacy-call"):
            await framework.trigger(
                LLMCallEvents.LLM_STREAM_INPUT,
                messages=[{"role": "user", "content": "legacy"}],
                model="fake",
            )
            await framework.trigger(
                LLMCallEvents.LLM_OUTPUT,
                response="legacy answer",
            )
            # New terminal events are harmless for a legacy callback span that
            # the historical LLM_OUTPUT contract already closed.
            await framework.trigger(
                LLMCallEvents.LLM_STREAM_COMPLETED,
                result=AssistantMessage(content="duplicate", finish_reason="stop"),
            )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="terminal-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    llm_spans = [span for span in exporter.get_finished_spans() if span.name == "llm.call"]
    assert len(llm_spans) == 2
    assert {
        json.loads(span.attributes[GEN_AI_OUTPUT_MESSAGES])[0]["parts"][0]["content"]
        for span in llm_spans
    } == {"unified answer", "legacy answer"}


@pytest.mark.asyncio
async def test_internal_probe_callback_flow_does_not_create_trajectory_span() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="internal-probe-suppression-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("internal-probe-suppression-test").start_span(
        "agent.root"
    )
    set_root_span(root, session_id="probe-session")
    try:
        with LlmObservationSuppression(), LlmCallScope(
            "probe-call",
            unified_completion=True,
        ):
            await framework.trigger(
                LLMCallEvents.LLM_INVOKE_INPUT,
                messages=[{"role": "user", "content": "image probe"}],
                model="fake",
            )
            await framework.trigger(
                LLMCallEvents.LLM_INVOKE_OUTPUT,
                result=AssistantMessage(content="red", finish_reason="stop"),
            )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="probe-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    assert not [
        span for span in exporter.get_finished_spans() if span.name == "llm.call"
    ]


@pytest.mark.asyncio
async def test_tool_definitions_model_dump_before_string_fallback() -> None:
    class ToolDefinition(BaseModel):
        type: str
        function: dict
        optional_note: str | None = None

    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="tool-definition-contract-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("tool-definition-contract-test").start_span("agent.root")
    set_root_span(root, session_id="tool-definition-session")
    pydantic_tool = ToolDefinition(
        type="function",
        function={
            "name": "search",
            "description": "Search documents",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
            },
        },
    )
    dict_tool = {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file",
            "parameters": {"type": "object"},
        },
    }
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=[{"role": "user", "content": "find it"}],
            model="fake",
            tools=[pydantic_tool, dict_tool],
        )
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="tool-definition-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    definitions = json.loads(span.attributes[GEN_AI_TOOL_DEFINITIONS])
    assert definitions == [
        {
            "type": "function",
            "function": pydantic_tool.function,
        },
        dict_tool,
    ]


@pytest.mark.asyncio
async def test_tool_definitions_failures_fallback_per_item_without_orphaning_span() -> None:
    class BrokenModelDumpTool:
        def model_dump(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("custom serializer failed")

        def __str__(self) -> str:
            return "broken-model-dump"

    class OpaqueTool:
        def __str__(self) -> str:
            return "opaque-tool"

    class BrokenStringTool:
        def __str__(self) -> str:
            raise RuntimeError("custom string failed")

    cyclic_tool = {}
    cyclic_tool["self"] = cyclic_tool
    tools = [BrokenModelDumpTool(), cyclic_tool, OpaqueTool(), BrokenStringTool()]

    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="tool-definition-fallback-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("tool-definition-fallback-test").start_span("agent.root")
    set_root_span(root, session_id="tool-definition-fallback-session")
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=[{"role": "user", "content": "find it"}],
            model="fake",
            tools=tools,
        )
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(
            session_id="tool-definition-fallback-session",
            expected_span=root,
        )
        runtime.shutdown()
        reset_state()

    llm_spans = [span for span in exporter.get_finished_spans() if span.name == "llm.call"]
    assert len(llm_spans) == 1
    span = llm_spans[0]
    definitions = json.loads(span.attributes[GEN_AI_TOOL_DEFINITIONS])
    assert definitions == [
        "broken-model-dump",
        {"self": "<recursive:dict>"},
        "opaque-tool",
        "<BrokenStringTool>",
    ]
    assert span.status.status_code is StatusCode.OK
    assert OJ_SPAN_FORCED_CLOSE not in span.attributes


@pytest.mark.asyncio
async def test_real_model_stream_early_close_is_forced_unset_before_root(
    monkeypatch,
) -> None:
    class _FakeClient:
        async def invoke(self, **kwargs):
            return AssistantMessage(content="unused")

        async def stream(self, **kwargs):
            yield AssistantMessageChunk(content="first")
            yield AssistantMessageChunk(content="never-consumed", finish_reason="stop")

    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model.create_model_client",
        lambda **kwargs: _FakeClient(),
    )
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="early-close-contract-test",
            sample_rate=1.0,
            backend="otlp",
        ),
        span_exporter_override=exporter,
    )
    root = runtime.get_tracer("early-close-contract-test").start_span("agent.root")
    root.set_attribute(OJ_TRACE_ROOT, True)
    set_root_span(root, session_id="early-close-session")
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="mock",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="fake"),
    )
    try:
        iterator = model.stream(messages=[{"role": "user", "content": "stop early"}])
        first = await anext(iterator)
        assert first.content == "first"
        await iterator.aclose()

        close_agent_run_span(root, session_id="early-close-session")
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="early-close-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    llm_span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    root_span = next(span for span in exporter.get_finished_spans() if span.name == "agent.root")
    assert llm_span.status.status_code is StatusCode.UNSET
    assert llm_span.attributes[OJ_SPAN_FORCED_CLOSE] is True
    assert root_span.attributes[OJ_TRACE_FORCED_CLOSE] is True
    assert root_span.attributes[OJ_TRACE_COMPLETE] is True
    assert llm_span.end_time <= root_span.end_time


@pytest.mark.asyncio
async def test_stream_callbacks_publish_recoverable_live_snapshots(
    monkeypatch,
) -> None:
    processor = SpanRecordProcessor()
    consumer = _LiveRecordConsumer()
    processor.register_consumer(consumer)
    provider = TracerProvider()
    tracker = ActiveSpanTracker()
    provider.add_span_processor(tracker)
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("live-callback-test")
    monkeypatch.setattr(demand_module, "_SPAN_RECORD_PROCESSOR", processor)
    reset_state()
    set_active_span_tracker(tracker)
    root = tracer.start_span(
        "agent.root",
        attributes={
            OJ_TRACE_ROOT: True,
            "gen_ai.conversation.id": "live-session",
        },
    )
    set_root_span(root, session_id="live-session")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="live-callback-test"),
        tracer=tracer,
    )

    try:
        with LlmCallScope(unified_completion=True):
            await handler.on_llm_stream_input(
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )
            await handler.on_llm_input(
                messages=[{"role": "user", "content": "hello"}],
            )
            await handler.on_llm_stream_output(
                result=AssistantMessageChunk(content="hel"),
            )
            await handler.on_llm_stream_output(
                result=AssistantMessageChunk(content="lo", finish_reason="stop"),
            )
            await handler.on_llm_stream_completed(
                result=AssistantMessage(content="hello", finish_reason="stop"),
            )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="live-session", expected_span=root)
        set_active_span_tracker(None)
        reset_state()
        provider.shutdown()

    llm_snapshots = [record for record in consumer.snapshots if record.name == "llm.call"]
    assert [record.update_kind for record in llm_snapshots] == [
        "started",
        "attributes",
        "attributes",
        "stream_chunk",
        "stream_chunk",
    ]
    assert [record.record_revision for record in llm_snapshots] == [1, 2, 3, 4, 5]
    final = next(record for record in consumer.records if record.span_id == llm_snapshots[0].span_id)
    assert final.record_revision == 6
    assert final.lifecycle == "final"
    second_chunk_payload = json.loads(llm_snapshots[-1].raw_json)
    second_chunk_span = second_chunk_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert [event["name"] for event in second_chunk_span["events"]] == [
        "llm.chunk",
        "openjiuwen.stream.chunk",
        "llm.chunk",
        "openjiuwen.stream.chunk",
    ]



def test_request_numbers_are_allocated_without_a_root_span() -> None:
    """A call made while no root span is registered still gets numbered.

    The counter used to live on the root span object, so these calls got no
    number at all and the trajectory UI invented substitutes for the gaps.
    """
    from openjiuwen.extensions.observability import callback_handler as handler_module

    reset_state()
    handler_module._FALLBACK_REQUEST_SEQUENCES.clear()
    try:
        allocated = [
            OtelCallbackHandler._next_request_number() for _ in range(3)
        ]
    finally:
        handler_module._FALLBACK_REQUEST_SEQUENCES.clear()
        reset_state()

    assert allocated == [1, 2, 3]
