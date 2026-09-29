# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""What the emitters write is what the published v2 payload schema describes,
and what the evolution projection can replay."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.windows import replay_windows
from openjiuwen.core.context_engine.schema.context_state import (
    ContextCompressionMetric,
    ContextCompressionModifiedMessage,
    ContextCompressionState,
)
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.context_compression_handler import (
    ContextCompressionObservabilityBridge,
)
from openjiuwen.extensions.observability.span_context import (
    reset_state,
    set_current_agent_span,
    set_root_span,
)
from openjiuwen.extensions.observability.trajectory_events import emit_context_window_commit
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserPayload, AskUserRail
from openjiuwen.harness.tools.ask_user import AskUserTool

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "openjiuwen"
    / "extensions"
    / "observability"
    / "schemas"
    / "trajectory_v2_payloads.schema.json"
)


def setup_function() -> None:
    reset_state()


def teardown_function() -> None:
    reset_state()


def _validate(event_kind: str, payload: dict) -> list[str]:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({**schema, "$ref": schema["eventKinds"][event_kind]})
    return [error.message for error in validator.iter_errors(payload)]


@pytest.mark.asyncio
async def test_emitted_events_follow_the_schema_and_replay_cleanly() -> None:
    exporter = InMemorySpanExporter()
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("payload-schema-test")
    handler = OtelCallbackHandler(ObservabilityConfig(), tracer=tracer)
    bridge = ContextCompressionObservabilityBridge(tracer=tracer, window_messages=handler.context_window_messages)
    # The agent span carries the ask_user events it logged.
    subscription = processor.subscribe(include_span_categories={"event", "agent"})
    session_id = "schema-session"
    attributes = {semconv.GEN_AI_CONVERSATION_ID: session_id, semconv.OJ_EXECUTION_SUBJECT_ID: "main"}
    root = tracer.start_span("agent.run", attributes=attributes)
    set_root_span(root, session_id=session_id)
    set_current_agent_span(root)

    system = SystemMessage(content="rules")
    first = UserMessage(content="hello", metadata={"context_message_id": "u1"})
    answer = AssistantMessage(content="hi", metadata={"context_message_id": "a1"})
    second = UserMessage(content="more", metadata={"context_message_id": "u2"})
    rail = AskUserRail()
    rail.tools = [AskUserTool()]
    card = rail.tools[0].card
    ctx = SimpleNamespace(agent=SimpleNamespace(ability_manager=SimpleNamespace(get=lambda _name: card)))
    tool_call = ToolCall(
        id="call-ask", type="function", name="ask_user", arguments=json.dumps({"query": "Q", "questions": []}), index=0
    )
    try:
        for span_id, messages in (("first", [system, first]), ("second", [system, first, answer, second])):
            llm = tracer.start_span(f"chat {span_id}", attributes=attributes)
            emit_context_window_commit(
                tracer=tracer,
                llm_span=llm,
                messages=handler.context_window_messages(messages),
                request_purpose="assistant",
            )
            llm.end()
        decision = await rail.resolve_interrupt(None, tool_call, None)
        rail._record_ask_user_event(ctx, tool_call, None, decision)
        user_input = AskUserPayload(answers={"Q": "A"})
        decision = await rail.resolve_interrupt(None, tool_call, user_input)
        rail._record_ask_user_event(ctx, tool_call, user_input, decision)
        await bridge.on_context_compression_state(
            context=SimpleNamespace(
                get_messages=lambda: [
                    UserMessage(content="summary", metadata={"context_message_id": "summary"}),
                    second,
                ]
            ),
            session_id=session_id,
            context_id="default",
            state=ContextCompressionState(
                operation_id="operation-1",
                status="completed",
                phase="active_compress",
                processor="RoundLevelCompressor",
                before=ContextCompressionMetric(messages=4, tokens=400),
                after=ContextCompressionMetric(messages=2, tokens=100),
                modified_messages=[
                    ContextCompressionModifiedMessage(
                        message_id="u2",
                        role="user",
                        offload_handle="handle-u2",
                        offload_type="filesystem",
                    ),
                ],
                summary="Compressed 4 -> 2 messages",
            ),
        )
    finally:
        set_current_agent_span(None)
        root.end()

    payloads: list[tuple[str, dict]] = []
    for span in exporter.get_finished_spans():
        attrs = dict(span.attributes)
        if semconv.OJ_TRAJECTORY_EVENT_KIND in attrs:
            payloads.append((attrs[semconv.OJ_TRAJECTORY_EVENT_KIND], json.loads(attrs[semconv.OJ_TRAJECTORY_PAYLOAD])))
        for event in span.events:
            event_attrs = dict(event.attributes or {})
            if semconv.OJ_TRAJECTORY_EVENT_KIND in event_attrs:
                payloads.append(
                    (
                        event_attrs[semconv.OJ_TRAJECTORY_EVENT_KIND],
                        json.loads(event_attrs[semconv.OJ_TRAJECTORY_PAYLOAD]),
                    )
                )
    assert sorted(kind for kind, _ in payloads) == [
        "ask_user.requested",
        "ask_user.resolved",
        "compaction.completed",
        "context.window.commit",
        "context.window.commit",
        "context.window.commit",
    ]
    for kind, payload in payloads:
        assert _validate(kind, payload) == [], kind
    compaction = next(payload for kind, payload in payloads if kind == "compaction.completed")
    assert [(item["message_id"], item["offload_handle"]) for item in compaction["modified_messages"]] == [
        ("u2", "handle-u2"),
    ]

    trajectory, issues = processor.drain(subscription)
    provider.shutdown()
    assert issues == ()
    replay = replay_windows(trajectory)
    assert replay.issues == ()
    assert len(replay.by_inference) == 2
    windows = replay.windows["main"]
    final = list(windows.values())[-1]
    assert [message["message_id"] for message in final] == ["openjiuwen:request-system-slot:0", "summary", "u2"]
