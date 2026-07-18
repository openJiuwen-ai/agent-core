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

import json
from dataclasses import dataclass
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    ObservabilityRail,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.observability.monitor_handler import OtelTeamMonitorHandler
from openjiuwen.agent_teams.observability.semconv import (
    AT_PLAN_APPROVED,
    AT_TASK_STATUS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
)
from openjiuwen.agent_teams.schema.events import (
    BroadcastEvent,
    EventMessage,
    MemberSpawnedEvent,
    MemberStatusChangedEvent,
    MessageEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskPlanRequestEvent,
    TaskPlanResponseEvent,
    TeamCleanedEvent,
    TeamCreatedEvent,
)
from openjiuwen.agent_teams.schema.status import TaskStatus
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
    ``reasoning_content``, ``finish_reason``, ``usage_metadata``, ``tool_calls``). Using
    a hand-rolled class avoids dragging in the full Pydantic model and
    its tool_call validator.
    """

    def __init__(
        self,
        content: str,
        *,
        reasoning_content: str = "",
        finish_reason: str = "stop",
        tool_calls: list[Any] | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls
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


def _create_team_span(team_name: str) -> Any:
    """Create a team span, simulating what Runner._maybe_attach_observability does.

    v18: Team span creation moved from callback_handler to runner.
    UTs that directly trigger AGENT_INVOKE_INPUT must create the team
    span first, just like the runner does before calling agent.invoke/stream.
    """
    from openjiuwen.agent_teams.observability.span_context import get_or_create_team_span
    from openjiuwen.agent_teams.observability.setup import get_tracer
    return get_or_create_team_span(team_name, get_tracer("openjiuwen.agent_teams.observability"))


# ---------------------------------------------------------------------------
# Callback handler: LLM streaming + reasoning + TTFT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_llm_call_records_ttft_and_reasoning(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Streaming LLM produces one llm.call span with TTFT and a reasoning child."""
    fw = Runner.callback_framework

    # Team span is required as parent for LLM spans.
    _create_team_span("test_team")

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
        LLMCallEvents.LLM_OUTPUT,
        messages=messages,
        response=final,
        finish_reason="stop",
        usage=_FakeUsage(),
    )

    llm_spans = _spans_by_name(in_memory_exporter, "llm.call")
    assert llm_spans, "no llm.call span captured"
    span = llm_spans[0]

    assert _attr(span, "gen_ai.system") == "openjiuwen-test"
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
    assert "forty-two" in _attr(rs, "gen_ai.completion.0.content", "")
    assert _attr(rs, "langfuse.observation.input") == "llm reasoning"
    assert rs.parent is not None and rs.parent.span_id == span.context.span_id


# ---------------------------------------------------------------------------
# Callback handler: non-streaming + tool call nesting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_nests_under_agent_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Tool spans opened inside an agent iteration become children of the agent span."""
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    # No ContextVar setup needed

    # Create team span (simulating Runner._maybe_attach_observability)
    _create_team_span("test_team")

    fw = Runner.callback_framework

    # Step 2: Agent invoke input
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    session.get_agent_id.return_value = "leader"
    session.get_agent_name.return_value = "leader"
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "use the calc tool"},
        session=session,
    )

    # Step 3: Rail creates agent span (iteration 1)
    # v21: mock agent with team_name and card.name
    mock_agent = MagicMock()
    mock_agent.team_name = "test_team"
    mock_card = MagicMock()
    mock_card.name = "leader"
    mock_agent.card = mock_card

    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, query="use the calc tool", loop_event=None)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)
    await rail.before_task_iteration(ctx)

    # Step 3: LLM call within the iteration
    messages = [{"role": "user", "content": "Use the calc tool."}]
    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=messages,
        model="fake-llm-1",
    )

    # Step 4: Tool call within the iteration
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

    # Step 5: Rail closes agent span
    await rail.after_task_iteration(ctx)

    # Verify: tool span is child of agent span
    agent_spans = [s for s in in_memory_exporter.get_finished_spans() if s.name.startswith("agent.leader.task_iteration")]
    tool_spans = _spans_by_name(in_memory_exporter, "tool.calc")
    assert agent_spans, "agent span missing"
    assert tool_spans, "tool span missing"
    agent_span = agent_spans[0]
    tool_span = tool_spans[0]
    assert _attr(tool_span, "gen_ai.tool.name") == "calc"
    assert tool_span.parent is not None
    assert tool_span.parent.span_id == agent_span.context.span_id

    # Cleanup
    from openjiuwen.agent_teams.observability.span_context import remove_team_span
    from opentelemetry.trace import Status, StatusCode
    ts = remove_team_span("test_team")
    if ts is not None and ts.is_recording():
        ts.set_status(Status(StatusCode.OK))
        ts.end()


