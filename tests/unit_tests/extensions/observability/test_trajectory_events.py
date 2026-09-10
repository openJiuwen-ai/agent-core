# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.semconv import (
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_EVENT_ID,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_SCHEMA_VERSION,
    OJ_TRAJECTORY_SEQUENCE_EPOCH,
    OJ_TRAJECTORY_SESSION_ID,
    OJ_TRAJECTORY_SUBJECT_ID,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
    OJ_TURN_NUMBER,
)
from openjiuwen.extensions.observability.span_context import (
    clear_root_span,
    queue_context_window_compaction,
    reset_state,
    set_root_span,
    set_current_agent_span,
)
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserPayload, AskUserRail
from openjiuwen.harness.tools.ask_user import AskUserTool
from openjiuwen.extensions.observability.trajectory_events import (
    emit_context_window_commit,
    emit_native_trajectory_event,
)


def _attrs(span) -> dict:
    return dict(span.attributes or {})


def _payload(span) -> dict:
    return json.loads(_attrs(span)[OJ_TRAJECTORY_PAYLOAD])


@pytest.mark.asyncio
async def test_ask_user_records_requested_and_resolved_events_across_closed_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("ask-user-events-test")
    rail = AskUserRail()
    rail.tools = [AskUserTool()]
    card = rail.tools[0].card
    ctx = SimpleNamespace(
        agent=SimpleNamespace(ability_manager=SimpleNamespace(get=lambda _name: card)),
    )
    tool_call = ToolCall(
        id="call-ask-user",
        type="function",
        name="ask_user",
        arguments=json.dumps({"query": "Choose", "questions": []}),
        index=0,
    )
    attributes = {
        OJ_SESSION_ID: "ask-session",
        OJ_EXECUTION_SUBJECT_ID: "ask-subject",
        OJ_EXECUTION_SUBJECT_KIND: "team_leader",
        OJ_TURN_NUMBER: 2,
        OJ_STEP_NUMBER: 3,
    }
    requested_parent = tracer.start_span("agent.requested", attributes=attributes)
    set_current_agent_span(requested_parent)
    try:
        decision = await rail.resolve_interrupt(None, tool_call, None)
        rail._record_ask_user_event(ctx, tool_call, None, decision)
    finally:
        requested_parent.end()

    resolved_parent = tracer.start_span("agent.resolved", attributes=attributes)
    set_current_agent_span(resolved_parent)
    try:
        user_input = AskUserPayload(answers={"Choose": "Option A"})
        decision = await rail.resolve_interrupt(
            None,
            tool_call,
            user_input,
        )
        rail._record_ask_user_event(ctx, tool_call, user_input, decision)
    finally:
        resolved_parent.end()
        set_current_agent_span(None)
        provider.shutdown()
        reset_state()

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    requested_event = spans[0].events[0]
    resolved_event = spans[1].events[0]
    assert requested_event.name == "ask_user.requested"
    assert resolved_event.name == "ask_user.resolved"
    requested_payload = json.loads(dict(requested_event.attributes)[OJ_TRAJECTORY_PAYLOAD])
    resolved_payload = json.loads(dict(resolved_event.attributes)[OJ_TRAJECTORY_PAYLOAD])
    assert requested_payload["interaction_id"] == "call-ask-user"
    assert requested_payload["schema"]["name"] == "ask_user"
    assert requested_payload["status"] == "pending"
    assert resolved_payload["interaction_id"] == "call-ask-user"
    assert resolved_payload["answers"] == {"Choose": "Option A"}
    assert resolved_payload["outcome"] == "answered"
    assert resolved_payload["status"] == "completed"


