# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.core.context_engine.context.processor_state_recorder import (
    ContextProcessorStateRecorder,
)
from openjiuwen.core.context_engine.schema.context_state import (
    ContextCompressionMetric,
    ContextCompressionSaved,
    ContextCompressionState,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_REQUEST_ID,
    OJ_AGENT_MODE,
    OJ_CONTEXT_OPERATION_ID,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_INFERENCE_ID,
    OJ_REQUEST_ID,
    OJ_REQUEST_PURPOSE,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_REQUEST_ID,
    OJ_TRAJECTORY_SCHEMA_VERSION,
    OJ_TRAJECTORY_SESSION_ID,
    OJ_TRAJECTORY_STEP_ID,
    OJ_TRAJECTORY_SUBJECT_ID,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
    OJ_TRAJECTORY_TURN_ID,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
)
from openjiuwen.extensions.observability.span_context import reset_state
from openjiuwen.extensions.observability.trajectory_events import emit_context_window_commit


def _state(status: str) -> ContextCompressionState:
    return ContextCompressionState(
        operation_id="compression-operation-1",
        status=status,
        phase="get_context_window",
        processor="DialogueCompressor",
        model="model-1",
        before=ContextCompressionMetric(messages=12, tokens=1200),
        after=(
            ContextCompressionMetric(messages=4, tokens=300)
            if status == "completed"
            else None
        ),
        saved=(
            ContextCompressionSaved(messages=8, tokens=900, percent=75.0)
            if status == "completed"
            else None
        ),
        summary="compressed 12 messages into 4",
        compact_summary="durable compacted context",
    )