# ---------------------------------------------------------------------------
# Callback handler: LLM response with both content and tool_calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_response_with_content_and_tool_calls(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """When LLM returns both content and tool_calls, both should be recorded."""
    fw = Runner.callback_framework

    # Team span is required as parent for LLM spans.
    _create_team_span("test_team")

    messages = [{"role": "user", "content": "What is the weather?"}]

    # Simulate LLM response with both content and tool_calls
    tool_call = {
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "get_weather",
            "arguments": '{"location": "Beijing"}',
        },
    }

    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=messages,
        model="fake-llm-1",
    )

    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=messages,
        result=_FakeAssistantMessage(
            content="Let me check the weather for you.",
            tool_calls=[tool_call],
        ),
    )

    llm_spans = _spans_by_name(in_memory_exporter, "llm.call")
    assert llm_spans, "no llm.call span captured"
    span = llm_spans[0]

    # Both content and tool_calls should be recorded
    assert "Let me check the weather for you." in _attr(span, "gen_ai.completion.0.content", "")
    tool_calls_attr = _attr(span, "gen_ai.tool_calls", "")
    assert tool_calls_attr, "tool_calls should be recorded"
    assert "get_weather" in tool_calls_attr
    assert "Beijing" in tool_calls_attr


@pytest.mark.asyncio
async def test_llm_call_error_marks_span_error(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """LLM_CALL_ERROR closes the open span with ERROR status and exception."""
    fw = Runner.callback_framework

    # Team span is required as parent for LLM spans.
    _create_team_span("test_team")

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
    from openjiuwen.agent_teams.observability.span_context import set_team_span, remove_team_span

    config = ObservabilityConfig(enabled=True, sample_rate=1.0)
    handler = OtelTeamMonitorHandler(config)

    # v13: Team span is created by on_agent_invoke_input, not by monitor handler.
    # For this test, create it manually.
    from openjiuwen.agent_teams.observability.setup import get_tracer
    tracer = get_tracer("test")
    team_span = tracer.start_span(name="team.alpha", kind=SpanKind.SERVER)
    team_span.set_attribute("agentteam.team.name", "alpha")
    set_team_span(team_span, "alpha")

    try:
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

        # Team span is owned by Runner's finally block, not by monitor_handler.
        # Close it manually here so it appears in the exporter.
        if team_span.is_recording():
            from opentelemetry.trace import Status, StatusCode
            team_span.set_status(Status(StatusCode.OK))
            team_span.end()

        team_spans = _spans_by_name(in_memory_exporter, "team.alpha")
        assert team_spans, "team root span missing"
        team_span_result = team_spans[0]
        assert _attr(team_span_result, "agentteam.team.name") == "alpha"
        assert _attr(team_span_result, "agentteam.team.display_name") == "Alpha Team"

        member_spans = _spans_by_name(in_memory_exporter, "member.alice.spawned")
        assert member_spans, "member.alice.spawned span missing"
        msg_spans = _spans_by_name(in_memory_exporter, "msg.leader->alice")
        assert msg_spans, "msg.leader->alice span missing"
        bc_spans = _spans_by_name(in_memory_exporter, "msg.broadcast.leader")
        assert bc_spans, "msg.broadcast.leader span missing"

        task_spans = _spans_by_name(in_memory_exporter, "task.t1")
        assert task_spans, "task span missing"
        task_span = task_spans[0]
        assert _attr(task_span, "agentteam.task.status") == "completed"
    finally:
        # Cleanup team span if still recording
        if team_span.is_recording():
            from opentelemetry.trace import Status, StatusCode
            team_span.set_status(Status(StatusCode.OK))
            team_span.end()
        remove_team_span("alpha")


def _new_plan_handler(team_name: str) -> tuple[OtelTeamMonitorHandler, Any]:
    """Create a monitor handler + an open team span for plan-mode tests.

    Mirrors the team-span setup in ``test_team_monitor_handler_emits_team_and_task_spans``:
    the handler uses the shared tracer registered by ``init_observability`` so
    child spans land in the per-test ``InMemorySpanExporter``.
    """
    from openjiuwen.agent_teams.observability.setup import get_tracer
    from openjiuwen.agent_teams.observability.span_context import set_team_span
    config = ObservabilityConfig(enabled=True, sample_rate=1.0)
    handler = OtelTeamMonitorHandler(config)
    team_span = get_tracer("test").start_span(name=f"team.{team_name}", kind=SpanKind.SERVER)
    team_span.set_attribute("agentteam.team.name", team_name)
    set_team_span(team_span, team_name)
    return handler, team_span


def _close_team_span(team_span: Any) -> None:
    if team_span.is_recording():
        from opentelemetry.trace import Status, StatusCode
        team_span.set_status(Status(StatusCode.OK))
        team_span.end()


@pytest.mark.asyncio
async def test_plan_request_advances_task_status_to_planning(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Plan-mode: TaskCreated -> TaskPlanRequest advances AT_TASK_STATUS to 'planning'.

    Plan mode never publishes TaskClaimedEvent; the request event itself carries
    status=planning and must be routed through _record_task_status_span.
    """
    from openjiuwen.agent_teams.observability.monitor_handler import _TASK_EVENT_TYPES
    from openjiuwen.agent_teams.observability.span_context import remove_team_span
    from openjiuwen.agent_teams.schema.events import TeamEvent

    assert TeamEvent.TASK_PLAN_REQUEST in _TASK_EVENT_TYPES
    assert TeamEvent.TASK_PLAN_RESPONSE in _TASK_EVENT_TYPES

    team = "plan_team"
    task_id = "plan-task-1"
    handler, team_span = _new_plan_handler(team)
    try:
        await handler(EventMessage.from_event(TaskCreatedEvent(
            team_name=team, task_id=task_id, status=TaskStatus.PENDING.value,
        )))
        await handler(EventMessage.from_event(TaskPlanRequestEvent(
            team_name=team, task_id=task_id, member_name="member-1",
            status=TaskStatus.PLANNING.value, plan_id="plan-1",
            member_plan_md="/tmp/plan.md", tool_call_id="tc-1",
        )))

        task_span = handler._task_spans.get(task_id)
        assert task_span is not None, "task span missing after create + plan_request"
        assert _attr(task_span, AT_TASK_STATUS) == "planning"
        assert _attr(task_span, "agentteam.task.plan_id") == "plan-1"
    finally:
        _close_team_span(team_span)
        remove_team_span(team)


@pytest.mark.asyncio
async def test_plan_response_approved_and_rejected_paths(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Plan response: approved -> in_progress + AT_PLAN_APPROVED=True;
    rejected -> status reverts to planning + AT_PLAN_APPROVED=False."""
    from openjiuwen.agent_teams.observability.span_context import remove_team_span

    team = "plan_team"
    task_id = "plan-task-2"
    feedback = "计划正确，请按计划执行"
    handler, team_span = _new_plan_handler(team)
    try:
        await handler(EventMessage.from_event(TaskCreatedEvent(
            team_name=team, task_id=task_id, status=TaskStatus.PENDING.value)))
        await handler(EventMessage.from_event(TaskPlanRequestEvent(
            team_name=team, task_id=task_id, member_name="member-1",
            status=TaskStatus.PLANNING.value, plan_id="plan-1",
            member_plan_md="/tmp/plan.md")))

        # approved
        await handler(EventMessage.from_event(TaskPlanResponseEvent(
            team_name=team, task_id=task_id, approved=True,
            status=TaskStatus.IN_PROGRESS.value, plan_id="plan-1",
            member_name="member-1", feedback=feedback, tool_call_id="tc-1")))
        task_span = handler._task_spans.get(task_id)
        assert _attr(task_span, AT_TASK_STATUS) == "in_progress"
        assert _attr(task_span, AT_PLAN_APPROVED) is True

        # rejected: status reverts to planning
        await handler(EventMessage.from_event(TaskPlanResponseEvent(
            team_name=team, task_id=task_id, approved=False,
            status=TaskStatus.PLANNING.value, plan_id="plan-1",
            member_name="member-1", feedback="需要修改")))
        assert _attr(task_span, AT_TASK_STATUS) == "planning"
        assert _attr(task_span, AT_PLAN_APPROVED) is False
    finally:
        _close_team_span(team_span)
        remove_team_span(team)


@pytest.mark.asyncio
async def test_plan_event_span_io_split_on_semantic_boundary(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Plan event child spans split input/output on semantic boundaries:
    plan_request: input=full payload, output={plan_id, status};
    plan_response: input={plan_id}, output={approved, feedback}."""
    from openjiuwen.agent_teams.observability.span_context import remove_team_span

    team = "plan_team"
    task_id = "plan-task-3"
    feedback = "计划正确，请按计划执行"
    handler, team_span = _new_plan_handler(team)
    try:
        await handler(EventMessage.from_event(TaskCreatedEvent(
            team_name=team, task_id=task_id, status=TaskStatus.PENDING.value)))
        await handler(EventMessage.from_event(TaskPlanRequestEvent(
            team_name=team, task_id=task_id, member_name="member-1",
            status=TaskStatus.PLANNING.value, plan_id="plan-1",
            member_plan_md="/tmp/plan.md")))
        await handler(EventMessage.from_event(TaskPlanResponseEvent(
            team_name=team, task_id=task_id, approved=True,
            status=TaskStatus.IN_PROGRESS.value, plan_id="plan-1",
            member_name="member-1", feedback=feedback)))

        finished = in_memory_exporter.get_finished_spans()
        plan_req_span = next(s for s in finished if s.name == f"task.{task_id}.plan_request")
        plan_resp_span = next(s for s in finished if s.name == f"task.{task_id}.plan_response")

        req_in = json.loads(_attr(plan_req_span, LANGFUSE_OBSERVATION_INPUT))
        req_out = json.loads(_attr(plan_req_span, LANGFUSE_OBSERVATION_OUTPUT))
        assert req_in["task_id"] == task_id
        assert req_in["member_plan_md"] == "/tmp/plan.md"
        assert req_out == {"plan_id": "plan-1", "status": "planning"}
        assert _attr(plan_req_span, AT_TASK_STATUS) == "planning"

        resp_in = json.loads(_attr(plan_resp_span, LANGFUSE_OBSERVATION_INPUT))
        resp_out = json.loads(_attr(plan_resp_span, LANGFUSE_OBSERVATION_OUTPUT))
        assert resp_in == {"plan_id": "plan-1"}
        assert resp_out == {"approved": True, "feedback": feedback}
        assert _attr(plan_resp_span, AT_TASK_STATUS) == "in_progress"
    finally:
        _close_team_span(team_span)
        remove_team_span(team)


# ---------------------------------------------------------------------------
# ObservabilityRail: task iteration spans
# ---------------------------------------------------------------------------


def _create_mock_agent(team_name: str = "test_team", member_name: str = "leader") -> MagicMock:
    """Create a mock agent with team_name and card.name attributes (v21 pattern)."""
    mock_agent = MagicMock()
    mock_agent.team_name = team_name
    mock_card = MagicMock()
    mock_card.name = member_name
    mock_agent.card = mock_card
    return mock_agent


@pytest.mark.asyncio
async def test_observability_rail_opens_and_closes_iteration_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Rail emits one agent.*.task_iteration span per before/after pair."""
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )
    from openjiuwen.agent_teams.observability.span_context import remove_team_span

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    # Create team span (simulating Runner._maybe_attach_observability)
    _create_team_span("test_team")

    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    session.get_agent_id.return_value = "leader"
    session.get_agent_name.return_value = "leader"
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )

    # v21: mock agent with team_name and card.name
    mock_agent = _create_mock_agent()
    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=3, loop_event=None, is_follow_up=True)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    # v13: span name is agent.{member}.task_iteration.{n}
    iter_spans = [s for s in in_memory_exporter.get_finished_spans()
                  if s.name.startswith("agent.") and "task_iteration.3" in s.name]
    assert iter_spans, "agent.*.task_iteration.3 span missing"
    span = iter_spans[0]
    assert _attr(span, "deepagent.task.iteration") == 3
    assert _attr(span, "deepagent.task.is_follow_up") is True
    # v13: observation type is AGENT
    assert _attr(span, "langfuse.observation.type") == "agent"

    # Cleanup
    remove_team_span("test_team")