@pytest.mark.asyncio
async def test_canonical_request_and_v2_event_survive_legacy_attribute_pressure() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(
            enabled=True,
            service_name="trajectory-pressure-test",
            backend="otlp",
            max_attributes=40,
        ),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("trajectory-pressure-test").start_span(
        "agent.root",
        attributes={
            OJ_SESSION_ID: "pressure-session",
            OJ_REQUEST_ID: "request-1",
            OJ_RUN_ID: "run-1",
            OJ_TURN_NUMBER: 3,
            OJ_STEP_NUMBER: 7,
            OJ_EXECUTION_SUBJECT_ID: "subject-pressure",
            OJ_EXECUTION_SUBJECT_KIND: "subagent",
            OJ_EXECUTION_SUBJECT_PARENT_ID: "main",
            OJ_EXECUTION_SUBJECT_SESSION_ID: "pressure-subsession",
        },
    )
    set_root_span(root, session_id="pressure-session")
    messages = [{"role": "system", "content": "system"}] + [
        {
            "message_id": f"message-{index}",
            "role": "user",
            "content": f"payload-{index}",
        }
        for index in range(110)
    ]
    try:
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=messages,
            model="fake",
        )
        await framework.trigger(LLMCallEvents.LLM_INPUT, messages=messages)
        events_before_llm_close = [
            span for span in exporter.get_finished_spans()
            if span.name == "context.window.commit"
        ]
        assert len(events_before_llm_close) == 1
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="pressure-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    llm_span = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
    instructions = json.loads(_attrs(llm_span)[GEN_AI_SYSTEM_INSTRUCTIONS])
    history = json.loads(_attrs(llm_span)[GEN_AI_INPUT_MESSAGES])
    assert len(instructions) + len(history) == 111
    assert not any(key.startswith("gen_ai.prompt.") for key in _attrs(llm_span))

    event_span = next(
        span for span in exporter.get_finished_spans()
        if span.name == "context.window.commit"
    )
    attrs = _attrs(event_span)
    payload = _payload(event_span)
    assert attrs[OJ_TRACE_SCHEMA_VERSION] == "2"
    assert attrs[OJ_TRAJECTORY_SCHEMA_VERSION] == "2"
    assert attrs[OJ_TRAJECTORY_EVENT_KIND] == "context.window.commit"
    assert len(attrs[OJ_TRAJECTORY_SEQUENCE_EPOCH]) == 32
    assert attrs[OJ_TRAJECTORY_SESSION_ID] == "pressure-session"
    assert attrs[OJ_SESSION_ID] == "pressure-session"
    assert attrs[OJ_REQUEST_ID] == "request-1"
    assert attrs[OJ_RUN_ID] == "run-1"
    assert attrs[OJ_TURN_NUMBER] == 3
    assert attrs[OJ_STEP_NUMBER] == 7
    assert attrs[OJ_EXECUTION_SUBJECT_SESSION_ID] == "pressure-subsession"
    assert len(attrs[OJ_TRAJECTORY_EVENT_ID]) == 32
    assert payload["complete"] is True
    assert len(payload["messages"]) == 111
    assert payload["base_window_id"] is None
    assert payload["transition_kind"] == "epoch_baseline"
    assert payload["baseline_reason"] == "runtime_epoch_start"
    assert payload["delta"] == []


