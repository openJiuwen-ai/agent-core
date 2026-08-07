"""Canonical processor-backed EvolutionRail regression tests."""

from __future__ import annotations

import asyncio
from contextvars import Context
from copy import deepcopy
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import iter_spans, span_attributes
from openjiuwen.agent_evolving.trajectory.store import InMemoryTrajectoryStore
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail


def _span(name: str, span_id: int, *, end_time: int | None = None) -> ReadableSpan:
    end = span_id if end_time is None else end_time
    context = SpanContext(
        trace_id=1,
        span_id=span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    return ReadableSpan(
        name=name,
        context=context,
        resource=Resource.create({"openjiuwen.session_id": "producer"}),
        kind=SpanKind.INTERNAL,
        attributes={},
        status=Status(StatusCode.OK),
        start_time=end - 1,
        end_time=end,
    )


class _Agent:
    card = SimpleNamespace(id="card-id")


def _ctx(event: AgentCallbackEvent, inputs: object, *, session: object | None = None) -> AgentCallbackContext:
    return AgentCallbackContext(agent=_Agent(), event=event, inputs=inputs, session=session)


class _HookRail(EvolutionRail):
    def __init__(self, processor: TrajectorySpanProcessor) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.NONE,
            trajectory_span_processor=processor,
        )
        self.hooks: list[tuple[str, object]] = []

    async def _on_after_model_call(self, ctx, trajectory):
        self.hooks.append(("model", trajectory))

    async def _on_after_tool_call(self, ctx, trajectory):
        self.hooks.append(("tool", trajectory))

    async def _on_after_task_iteration(self, ctx, trajectory):
        self.hooks.append(("iteration", trajectory))

    async def _on_after_invoke(self, ctx, trajectory):
        self.hooks.append(("invoke", trajectory))


@pytest.mark.asyncio
async def test_after_hooks_drain_and_receive_clean_scope_view() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    await rail.after_task_iteration(
        _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, InvokeInputs(query="q", conversation_id="s"))
    )
    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(
        _ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(messages=[{"role": "user", "content": "hi"}], response={}),
        )
    )
    processor.on_end(_span("tool.read", 2))
    await rail.after_tool_call(
        _ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
        )
    )
    await rail.after_invoke(_ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    assert rail.hooks[0] == ("iteration", None)
    assert [name for name, _ in rail.hooks[1:]] == ["model", "tool", "invoke"]
    assert len(list(iter_spans(rail.hooks[-1][1]))) == 2
    assert rail.get_trajectory(session_id="s", member_id="card-id") is not None


@pytest.mark.asyncio
async def test_rl_enrichment_is_immutable_and_targets_latest_llm_span() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    processor.on_end(_span("llm.call", 1))
    response = {
        "role": "assistant",
        "content": "done",
        "prompt_token_ids": [1, 2],
        "completion_token_ids": [3],
        "logprobs": [-0.2],
    }
    original = deepcopy(response)
    await rail.after_model_call(
        _ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(messages=[], response=response),
        )
    )

    assert response == original
    trajectory = rail.get_trajectory(session_id="s", member_id="card-id")
    assert trajectory is not None
    attrs = span_attributes(next(iter(iter_spans(trajectory))))
    assert attrs["evolution.rl.prompt_token_ids"] == [1, 2]
    assert attrs["evolution.rl.completion_token_ids"] == [3]
    assert attrs["evolution.rl.logprobs"] == [-0.2]


@pytest.mark.asyncio
async def test_missing_session_id_is_rejected_and_capture_error_fails_closed() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    with pytest.raises(ValueError, match="session_id"):
        await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q")))

    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    processor.on_end(SimpleNamespace(name="llm.call", context=SimpleNamespace(trace_id=None, span_id=None)))
    await rail.after_model_call(_ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={})))
    assert rail.hooks[-1] == ("model", None)


@pytest.mark.asyncio
async def test_session_agent_id_wins_over_card_id_for_scope() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    session = SimpleNamespace(get_session_id=lambda: "s", get_agent_id=lambda: "session-agent")
    await rail.before_invoke(
        _ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="ignored"), session=session)
    )
    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(
        _ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={}), session=session)
    )
    assert rail.get_trajectory(session_id="s", member_id="session-agent") is not None
    assert rail.get_trajectory(session_id="s", member_id="card-id") is None


@pytest.mark.asyncio
async def test_trajectory_rail_archives_one_execution_at_most_once() -> None:
    processor = TrajectorySpanProcessor()
    store = InMemoryTrajectoryStore()
    rail = TrajectoryRail(trajectory_span_processor=processor, trajectory_store=store)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(_ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={})))
    end_ctx = _ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s"))
    await rail.after_invoke(end_ctx)
    await rail.after_invoke(end_ctx)

    archives = store.query(session_id="s")
    assert len(archives) == 1
    assert len(list(iter_spans(archives[0]))) == 1


@pytest.mark.asyncio
async def test_agent_root_trace_routes_spans_ended_outside_subscription_context(
) -> None:
    """A single-agent root trace must bridge callbacks in a detached task context."""
    from openjiuwen.extensions.observability import span_context

    processor = TrajectorySpanProcessor()
    store = InMemoryTrajectoryStore()
    rail = TrajectoryRail(
        trajectory_span_processor=processor,
        trajectory_store=store,
    )
    root = SimpleNamespace(
        name="agent.agent.session-a",
        context=SimpleNamespace(trace_id=1),
        is_recording=lambda: True,
    )
    span_context.set_root_span(root, session_id="session-a")
    ctx = _ctx(
        AgentCallbackEvent.BEFORE_INVOKE,
        InvokeInputs(query="q", conversation_id="session-a"),
    )

    await rail.before_invoke(ctx)
    Context().run(processor.on_end, _span("llm.call", 1))
    await rail.after_invoke(ctx)

    archives = store.query(session_id="session-a")
    assert len(archives) == 1
    assert [span["name"] for span in iter_spans(archives[0])] == ["llm.call"]
    span_context.clear_root_span(expected_span=root)


@pytest.mark.asyncio
async def test_agent_callbacks_resolve_invoke_capture_across_task_contexts() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    session = SimpleNamespace(
        get_session_id=lambda: "session-a",
        get_agent_id=lambda: "agent-a",
    )
    invoke_ctx = _ctx(
        AgentCallbackEvent.BEFORE_INVOKE,
        InvokeInputs(query="q", conversation_id="session-a"),
        session=session,
    )
    await rail.before_invoke(invoke_ctx)
    processor.on_end(_span("llm.call", 1))

    model_ctx = _ctx(
        AgentCallbackEvent.AFTER_MODEL_CALL,
        ModelCallInputs(messages=[], response={}),
        session=session,
    )
    assert Context().run(rail._current_capture) is None
    task = Context().run(asyncio.create_task, rail.after_model_call(model_ctx))
    await task

    trajectory = rail.get_trajectory(session_id="session-a", member_id="agent-a")
    assert trajectory is not None
    assert [span["name"] for span in iter_spans(trajectory)] == ["llm.call"]
    assert (
        rail._resolve_capture(
            session_id="other-session",
            member_id="agent-a",
        )
        is None
    )

    task = Context().run(asyncio.create_task, rail.after_invoke(invoke_ctx))
    await task
    assert not rail._active_captures
    assert (
        rail._resolve_capture(
            session_id="session-a",
            member_id="agent-a",
        )
        is None
    )