@pytest.mark.asyncio
async def test_observability_rail_marks_error_on_exception(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """When ctx.exception is set, the iteration span closes as ERROR."""
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )
    from openjiuwen.agent_teams.observability.span_context import remove_team_span

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    # Create team span (simulating Runner._maybe_attach_observability)
    _create_team_span("test_team")

    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    session.get_agent_id.return_value = "leader"
    session.get_agent_name.return_value = "leader"
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )

    # v21: mock agent with team_name and card.name
    mock_agent = _create_mock_agent()
    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, loop_event=None)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)
    ctx.exception = ValueError("kaboom")

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    # v13: span name is agent.{member}.task_iteration.{n}
    iter_spans = [s for s in in_memory_exporter.get_finished_spans()
                  if s.name.startswith("agent.") and "task_iteration.1" in s.name]
    assert iter_spans
    from opentelemetry.trace import StatusCode

    assert iter_spans[0].status.status_code == StatusCode.ERROR

    # Cleanup
    remove_team_span("test_team")


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
        # Team span is required as parent for LLM spans.
        _create_team_span("test_team")

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
async def test_agent_invoke_creates_team_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """v18: Team span is created by Runner._maybe_attach_observability (not by callback_handler).
    After _create_team_span + AGENT_INVOKE_INPUT, the team span exists with correct attributes.
    AGENT_INVOKE_OUTPUT does NOT close team span.
    Team span is closed in finalize_team_trace (called from team_runner finally)."""
    from openjiuwen.agent_teams.observability.span_context import get_team_span, remove_team_span

    # v21: team_name is read from agent.team_name, no ContextVar needed
    # v18: Runner creates team span before agent invoke
    _create_team_span("test_team")

    fw = Runner.callback_framework
    # Use a simple session without get_agent_name (AgentTeamSession doesn't have it)
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    # Ensure get_agent_name is not present (or returns None)
    del session.get_agent_name

    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )

    # v18: Team span was created by _create_team_span (simulating runner),
    # and should still exist and be recording after AGENT_INVOKE_INPUT
    team_span = get_team_span("test_team")
    assert team_span is not None, "team span should exist after _create_team_span + AGENT_INVOKE_INPUT"
    assert team_span.is_recording(), "team span should be recording"
    assert _attr(team_span, "agentteam.team.name") == "test_team"

    # v15: After invoke_output, team span should STILL be recording
    # (not closed in on_agent_invoke_output anymore)
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
        result="acknowledged",
    )

    team_span_after = get_team_span("test_team")
    assert team_span_after is not None, "v15: team span should NOT be closed in invoke_output"
    assert team_span_after.is_recording(), "v15: team span should still be recording"

    # Team span is closed in finalize_team_trace (called from team_runner finally)
    # For this test, we manually close it
    from opentelemetry.trace import Status, StatusCode
    team_span.set_status(Status(StatusCode.OK))
    team_span.end()
    remove_team_span("test_team")

    # Now the span should be in the exporter (finished)
    team_spans = _spans_by_name(in_memory_exporter, "team.test_team")
    assert team_spans, "team span should be in exporter after being closed"