@pytest.mark.asyncio
async def test_core_occurrence_ids_and_request_system_slot_survive_provider_normalization() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    runtime.initialize(
        ObservabilityConfig(enabled=True, service_name="trajectory-identity-test", backend="otlp"),
        span_exporter_override=exporter,
    )
    framework = Runner.callback_framework
    root = runtime.get_tracer("trajectory-identity-test").start_span(
        "agent.root",
        attributes={
            OJ_SESSION_ID: "identity-session",
            OJ_EXECUTION_SUBJECT_ID: "identity-subject",
        },
    )
    set_root_span(root, session_id="identity-session")

    async def request(system_content: str, *, provider_keeps_system: bool) -> None:
        core_messages = [
            {"role": "system", "content": system_content},
            UserMessage(content="same", metadata={"context_message_id": "context-a"}),
            UserMessage(content="same", metadata={"context_message_id": "context-b"}),
        ]
        provider_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": "same"},
            {"role": "user", "content": "same"},
        ]
        if not provider_keeps_system:
            provider_messages = provider_messages[1:]
        await framework.trigger(LLMCallEvents.LLM_INVOKE_INPUT, messages=core_messages, model="fake")
        await framework.trigger(LLMCallEvents.LLM_INPUT, messages=provider_messages)
        await framework.trigger(LLMCallEvents.LLM_INPUT, messages=provider_messages)
        await framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            result=AssistantMessage(content="done", finish_reason="stop"),
        )

    try:
        await request("system-v1", provider_keeps_system=True)
        await request("system-v2", provider_keeps_system=False)
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="identity-session", expected_span=root)
        runtime.shutdown()
        reset_state()

    events = [span for span in exporter.get_finished_spans() if span.name == "context.window.commit"]
    assert len(events) == 2
    first, second = map(_payload, events)
    assert [message["message_id"] for message in first["messages"]] == [
        "openjiuwen:request-system-slot:0",
        "context-a",
        "context-b",
    ]
    assert [message["message_id"] for message in second["messages"]] == [
        "openjiuwen:request-system-slot:0",
        "context-a",
        "context-b",
    ]
    system_ops = [
        item for item in second["delta"]
        if item["message_id"] == "openjiuwen:request-system-slot:0"
    ]
    assert [item["op"] for item in system_ops] == ["replace"]


def test_context_window_delta_uses_occurrence_identity_and_preserves_history() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-delta-test")
    parent = tracer.start_span(
        "llm.call",
        attributes={
            OJ_SESSION_ID: "delta-session",
            OJ_EXECUTION_SUBJECT_ID: "subject-delta",
        },
    )
    try:
        first = [
            {"message_id": "a", "role": "user", "content": "same"},
            {"message_id": "b", "role": "user", "content": "same"},
            {"message_id": "c", "role": "tool", "content": "old"},
        ]
        second = [
            {"message_id": "b", "role": "user", "content": "same"},
            {"message_id": "a", "role": "user", "content": "same"},
            {"message_id": "c", "role": "tool", "content": "new"},
        ]
        third = [
            {"message_id": "b", "role": "user", "content": "same"},
            {"message_id": "c", "role": "tool", "content": "new"},
            {"message_id": "d", "role": "assistant", "content": "added"},
        ]
        emit_context_window_commit(
            tracer=tracer, llm_span=parent, messages=first, request_purpose="assistant"
        )
        emit_context_window_commit(
            tracer=tracer, llm_span=parent, messages=second, request_purpose="assistant"
        )
        emit_context_window_commit(
            tracer=tracer, llm_span=parent, messages=third, request_purpose="assistant"
        )
    finally:
        parent.end()
        provider.shutdown()
        reset_state()

    events = [span for span in exporter.get_finished_spans() if span.name == "context.window.commit"]
    assert [_attrs(span)[OJ_TRAJECTORY_SUBJECT_SEQUENCE] for span in events] == [1, 2, 3]
    payloads = [_payload(span) for span in events]
    assert [message["message_id"] for message in payloads[0]["messages"]] == ["a", "b", "c"]
    assert [message["content"] for message in payloads[0]["messages"][:2]] == ["same", "same"]
    assert payloads[0]["base_window_id"] is None
    assert payloads[0]["transition_kind"] == "epoch_baseline"
    assert payloads[0]["baseline_reason"] == "runtime_epoch_start"
    assert payloads[0]["delta"] == []
    assert payloads[1]["base_window_id"] == payloads[0]["window_id"]
    assert payloads[2]["base_window_id"] == payloads[1]["window_id"]
    second_ops = {(item["op"], item["message_id"]) for item in payloads[1]["delta"]}
    assert {("move", "a"), ("move", "b"), ("replace", "c")} <= second_ops
    third_ops = {(item["op"], item["message_id"]) for item in payloads[2]["delta"]}
    assert {("remove", "a"), ("move", "c"), ("insert", "d")} <= third_ops
    assert [message["message_id"] for message in payloads[0]["messages"]] == ["a", "b", "c"]


