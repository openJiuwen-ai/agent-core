# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for Claude SDK stream to OpenTelemetry span bridge."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.observability.claude import ClaudeSpanBridge, NoopClaudeSpanBridge
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.semconv import (
    AT_AGENT_INPUT,
    AT_AGENT_OUTPUT,
    AT_MEMBER_NAME,
    AT_SESSION_ID,
    AT_TEAM_NAME,
    GEN_AI_COMPLETION,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
)
from openjiuwen.agent_teams.observability.setup import get_tracer
from openjiuwen.agent_teams.observability.span_context import (
    get_current_agent_span,
    get_or_create_team_span,
    remove_team_span,
    set_current_agent_span,
)
from openjiuwen.core.session.stream.base import OutputSchema


@pytest.fixture
def in_memory_exporter() -> Iterator[InMemorySpanExporter]:
    """Initialize observability with an in-memory exporter for one test."""
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            service_name="openjiuwen-agent-teams",
            exporter="console",
            redact_prompts=False,
            redact_completions=False,
        ),
        span_exporter_override=exporter,
    )
    get_or_create_team_span("alpha", get_tracer("test.claude_bridge"))
    yield exporter
    span = remove_team_span("alpha")
    if span is not None and span.is_recording():
        span.end()
    shutdown_observability()


def _chunk(chunk_type: str, payload: dict[str, Any], index: int = 0) -> OutputSchema:
    """Build a runtime output chunk."""
    return OutputSchema(type=chunk_type, index=index, payload=payload)


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list[Any]:
    """Return finished spans with the given name."""
    return [span for span in exporter.get_finished_spans() if span.name == name]


def _attr(span: Any, key: str) -> Any:
    """Return one span attribute."""
    return span.attributes.get(key)


def test_claude_bridge_records_turn_output_and_reasoning(in_memory_exporter: InMemorySpanExporter) -> None:
    bridge = ClaudeSpanBridge(
        member_name="ppt-designer",
        member_agent_id="agent-1",
        team_name="alpha",
        session_id="sess-1",
    )

    bridge.start_turn(prompt="make a deck")
    bridge.record_chunk(_chunk("llm_reasoning", {"content": "thinking"}))
    bridge.record_chunk(_chunk("llm_output", {"content": "done"}))
    bridge.finish_turn(status="ok")

    turn_spans = _spans_by_name(in_memory_exporter, "agent.ppt-designer.claude_turn.1")
    assert len(turn_spans) == 1
    span = turn_spans[0]
    assert _attr(span, AT_AGENT_INPUT) == "make a deck"
    assert _attr(span, AT_AGENT_OUTPUT) == "done"
    assert _attr(span, LANGFUSE_OBSERVATION_OUTPUT) == "done"
    assert _attr(span, "claude.reasoning") is None
    assert _attr(span, "claude.turn.status") == "ok"
    assert _attr(span, "agentteam.backend") == "claude"
    assert _attr(span, AT_MEMBER_NAME) == "ppt-designer"
    assert _attr(span, AT_TEAM_NAME) == "alpha"
    assert _attr(span, AT_SESSION_ID) == "sess-1"

    reasoning_spans = _spans_by_name(in_memory_exporter, "llm.reasoning")
    assert len(reasoning_spans) == 1
    reasoning_span = reasoning_spans[0]
    assert reasoning_span.parent.span_id == span.context.span_id
    assert _attr(reasoning_span, LANGFUSE_OBSERVATION_INPUT) == "llm reasoning"
    assert _attr(reasoning_span, LANGFUSE_OBSERVATION_OUTPUT) == "thinking"
    assert _attr(reasoning_span, f"{GEN_AI_COMPLETION}.0.role") == "reasoning"
    assert _attr(reasoning_span, f"{GEN_AI_COMPLETION}.0.is_reasoning") is True
    assert _attr(reasoning_span, f"{GEN_AI_COMPLETION}.0.content") == "thinking"
    assert _attr(reasoning_span, "agentteam.backend") == "claude"


def test_claude_bridge_records_tool_input_and_output(in_memory_exporter: InMemorySpanExporter) -> None:
    bridge = ClaudeSpanBridge(member_name="coder", team_name="alpha", session_id="sess-1")

    bridge.start_turn(prompt="use a tool")
    bridge.record_chunk(
        _chunk(
            "tool_call",
            {
                "name": "Bash",
                "arguments": '{"command":"pwd"}',
                "tool_call_id": "tool-1",
            },
        ),
    )
    bridge.record_chunk(
        _chunk(
            "tool_result",
            {
                "tool_name": "Bash",
                "result": "ok",
                "tool_call_id": "tool-1",
            },
        ),
    )
    bridge.finish_turn(status="ok")

    tool_spans = _spans_by_name(in_memory_exporter, "tool.Bash")
    assert len(tool_spans) == 1
    span = tool_spans[0]
    assert _attr(span, GEN_AI_TOOL_NAME) == "Bash"
    assert _attr(span, GEN_AI_TOOL_INPUT) == '{"command":"pwd"}'
    assert _attr(span, GEN_AI_TOOL_OUTPUT) == "ok"
    assert _attr(span, "claude.tool.call_id") == "tool-1"
    assert _attr(span, "agentteam.backend") == "claude"


