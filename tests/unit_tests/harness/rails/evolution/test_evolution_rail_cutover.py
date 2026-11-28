"""Canonical processor-backed EvolutionRail regression tests."""

from __future__ import annotations

import asyncio
from contextvars import Context
from copy import deepcopy
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
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


def _span(name: str, span_id: int, *, end_time: int | None = None, scope_name: str = "test") -> ReadableSpan:
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
        instrumentation_scope=InstrumentationScope(scope_name),
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


@pytest.mark.asyncio
async def test_clean_window_preserves_scopes_across_increments() -> None:
    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    ctx = _ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s"))
    await rail.before_invoke(ctx)
    try:
        for span_id, scope in ((1, "native"), (2, "bridge"), (3, "native")):
            processor.on_end(_span("tool.read", span_id, scope_name=scope))
            await rail.after_tool_call(
                _ctx(
                    AgentCallbackEvent.AFTER_TOOL_CALL,
                    ToolCallInputs(tool_name="read", tool_args={}, tool_result="ok"),
                )
            )
        trajectory = rail.get_trajectory(session_id="s", member_id="card-id")
        assert trajectory is not None
        scopes = trajectory.to_otlp()["resourceSpans"][0]["scopeSpans"]
        assert [(group["scope"]["name"], [span["spanId"] for span in group["spans"]]) for group in scopes] == [
            ("native", [f"{1:016x}", f"{3:016x}"]),
            ("bridge", [f"{2:016x}"]),
        ]
    finally:
        await rail.after_invoke(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["iteration", "model", "tool"])
async def test_rail_normalizes_only_the_new_span_at_the_capture_hooks(monkeypatch, hook_name) -> None:
    from openjiuwen.agent_evolving.trajectory import spans

    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    inputs = InvokeInputs(query="q", conversation_id="s")
    invoke = _ctx(AgentCallbackEvent.BEFORE_INVOKE, inputs)
    iteration = _ctx(AgentCallbackEvent.AFTER_TASK_ITERATION, inputs)
    await rail.before_invoke(invoke)
    for span_id in range(1, 201):
        processor.on_end(_span("llm.call", span_id))
    await rail.after_task_iteration(iteration)
    old_snapshot = rail.hooks[-1][1]
    old_payload = old_snapshot.to_otlp()

    seen = []
    normalize = spans.normalize_span

    def track(span):
        seen.append(spans.span_identity(span))
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", track)
    if hook_name == "model":
        processor.on_end(_span("llm.call", 201))
        await rail.after_model_call(
            _ctx(
                AgentCallbackEvent.AFTER_MODEL_CALL,
                ModelCallInputs(messages=[], response={"prompt_token_ids": [7]}),
            )
        )
    elif hook_name == "tool":
        processor.on_end(_span("tool.read", 201))
        await rail.after_tool_call(
            _ctx(AgentCallbackEvent.AFTER_TOOL_CALL, ToolCallInputs(tool_name="read", tool_result="ok"))
        )
    else:
        processor.on_end(_span("llm.call", 201))
        await rail.after_task_iteration(iteration)

    assert seen == [(f"{1:032x}", f"{201:016x}")]
    projected = rail.hooks[-1][1]
    payload = projected.to_otlp()
    assert [span["spanId"] for span in spans.iter_payload_spans(payload)] == [
        f"{span_id:016x}" for span_id in range(2, 202)
    ]
    if hook_name == "model":
        latest = list(spans.iter_payload_spans(payload))[-1]
        assert spans.read_rl_fields(latest) == {"prompt_token_ids": [7]}

    processor.on_end(_span("llm.call", 201))
    seen.clear()
    await rail.after_task_iteration(iteration)
    assert seen == []
    assert list(spans.iter_payload_spans(rail.hooks[-1][1].to_otlp())) == list(spans.iter_payload_spans(payload))

    rail._reset_current_scope()
    assert rail.get_trajectory(session_id="s", member_id="card-id") is None
    assert old_snapshot.to_otlp() == old_payload
    await rail.after_invoke(invoke)