def test_context_window_first_commit_after_epoch_rotation_is_a_full_baseline() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-epoch-baseline-test")
    parent = tracer.start_span(
        "llm.call",
        attributes={
            OJ_SESSION_ID: "baseline-session",
            OJ_EXECUTION_SUBJECT_ID: "baseline-subject",
        },
    )
    messages = [{
        "message_id": "stable-system",
        "role": "system",
        "content": "unchanged",
    }]
    try:
        emit_context_window_commit(
            tracer=tracer,
            llm_span=parent,
            messages=messages,
            request_purpose="assistant",
        )
        reset_state()
        emit_context_window_commit(
            tracer=tracer,
            llm_span=parent,
            messages=messages,
            request_purpose="assistant",
        )
    finally:
        parent.end()
        provider.shutdown()
        reset_state()

    events = [span for span in exporter.get_finished_spans() if span.name == "context.window.commit"]
    assert len(events) == 2
    assert len({_attrs(span)[OJ_TRAJECTORY_SEQUENCE_EPOCH] for span in events}) == 2
    for event in events:
        payload = _payload(event)
        assert payload["base_window_id"] is None
        assert payload["complete"] is True
        assert payload["transition_kind"] == "epoch_baseline"
        assert payload["baseline_reason"] == "runtime_epoch_start"
        assert payload["messages"] == messages
        assert payload["delta"] == []


def test_epoch_baseline_preserves_compaction_correlation_independently() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-baseline-compaction-test")
    parent = tracer.start_span(
        "llm.call",
        attributes={
            OJ_SESSION_ID: "baseline-compaction-session",
            OJ_EXECUTION_SUBJECT_ID: "baseline-compaction-subject",
            OJ_REQUEST_ID: "request-1",
            OJ_STEP_ID: "step-1",
        },
    )
    try:
        queued = queue_context_window_compaction(
            session_id="baseline-compaction-session",
            subject_id="baseline-compaction-subject",
            request_id="request-1",
            step_id="step-1",
            operation_id="operation-1",
        )
        assert queued is True
        emit_context_window_commit(
            tracer=tracer,
            llm_span=parent,
            messages=[{"message_id": "summary", "role": "user", "content": "compacted"}],
            request_purpose="assistant",
        )
    finally:
        parent.end()
        provider.shutdown()
        reset_state()

    event = next(span for span in exporter.get_finished_spans() if span.name == "context.window.commit")
    payload = _payload(event)
    assert payload["transition_kind"] == "epoch_baseline"
    assert payload["baseline_reason"] == "runtime_epoch_start"
    assert payload["correlation_kind"] == "compaction"
    assert payload["caused_by_operation_id"] == "operation-1"
    assert payload["input_window_id"] is None
    assert payload["output_window_id"] == payload["window_id"]
    assert payload["delta"] == []


def test_native_events_share_one_epoch_and_keep_subject_sequences_dense_across_traces() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-epoch-test")
    parents = [
        tracer.start_span(
            "agent.root",
            attributes={
                OJ_SESSION_ID: session_id,
                OJ_EXECUTION_SUBJECT_ID: subject_id,
            },
        )
        for session_id, subject_id in (
            ("shared-session", "subject-a"),
            ("shared-session", "subject-a"),
            ("other-session", "subject-b"),
        )
    ]
    try:
        emit_native_trajectory_event(
            tracer=tracer,
            parent_span=parents[0],
            event_kind="test.first",
            payload={"index": 1},
        )
        emit_native_trajectory_event(
            tracer=tracer,
            parent_span=parents[1],
            event_kind="test.second",
            payload={"index": 2},
        )
        emit_native_trajectory_event(
            tracer=tracer,
            parent_span=parents[2],
            event_kind="test.other-subject",
            payload={"index": 3},
        )
    finally:
        for parent in parents:
            parent.end()
        provider.shutdown()
        reset_state()

    events = [span for span in exporter.get_finished_spans() if span.name.startswith("test.")]
    assert len({_attrs(span)[OJ_TRAJECTORY_SEQUENCE_EPOCH] for span in events}) == 1
    assert [_attrs(span)[OJ_TRAJECTORY_SUBJECT_SEQUENCE] for span in events] == [1, 2, 1]