# ---------------------------------------------------------------------------
# Hypothesis verification: agent span end does not affect team span visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_span_survives_after_rail_iteration(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Verify: when rail closes an agent span (iteration end),
    the team span is still visible and accessible via get_team_span().

    v13: Agent spans are created/closed by ObservabilityRail per iteration.
    Team span remains accessible across iterations.
    """
    from openjiuwen.agent_teams.observability.span_context import (
        get_team_span,
        reset_all,
        remove_team_span,
    )
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    # Reset state to ensure clean test
    reset_all()

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    # Create team span (simulating Runner._maybe_attach_observability)
    _create_team_span("test_team")

    fw = Runner.callback_framework

    # Step 1: Agent invoke input
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    session.get_agent_id.return_value = "leader"
    session.get_agent_name.return_value = "leader"

    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )

    # Verify team span was created
    team_span_before = get_team_span("test_team")
    assert team_span_before is not None, "team span should be created"
    assert team_span_before.is_recording() is True, "team span should be recording"

    # Step 2: Rail creates and closes agent span (iteration 1)
    # v21: mock agent with team_name and card.name
    mock_agent = _create_mock_agent()
    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, query="hello", loop_event=None)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)
    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    # Step 3: KEY VERIFICATION - team span is STILL visible and recording
    team_span_after = get_team_span("test_team")
    assert team_span_after is not None, "team span should STILL be accessible after iteration ends"
    assert team_span_after.is_recording() is True, "team span should STILL be recording"

    # The agent span should be in finished spans
    agent_spans_in_exporter = [s for s in in_memory_exporter.get_finished_spans()
                               if s.name.startswith("agent.leader.task_iteration")]
    assert agent_spans_in_exporter, "agent iteration span should be in exporter (was ended)"

    # Verify parent-child relationship: agent span was child of team span
    agent_span = agent_spans_in_exporter[0]
    assert agent_span.parent is not None, "agent span should have a parent"
    assert agent_span.parent.span_id == team_span_after.context.span_id, \
        "agent span's parent should be team span"

    # Cleanup: end team span manually
    from opentelemetry.trace import Status, StatusCode
    ts = remove_team_span("test_team")
    if ts is not None and ts.is_recording():
        ts.set_attribute("langfuse.observation.output", "test_cleanup")
        ts.set_status(Status(StatusCode.OK))
        ts.end()


# ---------------------------------------------------------------------------
# v14: Trace isolation and span tree shape verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_runs_produce_two_separate_traces(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """v15: Two Runner.run calls produce two independent traces.
    Each trace has its own team span as root.
    Team span is closed in finalize_team_trace (called from team_runner finally)."""
    from openjiuwen.agent_teams.observability.span_context import remove_team_span, get_team_span
    from openjiuwen.agent_teams.observability.setup import finalize_team_trace
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"

    # --- Run 1 ---
    # v18: Runner creates team span before agent invoke
    _create_team_span("test_team")
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "run 1 query"},
        session=session,
    )
    # Rail iteration
    # v21: mock agent with team_name and card.name
    mock_agent = _create_mock_agent()
    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, query="run 1 query", loop_event=None)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)
    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)
    # End run 1 (invoke_output does NOT close team span)
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "leader", "user_input": "run 1 query"},
        session=session,
        result="run 1 result",
    )

    # Verify team span is still recording after invoke_output
    assert get_team_span() is not None, "team span should still exist after invoke_output"

    # Close team span via finalize_team_trace (simulates team_runner finally)
    finalize_team_trace("test_team")

    # Verify team span is cleared after finalize
    assert get_team_span() is None, "team span ContextVar should be cleared after finalize"

    # --- Run 2 ---
    # v18: Runner creates team span before agent invoke (new span for run 2)
    _create_team_span("test_team")
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "run 2 query"},
        session=session,
    )
    # Rail iteration
    inputs2 = TaskIterationInputs(iteration=1, query="run 2 query", loop_event=None)
    ctx2 = AgentCallbackContext(agent=mock_agent, inputs=inputs2)
    await rail.before_task_iteration(ctx2)
    await rail.after_task_iteration(ctx2)
    # End run 2
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "leader", "user_input": "run 2 query"},
        session=session,
        result="run 2 result",
    )

    # Close team span via finalize_team_trace
    finalize_team_trace("test_team")

    # Verify: 2 team spans (2 traces)
    team_spans = _spans_by_name(in_memory_exporter, "team.test_team")
    assert len(team_spans) == 2, f"expected 2 team spans (2 traces), got {len(team_spans)}"

    # Verify: 2 agent spans (1 per run)
    agent_spans = [s for s in in_memory_exporter.get_finished_spans()
                   if s.name.startswith("agent.leader.task_iteration")]
    assert len(agent_spans) == 2, f"expected 2 agent spans, got {len(agent_spans)}"

    # Verify: each agent span's parent is a team span
    team_span_ids = {s.context.span_id for s in team_spans}
    for agent_span in agent_spans:
        assert agent_span.parent is not None, "agent span should have parent"
        assert agent_span.parent.span_id in team_span_ids, \
            "agent span parent should be a team span"

    remove_team_span("test_team")


@pytest.mark.asyncio
async def test_member_invoke_does_not_close_team_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """v15: Member's AGENT_INVOKE_OUTPUT does NOT close the team span.
    Team span is closed in finalize_team_trace (called from team_runner finally)."""
    from openjiuwen.agent_teams.observability.span_context import remove_team_span, get_team_span
    from openjiuwen.agent_teams.observability.setup import finalize_team_trace

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"

    # v18: Runner creates team span before agent invoke
    _create_team_span("test_team")

    # Leader invoke
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )
    assert get_team_span() is not None

    # Member invoke_output should NOT close team span
    member_session = MagicMock()
    member_session.get_session_id.return_value = "test_session"

    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "test_team_worker", "user_input": "done"},
        session=member_session,
        result="worker result",
    )

    # Team span should still be alive (member didn't close it)
    team_span = get_team_span()
    assert team_span is not None, "team span should NOT be closed by member invoke_output"
    assert team_span.is_recording(), "team span should still be recording"

    # Now close via finalize_team_trace
    finalize_team_trace("test_team")

    # Now team span should be closed
    assert get_team_span() is None, "team span should be closed after finalize_team_trace"

    remove_team_span("test_team")


@pytest.mark.asyncio
async def test_span_tree_shape(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """v15: Verify the correct span tree shape:
    team.{name} (root)
      ├── agent.leader.task_iteration.1
      │       ├── llm.call
      │       └── tool.xxx
    No duplicate team spans, no orphan spans.
    Team span is closed in finalize_team_trace."""
    from openjiuwen.agent_teams.observability.span_context import remove_team_span
    from openjiuwen.agent_teams.observability.setup import finalize_team_trace
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        TaskIterationInputs,
    )

    # v21: team_name and member_name are read from agent.team_name and agent.card.name
    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"

    # v18: Runner creates team span before agent invoke
    _create_team_span("test_team")

    # Step 1: Agent invoke input
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "use the calc tool"},
        session=session,
    )

    # Step 2: Rail creates agent span (iteration 1)
    # v21: mock agent with team_name and card.name
    mock_agent = _create_mock_agent()
    rail = ObservabilityRail()
    inputs = TaskIterationInputs(iteration=1, query="use the calc tool", loop_event=None)
    ctx = AgentCallbackContext(agent=mock_agent, inputs=inputs)
    await rail.before_task_iteration(ctx)

    # Step 3: LLM call within the iteration
    messages = [{"role": "user", "content": "Use the calc tool."}]
    await fw.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=messages,
        model="fake-llm-1",
    )

    # Step 4: Tool call within the iteration
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

    # Step 5: Rail closes agent span
    await rail.after_task_iteration(ctx)

    # Step 6: invoke_output does NOT close team span
    await fw.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "leader", "user_input": "use the calc tool"},
        session=session,
        result="42",
    )

    # Step 7: finalize_team_trace closes team span
    finalize_team_trace("test_team")

    # Verify tree shape
    all_spans = in_memory_exporter.get_finished_spans()

    # Exactly 1 team span as root
    team_spans = [s for s in all_spans if s.name == "team.test_team"]
    assert len(team_spans) == 1, f"expected exactly 1 team span, got {len(team_spans)}"
    team_span = team_spans[0]
    assert team_span.parent is None, "team span should be ROOT (no parent)"

    # Exactly 1 agent span, parent = team span
    agent_spans = [s for s in all_spans if s.name.startswith("agent.leader.task_iteration")]
    assert len(agent_spans) == 1, f"expected 1 agent span, got {len(agent_spans)}"
    agent_span = agent_spans[0]
    assert agent_span.parent is not None
    assert agent_span.parent.span_id == team_span.context.span_id, \
        "agent span parent should be team span"

    # LLM span parent = agent span
    llm_spans = [s for s in all_spans if s.name == "llm.call"]
    assert llm_spans, "llm.call span should exist"
    llm_span = llm_spans[0]
    assert llm_span.parent is not None
    assert llm_span.parent.span_id == agent_span.context.span_id, \
        "llm span parent should be agent span"

    # Tool span parent = agent span
    tool_spans = [s for s in all_spans if s.name == "tool.calc"]
    assert tool_spans, "tool.calc span should exist"
    tool_span = tool_spans[0]
    assert tool_span.parent is not None
    assert tool_span.parent.span_id == agent_span.context.span_id, \
        "tool span parent should be agent span"

    # No orphan spans (every non-root span's parent exists)
    span_ids = {s.context.span_id for s in all_spans}
    for s in all_spans:
        if s.parent is not None:
            assert s.parent.span_id in span_ids, \
                f"orphan span: {s.name} parent {s.parent.span_id} not found"

    remove_team_span("test_team")


@pytest.mark.asyncio
async def test_team_span_uses_agent_team_name(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """v21: Team span name comes from agent.team_name attribute.

    The team span is created by Runner._maybe_attach_observability with the
    correct team_name, and Rail reads team_name from agent.team_name.
    """
    from openjiuwen.agent_teams.observability.span_context import get_team_span, remove_team_span

    # v21: team_name is read from agent.team_name, no ContextVar needed
    real_team_name = "my_custom_team"

    # v18: Runner creates team span before agent invoke
    _create_team_span(real_team_name)

    fw = Runner.callback_framework
    session = MagicMock()
    session.get_session_id.return_value = "test_session"
    session.get_team_id.return_value = "agent_team"  # default value (ignored)
    del session.get_agent_name

    await fw.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"agent_id": "leader", "user_input": "hello"},
        session=session,
    )

    # Team span should use the team_name passed to _create_team_span
    team_span = get_team_span(real_team_name)
    assert team_span is not None, "team span should be created"
    assert team_span.is_recording(), "team span should be recording"
    assert _attr(team_span, "agentteam.team.name") == real_team_name, \
        f"team span name should be '{real_team_name}', not 'agent_team'"

    # Verify trace name comes from span name (Langfuse auto-maps root span name)
    assert team_span.name == f"team.{real_team_name}", \
        f"span name should be 'team.{real_team_name}'"
    tags = _attr(team_span, "langfuse.trace.tags")
    assert tags is not None
    assert real_team_name in tags, f"tags should contain '{real_team_name}'"

    # Cleanup
    from opentelemetry.trace import Status, StatusCode
    team_span.set_status(Status(StatusCode.OK))
    team_span.end()
    remove_team_span(real_team_name)

    # Verify exported span has correct team name
    team_spans = _spans_by_name(in_memory_exporter, f"team.{real_team_name}")
    assert team_spans, f"team span 'team.{real_team_name}' should be in exporter"
    assert _attr(team_spans[0], "agentteam.team.name") == real_team_name
