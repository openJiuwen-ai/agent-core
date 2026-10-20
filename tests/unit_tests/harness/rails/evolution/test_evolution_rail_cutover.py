"""Canonical processor-backed EvolutionRail regression tests."""

from __future__ import annotations

import asyncio
import threading
import time
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


class _PlainRail(EvolutionRail):
    def __init__(self, processor: TrajectorySpanProcessor) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.NONE,
            trajectory_span_processor=processor,
        )


def _count_projections(rail: EvolutionRail) -> list[None]:
    calls: list[None] = []
    original = rail._project_window

    def counting(capture):
        calls.append(None)
        return original(capture)

    rail._project_window = counting
    return calls


class _ToolFreeHookRail(_HookRail):
    _trajectory_free_hooks = frozenset({EvolutionTriggerPoint.AFTER_TOOL_CALL})

    async def _on_after_tool_call(self, ctx, trajectory):
        self.hooks.append(("tool", trajectory))


@pytest.mark.asyncio
async def test_model_and_tool_calls_skip_projection_without_a_consumer() -> None:
    processor = TrajectorySpanProcessor()
    rail = _PlainRail(processor)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(_ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={})))
    processor.on_end(_span("tool.read", 2))
    await rail.after_tool_call(
        _ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
        )
    )

    assert projections == []
    trajectory = rail.get_trajectory(session_id="s", member_id="card-id")
    assert trajectory is not None
    assert [span["name"] for span in iter_spans(trajectory)] == ["llm.call", "tool.read"]


@pytest.mark.asyncio
async def test_model_call_projects_once_for_an_overridden_hook() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(_ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={})))

    assert len(projections) == 1
    name, trajectory = rail.hooks[-1]
    assert name == "model"
    assert [span["name"] for span in iter_spans(trajectory)] == ["llm.call"]


@pytest.mark.asyncio
async def test_trajectory_free_hook_receives_none_without_projection() -> None:
    processor = TrajectorySpanProcessor()
    rail = _ToolFreeHookRail(processor)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("tool.read", 1))
    await rail.after_tool_call(
        _ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
        )
    )

    assert projections == []
    assert rail.hooks[-1] == ("tool", None)
    assert rail.get_trajectory(session_id="s", member_id="card-id") is not None


@pytest.mark.asyncio
async def test_trajectory_free_marker_does_not_leak_into_subclass_overrides() -> None:
    class _ReadingChild(_ToolFreeHookRail):
        async def _on_after_tool_call(self, ctx, trajectory):
            self.hooks.append(("child-tool", trajectory))

    class _InheritingChild(_ToolFreeHookRail):
        pass

    tool_ctx = _ctx(
        AgentCallbackEvent.AFTER_TOOL_CALL,
        ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
    )
    reading = _ReadingChild(TrajectorySpanProcessor())
    reading_projections = _count_projections(reading)
    await reading.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    reading._trajectory_span_processor.on_end(_span("tool.read", 1))
    await reading.after_tool_call(tool_ctx)

    inheriting = _InheritingChild(TrajectorySpanProcessor())
    inheriting_projections = _count_projections(inheriting)
    await inheriting.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    inheriting._trajectory_span_processor.on_end(_span("tool.read", 1))
    await inheriting.after_tool_call(tool_ctx)

    assert len(reading_projections) == 1
    name, trajectory = reading.hooks[-1]
    assert name == "child-tool"
    assert [span["name"] for span in iter_spans(trajectory)] == ["tool.read"]
    assert inheriting_projections == []
    assert inheriting.hooks[-1] == ("tool", None)


def _hook_needs(cls: type, point: EvolutionTriggerPoint, trigger=EvolutionTriggerPoint.NONE) -> bool:
    rail = object.__new__(cls)
    rail._evolution_trigger = trigger
    return rail._hook_needs_trajectory(point)


def test_shipped_rails_declare_projection_consumers() -> None:
    from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail
    from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import TeamSkillEvolutionRail
    from openjiuwen.harness.rails.evolution.ttse_rail import TTSERail
    from openjiuwen.harness.rails.skills.skill_create_rail import SkillCreateRail

    class _ToolReadingSkillRail(SkillEvolutionRail):
        async def _on_after_tool_call(self, ctx, trajectory):
            del ctx, trajectory

    points = EvolutionTriggerPoint
    assert not _hook_needs(SkillEvolutionRail, points.AFTER_MODEL_CALL)
    assert not _hook_needs(SkillEvolutionRail, points.AFTER_TOOL_CALL)
    assert not _hook_needs(SkillEvolutionRail, points.AFTER_TASK_ITERATION)
    assert not _hook_needs(SkillEvolutionRail, points.AFTER_INVOKE)
    assert _hook_needs(SkillEvolutionRail, points.AFTER_INVOKE, trigger=points.AFTER_INVOKE)
    assert _hook_needs(SkillEvolutionRail, points.AFTER_TOOL_CALL, trigger=points.AFTER_TOOL_CALL)
    assert _hook_needs(_ToolReadingSkillRail, points.AFTER_TOOL_CALL)
    assert not _hook_needs(_ToolReadingSkillRail, points.AFTER_TASK_ITERATION)
    assert not _hook_needs(TeamSkillEvolutionRail, points.AFTER_TOOL_CALL)
    assert not _hook_needs(TeamSkillEvolutionRail, points.AFTER_TASK_ITERATION)
    assert not _hook_needs(TTSERail, points.AFTER_TASK_ITERATION)
    assert _hook_needs(TTSERail, points.AFTER_INVOKE)
    assert not _hook_needs(TrajectoryRail, points.AFTER_INVOKE)
    assert _hook_needs(SkillCreateRail, points.AFTER_TASK_ITERATION)