def test_reset_state_rotates_epoch_and_restarts_subject_sequence() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-epoch-reset-test")
    parent = tracer.start_span(
        "agent.root",
        attributes={
            OJ_SESSION_ID: "reset-session",
            OJ_EXECUTION_SUBJECT_ID: "reset-subject",
        },
    )
    try:
        emit_native_trajectory_event(
            tracer=tracer,
            parent_span=parent,
            event_kind="test.before-reset",
            payload={},
        )
        reset_state()
        emit_native_trajectory_event(
            tracer=tracer,
            parent_span=parent,
            event_kind="test.after-reset",
            payload={},
        )
    finally:
        parent.end()
        provider.shutdown()
        reset_state()

    before, after = [
        span for span in exporter.get_finished_spans() if span.name.startswith("test.")
    ]
    before_attrs = _attrs(before)
    after_attrs = _attrs(after)
    assert before_attrs[OJ_TRAJECTORY_SEQUENCE_EPOCH] != after_attrs[OJ_TRAJECTORY_SEQUENCE_EPOCH]
    assert before_attrs[OJ_TRAJECTORY_SUBJECT_SEQUENCE] == 1
    assert after_attrs[OJ_TRAJECTORY_SUBJECT_SEQUENCE] == 1


@pytest.mark.asyncio
async def test_concurrent_subjects_have_independent_sequence_and_window_state() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trajectory-concurrency-test")
    parents = {
        subject: tracer.start_span(
            "llm.call",
            attributes={
                OJ_SESSION_ID: "shared-session",
                OJ_EXECUTION_SUBJECT_ID: subject,
            },
        )
        for subject in ("subject-a", "subject-b")
    }

    async def emit(subject: str) -> None:
        for index in range(2):
            emit_context_window_commit(
                tracer=tracer,
                llm_span=parents[subject],
                messages=[{
                    "message_id": f"{subject}-{index}",
                    "role": "user",
                    "content": subject,
                }],
                request_purpose="assistant",
            )
            await asyncio.sleep(0)

    try:
        await asyncio.gather(emit("subject-a"), emit("subject-b"))
    finally:
        for parent in parents.values():
            parent.end()
        provider.shutdown()
        reset_state()

    grouped: dict[str, list] = {"subject-a": [], "subject-b": []}
    for span in exporter.get_finished_spans():
        attrs = _attrs(span)
        if attrs.get(OJ_TRAJECTORY_EVENT_KIND) == "context.window.commit":
            grouped[attrs[OJ_TRAJECTORY_SUBJECT_ID]].append(span)
    for subject, events in grouped.items():
        events.sort(key=lambda span: _attrs(span)[OJ_TRAJECTORY_SUBJECT_SEQUENCE])
        assert [_attrs(span)[OJ_TRAJECTORY_SUBJECT_SEQUENCE] for span in events] == [1, 2]
        first, second = map(_payload, events)
        assert first["base_window_id"] is None
        assert second["base_window_id"] == first["window_id"]
        assert all(message["content"] == subject for event in events for message in _payload(event)["messages"])


def test_langfuse_only_span_is_not_a_native_v2_event() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("langfuse-isolation-test")
    span = tracer.start_span(
        "legacy.langfuse",
        attributes={"langfuse.observation.input": "legacy"},
    )
    span.end()
    provider.shutdown()
    attrs = _attrs(exporter.get_finished_spans()[0])
    assert OJ_TRAJECTORY_SCHEMA_VERSION not in attrs
    assert OJ_TRAJECTORY_EVENT_KIND not in attrs
    assert OJ_TRAJECTORY_PAYLOAD not in attrs