@pytest.mark.asyncio
async def test_real_recorder_completion_emits_correlated_native_v2_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_state()
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="context-compression-v2-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    recorder = ContextProcessorStateRecorder(
        session_id="sub-session-1",
        context_id="context-1",
        get_session_ref=lambda: None,
    )
    try:
        tracer = runtime.get_tracer("context-compression-v2-test")
        parent_attributes = {
            OJ_SESSION_ID: "root-session-1",
            OJ_REQUEST_ID: "request-1",
            OJ_RUN_ID: "run-1",
            OJ_TURN_ID: "turn-1",
            OJ_TURN_NUMBER: 7,
            OJ_STEP_ID: "step-1",
            OJ_STEP_NUMBER: 3,
            OJ_AGENT_MODE: "single_agent",
            OJ_EXECUTION_SUBJECT_ID: "subagent:one",
            OJ_EXECUTION_SUBJECT_KIND: "subagent",
            OJ_EXECUTION_SUBJECT_PARENT_ID: "main",
            OJ_EXECUTION_SUBJECT_SESSION_ID: "sub-session-1",
        }
        with tracer.start_as_current_span("agent.step", attributes=parent_attributes) as parent:
            with tracer.start_as_current_span(
                "llm.call",
                attributes={
                    GEN_AI_REQUEST_ID: "compaction-request-1",
                    OJ_INFERENCE_ID: "compaction-inference-1",
                },
            ) as llm_span:
                import openjiuwen.extensions.observability.context_compression_handler as handler_module

                monkeypatch.setattr(handler_module, "get_current_llm_span", lambda: llm_span)
                await runtime._context_compression_handler.on_llm_request_input(
                    request_purpose="compaction",
                    context_operation_id="compression-operation-1",
                )
                assert llm_span.attributes[OJ_REQUEST_PURPOSE] == "compaction"
                assert llm_span.attributes[OJ_CONTEXT_OPERATION_ID] == "compression-operation-1"
            with tracer.start_as_current_span(
                "llm.call",
                attributes=parent_attributes,
            ) as baseline_llm_span:
                emit_context_window_commit(
                    tracer=tracer,
                    llm_span=baseline_llm_span,
                    messages=[{
                        "message_id": "message-before-compaction",
                        "role": "user",
                        "content": "before",
                    }],
                    request_purpose="assistant",
                )
            await recorder.emit(object(), _state("started"))
            await recorder.emit(object(), _state("completed"))
            with tracer.start_as_current_span(
                "llm.call",
                attributes=parent_attributes,
            ) as next_llm_span:
                emit_context_window_commit(
                    tracer=tracer,
                    llm_span=next_llm_span,
                    messages=[{
                        "message_id": "message-after-compaction",
                        "role": "user",
                        "content": "continue",
                    }],
                    request_purpose="assistant",
                )
            with tracer.start_as_current_span(
                "llm.call",
                attributes=parent_attributes,
            ) as later_llm_span:
                emit_context_window_commit(
                    tracer=tracer,
                    llm_span=later_llm_span,
                    messages=[{
                        "message_id": "message-after-compaction",
                        "role": "user",
                        "content": "continue",
                    }],
                    request_purpose="assistant",
                )

        spans = list(exporter.get_finished_spans())
        events = [span for span in spans if span.name == "compaction.completed"]
        assert len(events) == 1
        event = events[0]
        assert event.parent.span_id == parent.context.span_id
        assert event.attributes[OJ_TRACE_SCHEMA_VERSION] == "2"
        assert event.attributes[OJ_TRAJECTORY_SCHEMA_VERSION] == "2"
        assert event.attributes[OJ_TRAJECTORY_EVENT_KIND] == "compaction.completed"
        assert event.attributes[OJ_TRAJECTORY_SUBJECT_ID] == "subagent:one"
        assert event.attributes[OJ_TRAJECTORY_SUBJECT_SEQUENCE] == 2
        assert event.attributes[OJ_TRAJECTORY_SESSION_ID] == "root-session-1"
        assert event.attributes[OJ_TRAJECTORY_TURN_ID] == "turn-1"
        assert event.attributes[OJ_TRAJECTORY_STEP_ID] == "step-1"
        assert event.attributes[OJ_TRAJECTORY_REQUEST_ID] == "request-1"
        assert event.attributes[OJ_SESSION_ID] == "root-session-1"
        assert event.attributes[OJ_TURN_NUMBER] == 7
        assert event.attributes[OJ_STEP_NUMBER] == 3
        assert event.attributes[OJ_EXECUTION_SUBJECT_SESSION_ID] == "sub-session-1"
        payload = json.loads(event.attributes[OJ_TRAJECTORY_PAYLOAD])
        assert payload["operation_id"] == "compression-operation-1"
        assert payload["status"] == "completed"
        assert payload["context_id"] == "context-1"
        assert payload["context_session_id"] == "sub-session-1"
        assert payload["compact_summary"] == "durable compacted context"
        assert payload["before"]["messages"] == 12
        assert payload["after"]["messages"] == 4
        assert payload["model_requests"] == [{
            "request_id": "compaction-request-1",
            "inference_id": "compaction-inference-1",
        }]
        context_events = [span for span in spans if span.name == "context.window.commit"]
        assert len(context_events) == 3
        baseline_payload = json.loads(
            context_events[0].attributes[OJ_TRAJECTORY_PAYLOAD]
        )
        transition_payload = json.loads(
            context_events[1].attributes[OJ_TRAJECTORY_PAYLOAD]
        )
        assert transition_payload["transition_kind"] == "compaction"
        assert transition_payload["caused_by_operation_id"] == "compression-operation-1"
        assert transition_payload["input_window_id"] == baseline_payload["window_id"]
        assert transition_payload["input_window_id"] == transition_payload["base_window_id"]
        assert transition_payload["output_window_id"] == transition_payload["window_id"]
        later_payload = json.loads(context_events[2].attributes[OJ_TRAJECTORY_PAYLOAD])
        assert "transition_kind" not in later_payload
        assert "caused_by_operation_id" not in later_payload
        assert "input_window_id" not in later_payload
        assert "output_window_id" not in later_payload
    finally:
        runtime.shutdown()
        reset_state()
