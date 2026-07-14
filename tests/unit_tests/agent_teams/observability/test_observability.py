# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end style tests for the agent_teams observability subsystem.

We exercise the three injection points (Callback handler, Monitor
handler, Rail) against a real ``Runner.callback_framework`` and assert
on the spans collected by ``InMemorySpanExporter``. We avoid spinning
up an actual Team or DeepAgent; that would require fakes for storage,
messager, model client and worktree, and would not change what we are
trying to verify (the OTel span tree shape).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    ObservabilityRail,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.observability.monitor_handler import OtelTeamMonitorHandler
from openjiuwen.agent_teams.schema.events import (
    BroadcastEvent,
    EventMessage,
    MemberSpawnedEvent,
    MemberStatusChangedEvent,
    MessageEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TeamCleanedEvent,
    TeamCreatedEvent,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import (
    AgentEvents,
    LLMCallEvents,
    ToolCallEvents,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    """Mirror UsageMetadata fields used by the handler."""

    input_tokens: int = 12
    output_tokens: int = 7
    total_tokens: int = 19
    model_name: str = "fake-llm-1"


class _FakeAssistantMessage:
    """Stand-in for ``AssistantMessage`` returned by the model client.

    We expose only the attributes the OTel handler reads (``content``,
    ``reasoning_content``, ``finish_reason``, ``usage_metadata``). Using
    a hand-rolled class avoids dragging in the full Pydantic model and
    its tool_call validator.
    """

    def __init__(
        self,
        content: str,
        *,
        reasoning_content: str = "",
        finish_reason: str = "stop",
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.finish_reason = finish_reason
        self.usage_metadata = _FakeUsage()


class _FakeChunk:
    """Stand-in for one streaming AssistantMessageChunk."""

    def __init__(self, content: str) -> None:
        self.content = content


@pytest.fixture
def in_memory_exporter() -> Iterator[InMemorySpanExporter]:
    """Per-test fresh exporter; tear down observability between cases."""
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="openjiuwen-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    yield exporter
    shutdown_observability()


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list[Any]:
    """Return all finished spans matching the given name."""
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _attr(span: Any, key: str, default: Any = None) -> Any:
    """Look up a span attribute, defaulting if absent."""
    attrs = dict(span.attributes or {})
    return attrs.get(key, default)


# ---------------------------------------------------------------------------
# Callback handler: LLM streaming + reasoning + TTFT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_llm_call_records_ttft_and_reasoning(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Streaming LLM produces one llm.call span with TTFT and a reasoning child."""
    fw = Runner.callback_framework
    messages = [
        {"role": "system", "content": "You are a friendly helper."},
        {"role": "user", "content": "Compute 6 * 7."},
    ]

    await fw.trigger(
        LLMCallEvents.LLM_STREAM_INPUT,
        messages=messages,
        temperature=0.5,
        top_p=0.9,
        max_tokens=512,
        model="fake-llm-1",
    )

    for delta in ("4", "2"):
        await fw.trigger(
            LLMCallEvents.LLM_STREAM_OUTPUT,
            messages=messages,
            result=_FakeChunk(content=delta),
        )

    final = _FakeAssistantMessage(
        content="42",
        reasoning_content="Six times seven equals forty-two.",
        finish_reason="stop",
    )
    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=messages,
        result=final,
    )

    llm_spans = _spans_by_name(in_memory_exporter, "llm.call")
    assert llm_spans, "no llm.call span captured"
    span = llm_spans[0]

    assert _attr(span, "gen_ai.system") == "openjiuwen"
    assert _attr(span, "gen_ai.request.model") == "fake-llm-1"
    assert _attr(span, "gen_ai.request.temperature") == 0.5
    assert _attr(span, "gen_ai.usage.prompt_tokens") == 12
    assert _attr(span, "gen_ai.usage.completion_tokens") == 7
    assert _attr(span, "gen_ai.usage.total_tokens") == 19

    ttft = _attr(span, "gen_ai.response.time_to_first_token_ms")
    assert ttft is not None and ttft >= 0.0, "TTFT must be recorded on the LLM span"

    assert "Compute 6 * 7" in _attr(span, "gen_ai.prompt.1.content", "")
    assert _attr(span, "gen_ai.completion.0.content") == "42"
    assert _attr(span, "gen_ai.response.finish_reason") == "stop"

    reasoning_spans = _spans_by_name(in_memory_exporter, "llm.reasoning")
    assert reasoning_spans, "no llm.reasoning child span emitted"
    rs = reasoning_spans[0]
    assert _attr(rs, "gen_ai.completion.0.is_reasoning") is True
    assert "forty-two" in _attr(rs, "gen_ai.completion.0.content", "")
    assert rs.parent is not None and rs.parent.span_id == span.context.span_id


# ---------------------------------------------------------------------------
# Callback handler: non-streaming + tool call nesting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_nests_under_llm_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Tool spans opened inside an LLM call become children of that LLM span."""
    fw = Runner.callback_framework
    messages = [{"role": "user", "content": "Use the calc tool."}]

    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=messages,
        model="fake-llm-1",
    )

    await fw.trigger(
        ToolCallEvents.TOOL_CALL_STARTED,
        tool_name="calc",
        tool_id="calc-1",
        inputs=((), {"expr": "6*7"}),
    )
    await fw.trigger(
        ToolCallEvents.TOOL_CALL_FINISHED,
        tool_name="calc",
        tool_id="calc-1",
        inputs=((), {"expr": "6*7"}),
        result=42,
    )

    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=messages,
        result=_FakeAssistantMessage(content="42"),
    )

    llm_spans = _spans_by_name(in_memory_exporter, "llm.call")
    tool_spans = _spans_by_name(in_memory_exporter, "tool.calc")
    assert llm_spans and tool_spans
    llm_span = llm_spans[0]
    tool_span = tool_spans[0]
    assert _attr(tool_span, "gen_ai.tool.name") == "calc"
    assert "6*7" in _attr(tool_span, "gen_ai.tool.input", "")
    assert _attr(tool_span, "gen_ai.tool.output") == "42"

    assert tool_span.parent is not None
    assert tool_span.parent.span_id == llm_span.context.span_id