@pytest.mark.asyncio
async def test_same_scope_update_is_atomic_on_failure_and_serializes_writers(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from openjiuwen.agent_evolving.trajectory import spans

    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    invoke = _ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s"))
    await rail.before_invoke(invoke)
    capture = rail._current_capture()
    assert capture is not None
    for span_id in (1, 2, 3):
        processor.on_end(_span("llm.call", span_id))
    first_batch, issues = processor.drain(capture.subscription)
    assert first_batch is not None and not issues
    processor.on_end(_span("llm.call", 4))
    second_batch, issues = processor.drain(capture.subscription)
    assert second_batch is not None and not issues

    # Seed one old span, then fail after one new identity has been staged.
    seed = rail._merge_clean_increment(capture, second_batch)
    original_window = rail._scope_windows[capture.scope_key]
    original_index = original_window._identities
    normalize = spans.normalize_span

    def fail_mid_batch(span):
        if span["spanId"] == f"{2:016x}":
            raise ValueError("injected normalization failure")
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", fail_mid_batch)
    with pytest.raises(ValueError, match="injected normalization failure"):
        rail._merge_clean_increment(capture, first_batch)
    assert rail._scope_windows[capture.scope_key] is original_window
    assert original_window._identities == original_index
    old = rail.get_trajectory(session_id="s", member_id="card-id")
    assert old is not None
    assert list(spans.iter_payload_spans(old.to_otlp())) == list(spans.iter_payload_spans(seed.to_otlp()))

    monkeypatch.setattr(spans, "normalize_span", normalize)
    for span_id in (3, 5):
        processor.on_end(_span("llm.call", span_id))
    overlapping_batch, issues = processor.drain(capture.subscription)
    assert overlapping_batch is not None and not issues

    with ThreadPoolExecutor(max_workers=2) as workers:
        retry = workers.submit(rail._merge_clean_increment, capture, first_batch)
        overlap = workers.submit(rail._merge_clean_increment, capture, overlapping_batch)
        retry_snapshot = retry.result(timeout=5)
        assert {f"{span_id:016x}" for span_id in range(1, 5)} <= {
            span["spanId"] for span in spans.iter_payload_spans(retry_snapshot.to_otlp())
        }
        overlap.result(timeout=5)

    combined = rail.get_trajectory(session_id="s", member_id="card-id")
    assert combined is not None
    assert [span["spanId"] for span in spans.iter_payload_spans(combined.to_otlp())] == [
        f"{span_id:016x}" for span_id in range(1, 6)
    ]
    expected_index = {spans.span_identity(span) for span in spans.iter_payload_spans(combined.to_otlp())}
    assert rail._scope_windows[capture.scope_key]._identities == expected_index

    rail._reset_current_scope()
    reset = rail._merge_clean_increment(capture, first_batch)
    assert list(spans.iter_payload_spans(reset.to_otlp())) == list(spans.iter_payload_spans(first_batch.to_otlp()))
    rail.uninit(invoke.agent)
    assert rail.get_trajectory(session_id="s", member_id="card-id") is None
    assert len(list(spans.iter_payload_spans(seed.to_otlp()))) == 1


@pytest.mark.asyncio
async def test_uninit_during_update_cannot_publish_stale_index(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from openjiuwen.agent_evolving.trajectory import spans

    processor = TrajectorySpanProcessor()
    rail = _HookRail(processor)
    invoke = _ctx(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query="q", conversation_id="s"))
    await rail.before_invoke(invoke)
    capture = rail._current_capture()
    assert capture is not None

    processor.on_end(_span("llm.call", 1))
    await rail.after_task_iteration(invoke)
    processor.on_end(_span("llm.call", 2))
    stale_batch, issues = processor.drain(capture.subscription)
    assert stale_batch is not None and not issues

    entered = Event()
    resume = Event()
    normalize = spans.normalize_span

    def pause_update(span):
        if span["spanId"] == f"{2:016x}":
            entered.set()
            assert resume.wait(5)
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", pause_update)
    with ThreadPoolExecutor(max_workers=1) as worker:
        old_update = worker.submit(rail._merge_clean_increment, capture, stale_batch)
        try:
            assert entered.wait(5)
            rail.uninit(invoke.agent)
            await rail.before_invoke(invoke)
            processor.on_end(_span("llm.call", 3))
            await rail.after_task_iteration(invoke)
        finally:
            resume.set()
        old_update.result(timeout=5)

    current = rail.get_trajectory(session_id="s", member_id="card-id")
    assert current is not None
    assert [span["spanId"] for span in spans.iter_payload_spans(current.to_otlp())] == [f"{3:016x}"]
    assert rail._scope_windows[capture.scope_key]._identities == {(f"{1:032x}", f"{3:016x}")}
    await rail.after_invoke(invoke)