@pytest.mark.asyncio
async def test_iteration_and_invoke_skip_projection_without_a_consumer() -> None:
    processor = TrajectorySpanProcessor()
    rail = _PlainRail(processor)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("llm.call", 1))
    await rail.after_task_iteration(
        _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, InvokeInputs(query="q", conversation_id="s"))
    )
    processor.on_end(_span("tool.read", 2))
    await rail.after_invoke(_ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    assert projections == []
    trajectory = rail.get_trajectory(session_id="s", member_id="card-id")
    assert trajectory is not None
    assert [span["name"] for span in iter_spans(trajectory)] == ["llm.call", "tool.read"]


@pytest.mark.asyncio
async def test_after_invoke_trigger_still_projects_the_final_window() -> None:
    class _InvokeTriggerRail(EvolutionRail):
        def __init__(self, processor: TrajectorySpanProcessor) -> None:
            super().__init__(
                evolution_trigger=EvolutionTriggerPoint.AFTER_INVOKE,
                trajectory_span_processor=processor,
            )
            self.triggered: list[object] = []

        def _allow_evolution_trigger(self, trigger_point, ctx) -> bool:
            return True

        async def _trigger_evolution(self, trajectory, ctx) -> None:
            self.triggered.append(trajectory)

    processor = TrajectorySpanProcessor()
    rail = _InvokeTriggerRail(processor)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("llm.call", 1))
    await rail.after_task_iteration(
        _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, InvokeInputs(query="q", conversation_id="s"))
    )
    await rail.after_invoke(_ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    assert len(projections) == 1
    assert len(rail.triggered) == 1
    assert [span["name"] for span in iter_spans(rail.triggered[0])] == ["llm.call"]


@pytest.mark.asyncio
async def test_trajectory_rail_archives_without_projecting_the_clean_window() -> None:
    processor = TrajectorySpanProcessor()
    store = InMemoryTrajectoryStore()
    rail = TrajectoryRail(trajectory_span_processor=processor, trajectory_store=store)
    projections = _count_projections(rail)
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    processor.on_end(_span("llm.call", 1))
    await rail.after_task_iteration(
        _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, InvokeInputs(query="q", conversation_id="s"))
    )
    await rail.after_invoke(_ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    assert projections == []
    archives = store.query(session_id="s")
    assert len(archives) == 1
    assert [span["name"] for span in iter_spans(archives[0])] == ["llm.call"]


@pytest.mark.asyncio
async def test_event_loop_keeps_running_while_a_projection_holds_the_gil() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    original = rail._project_window

    def gil_bound_projection(capture):
        deadline = time.perf_counter() + 0.3
        while time.perf_counter() < deadline:
            sum(range(1000))
        return original(capture)

    rail._project_window = gil_bound_projection
    await rail.before_invoke(_ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s")))
    processor.on_end(_span("llm.call", 1))

    ticks: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        while not done.is_set():
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(ticker())
    try:
        await rail.after_model_call(
            _ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={}))
        )
    finally:
        done.set()
        await ticker_task

    # A blocked loop would tick once and show a single gap of >= 0.3s.
    gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:])]
    assert len(ticks) >= 3
    assert max(gaps) < 0.15


@pytest.mark.asyncio
async def test_hook_draining_and_merging_run_off_the_event_loop() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    loop_thread = threading.get_ident()
    threads: list[int] = []
    drain = rail._drain_for_hook
    merge = rail._merge_clean_increment

    def recording_drain(*args, **kwargs):
        threads.append(threading.get_ident())
        return drain(*args, **kwargs)

    def recording_merge(*args, **kwargs):
        threads.append(threading.get_ident())
        return merge(*args, **kwargs)

    rail._drain_for_hook = recording_drain
    rail._merge_clean_increment = recording_merge
    invoke_ctx = _ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s"))
    await rail.before_invoke(invoke_ctx)

    processor.on_end(_span("llm.call", 1))
    await rail.after_model_call(_ctx(AgentCallbackEvent.AFTER_MODEL_CALL, ModelCallInputs(messages=[], response={})))
    processor.on_end(_span("tool.read", 2))
    await rail.after_tool_call(
        _ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
        )
    )
    await rail.after_task_iteration(
        _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, InvokeInputs(query="q", conversation_id="s"))
    )
    await rail.after_invoke(_ctx(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query="q", conversation_id="s")))

    assert threads
    assert loop_thread not in threads
    assert len(list(iter_spans(rail.hooks[-1][1]))) == 2


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