# ---------------------------------------------------------------------------
# Callback handler: error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_call_error_marks_span_error(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """LLM_CALL_ERROR closes the open span with ERROR status and exception."""
    fw = Runner.callback_framework

    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=[{"role": "user", "content": "fail please"}],
        model="fake-llm-1",
    )
    await fw.trigger(
        LLMCallEvents.LLM_CALL_ERROR,
        error=RuntimeError("provider down"),
    )

    spans = _spans_by_name(in_memory_exporter, "llm.call")
    assert spans
    span = spans[0]
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR
    assert "provider down" in (span.status.description or "")


# ---------------------------------------------------------------------------
# Monitor handler: team + member + message + task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_monitor_handler_emits_team_and_task_spans(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """End-to-end Monitor handler verifies team / task / message spans."""
    config = ObservabilityConfig(enabled=True, sample_rate=1.0)
    handler = OtelTeamMonitorHandler(config)

    await handler(
        EventMessage.from_event(
            TeamCreatedEvent(
                team_name="alpha",
                display_name="Alpha Team",
                leader_member_name="leader",
                created=1700000000,
            ),
        ),
    )
    await handler(
        EventMessage.from_event(MemberSpawnedEvent(team_name="alpha", member_name="alice")),
    )
    await handler(
        EventMessage.from_event(
            MemberStatusChangedEvent(
                team_name="alpha",
                member_name="alice",
                old_status="UNSTARTED",
                new_status="READY",
            ),
        ),
    )
    await handler(
        EventMessage.from_event(
            MessageEvent(
                team_name="alpha",
                message_id="m1",
                from_member_name="leader",
                to_member_name="alice",
            ),
        ),
    )
    await handler(
        EventMessage.from_event(
            BroadcastEvent(team_name="alpha", message_id="m2", from_member_name="leader"),
        ),
    )
    await handler(
        EventMessage.from_event(
            TaskCreatedEvent(team_name="alpha", task_id="t1", status="open"),
        ),
    )
    await handler(
        EventMessage.from_event(TaskCompletedEvent(team_name="alpha", task_id="t1")),
    )
    await handler(EventMessage.from_event(TeamCleanedEvent(team_name="alpha")))

    team_spans = _spans_by_name(in_memory_exporter, "team.alpha")
    assert team_spans, "team root span missing"
    team_span = team_spans[0]
    assert _attr(team_span, "agentteam.team.name") == "alpha"
    assert _attr(team_span, "agentteam.team.display_name") == "Alpha Team"

    event_names = [e.name for e in team_span.events]
    assert "member_spawned" in event_names
    assert "member_status_changed" in event_names
    assert "message" in event_names
    assert "broadcast" in event_names

    task_spans = _spans_by_name(in_memory_exporter, "task.t1")
    assert task_spans, "task span missing"
    task_span = task_spans[0]
    assert _attr(task_span, "agentteam.task.status") == "completed"