def test_claude_bridge_tool_execution_context_binds_and_restores_agent_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    tracer = get_tracer("test.claude_bridge")
    previous_span = tracer.start_span("agent.previous")
    set_current_agent_span(previous_span)
    bridge = ClaudeSpanBridge(member_name="coder", team_name="alpha")

    bridge.start_turn(prompt="work")
    assert get_current_agent_span() is previous_span

    with bridge.tool_execution_context():
        active_span = get_current_agent_span()
        assert active_span is not None
        assert active_span.name == "agent.coder.claude_turn.1"

    bridge.finish_turn(status="ok")

    assert get_current_agent_span() is previous_span
    set_current_agent_span(None)
    previous_span.end()


@pytest.mark.asyncio
async def test_claude_bridge_suppresses_team_tool_sdk_span_and_parents_execution_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    bridge = ClaudeSpanBridge(member_name="coder", team_name="alpha", session_id="sess-1")
    tracer = get_tracer("test.claude_bridge")
    handler = OtelCallbackHandler(
        ObservabilityConfig(
            exporter="console",
            redact_prompts=False,
            redact_completions=False,
        ),
        tracer=tracer,
    )

    bridge.start_turn(prompt="message teammate")
    with bridge.tool_execution_context():
        await handler.on_tool_call_started(
            tool_name="send_message",
            inputs={"to": "reviewer", "content": "please review"},
        )
        await handler.on_tool_call_finished(tool_name="send_message", result="sent")
    bridge.record_chunk(
        _chunk(
            "tool_call",
            {
                "name": "mcp__openjiuwen-team__send_message",
                "arguments": '{"to":"reviewer","content":"please review"}',
                "tool_call_id": "tool-1",
                "is_team_tool": True,
            },
        ),
    )
    bridge.record_chunk(
        _chunk(
            "tool_result",
            {
                "tool_name": "mcp__openjiuwen-team__send_message",
                "result": "sent",
                "tool_call_id": "tool-1",
                "is_team_tool": True,
            },
        ),
    )
    bridge.finish_turn(status="ok")

    turn_span = _spans_by_name(in_memory_exporter, "agent.coder.claude_turn.1")[0]
    team_tool_spans = _spans_by_name(in_memory_exporter, "tool.send_message")
    assert len(team_tool_spans) == 1
    assert team_tool_spans[0].parent.span_id == turn_span.context.span_id
    assert _attr(team_tool_spans[0], GEN_AI_TOOL_NAME) == "send_message"
    assert _spans_by_name(in_memory_exporter, "tool.mcp__openjiuwen-team__send_message") == []


def test_claude_bridge_redacts_tool_content() -> None:
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            exporter="console",
            redact_prompts=True,
            redact_completions=True,
        ),
        span_exporter_override=exporter,
    )
    get_or_create_team_span("alpha", get_tracer("test.claude_bridge"))
    try:
        bridge = ClaudeSpanBridge(member_name="coder", team_name="alpha")
        bridge.start_turn(prompt="secret prompt")
        bridge.record_chunk(
            _chunk(
                "tool_call",
                {
                    "name": "secret_tool",
                    "arguments": "secret arg",
                    "tool_call_id": "tool-1",
                },
            ),
        )
        bridge.record_chunk(
            _chunk(
                "tool_result",
                {
                    "tool_name": "secret_tool",
                    "result": "secret output",
                    "tool_call_id": "tool-1",
                },
            ),
        )
        bridge.finish_turn(status="ok")
    finally:
        span = remove_team_span("alpha")
        if span is not None and span.is_recording():
            span.end()
        shutdown_observability()

    tool_span = _spans_by_name(exporter, "tool.secret_tool")[0]
    assert str(_attr(tool_span, GEN_AI_TOOL_INPUT)).startswith("sha256:")
    assert str(_attr(tool_span, GEN_AI_TOOL_OUTPUT)).startswith("sha256:")


def test_claude_bridge_marks_failed_turn(in_memory_exporter: InMemorySpanExporter) -> None:
    bridge = ClaudeSpanBridge(member_name="coder", team_name="alpha")

    bridge.start_turn(prompt="fail")
    bridge.finish_turn(status="failed", error=RuntimeError("boom"))

    span = _spans_by_name(in_memory_exporter, "agent.coder.claude_turn.1")[0]
    assert _attr(span, "claude.turn.status") == "failed"
    assert _attr(span, "claude.turn.error") == "boom"


def test_claude_bridge_build_returns_noop_when_observability_is_not_initialized() -> None:
    shutdown_observability()

    bridge = ClaudeSpanBridge.build(member_name="coder")

    assert isinstance(bridge, NoopClaudeSpanBridge)
    bridge.start_turn(prompt="ignored")
    bridge.record_chunk(_chunk("llm_output", {"content": "ignored"}))
    bridge.finish_turn(status="ok")