# ---------------------------------------------------------------------------
# ObservabilityRail: task iteration spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_rail_opens_and_closes_iteration_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Rail emits one deepagent.task_iteration span per before/after pair."""
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=3, loop_event=None, is_follow_up=True)
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=inputs)

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    iter_spans = _spans_by_name(in_memory_exporter, "deepagent.task_iteration.3")
    assert iter_spans, "task_iteration span missing"
    span = iter_spans[0]
    assert _attr(span, "deepagent.task.iteration") == 3
    assert _attr(span, "deepagent.task.is_follow_up") is True


@pytest.mark.asyncio
async def test_observability_rail_marks_error_on_exception(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """When ctx.exception is set, the iteration span closes as ERROR."""
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, loop_event=None)
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=inputs)
    ctx.exception = ValueError("kaboom")

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    iter_spans = _spans_by_name(in_memory_exporter, "deepagent.task_iteration.1")
    assert iter_spans
    from opentelemetry.trace import StatusCode

    assert iter_spans[0].status.status_code == StatusCode.ERROR


# ---------------------------------------------------------------------------
# Configuration: enabled toggle and redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_config_is_a_noop() -> None:
    """init_observability(enabled=False) registers no callbacks and exports nothing."""
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(enabled=False),
        span_exporter_override=exporter,
    )
    fw = Runner.callback_framework
    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=[{"role": "user", "content": "hi"}],
        model="fake-llm-1",
    )
    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=[],
        result=_FakeAssistantMessage(content="hello"),
    )
    shutdown_observability()
    assert exporter.get_finished_spans() == ()


@pytest.mark.asyncio
async def test_redaction_replaces_prompt_and_completion_text() -> None:
    """When redaction is on, prompt/completion attributes carry sha256 prefixes."""
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            redact_prompts=True,
            redact_completions=True,
        ),
        span_exporter_override=exporter,
    )
    try:
        fw = Runner.callback_framework
        await fw.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=[{"role": "user", "content": "secret prompt"}],
            model="fake-llm-1",
        )
        await fw.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            messages=[],
            result=_FakeAssistantMessage(content="secret answer"),
        )
        spans = [s for s in exporter.get_finished_spans() if s.name == "llm.call"]
        assert spans
        prompt = _attr(spans[0], "gen_ai.prompt.0.content", "")
        completion = _attr(spans[0], "gen_ai.completion.0.content", "")
        assert prompt.startswith("sha256:") and "secret" not in prompt
        assert completion.startswith("sha256:") and "secret" not in completion
    finally:
        shutdown_observability()


# ---------------------------------------------------------------------------
# Agent invoke spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_invoke_emits_named_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """AGENT_INVOKE_INPUT/OUTPUT pair produces an agent.* span."""
    fw = Runner.callback_framework

    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
    )
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "leader", "user_input": "hello"},
        result="acknowledged",
    )

    agent_spans = [s for s in in_memory_exporter.get_finished_spans() if s.name == "agent.leader"]
    assert agent_spans, "agent.leader span missing"
    span = agent_spans[0]
    assert _attr(span, "agentteam.agent.id") == "leader"
    assert _attr(span, "agentteam.agent.input") == "hello"
    assert _attr(span, "agentteam.agent.output") == "acknowledged"
