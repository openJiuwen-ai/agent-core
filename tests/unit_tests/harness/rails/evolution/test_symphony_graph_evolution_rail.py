from __future__ import annotations

import asyncio
import json
import threading
from contextvars import Context
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

import openjiuwen.harness.rails.evolution.evolution_rail as evolution_rail_module
import openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail as rail_module
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs, ToolCallInputs
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability import span_context as shared_span_context
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.harness.observability.rail import AgentObservabilityRail
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import SymphonyExecutionFragment
from openjiuwen.harness.rails.evolution.symphony_execution_graph import CapabilityIdentity
from openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail import (
    SymphonyGraphEvolutionInput,
    SymphonyGraphEvolutionRail,
    TeamSymphonyGraphEvolutionRail,
)


def _span(
    name: str,
    span_id: int,
    *,
    trace_id: int = 1,
    parent_span_id: int | None = None,
    attributes: dict | None = None,
) -> ReadableSpan:
    parent = None
    if parent_span_id is not None:
        parent = SpanContext(
            trace_id=trace_id,
            span_id=parent_span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
    return ReadableSpan(
        name=name,
        context=SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        ),
        parent=parent,
        resource=Resource.create({"producer": "test"}),
        kind=SpanKind.INTERNAL,
        attributes=attributes or {},
        status=Status(StatusCode.OK),
        start_time=span_id,
        end_time=span_id + 1,
    )


def _trajectory() -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: "trajectory-1", SESSION_ID: "session-1"})
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": []}],
                }
            ]
        }
    )


def _ctx(
    *,
    session_id: str = "session-1",
    member_id: str = "member-1",
    result: dict | None = None,
) -> AgentCallbackContext:
    session = SimpleNamespace(
        get_session_id=lambda: session_id,
        get_agent_id=lambda: member_id,
    )
    return AgentCallbackContext(
        agent=SimpleNamespace(card=SimpleNamespace(id=member_id)),
        inputs=InvokeInputs(
            query="run",
            conversation_id=session_id,
            result=result or {"result_type": "answer", "output": "done"},
        ),
        session=session,
    )


def _tool_ctx(
    invoke_ctx: AgentCallbackContext,
    result: object,
    name: str = "symphony_compose_graph",
    *,
    call_id: str | None = None,
):
    return AgentCallbackContext(
        agent=invoke_ctx.agent,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id=call_id) if call_id is not None else None,
            tool_name=name,
            tool_result=result,
        ),
        session=invoke_ctx.session,
    )


def _ready_graph(graph_id: str) -> dict:
    return {
        "graph": {
            "id": graph_id,
            "type": "planned_graph",
            "directed": True,
            "metadata": {"status": "ready"},
            "nodes": {},
            "edges": [],
        }
    }


def _root(trace_id: int, team: str = "team-1", *, recording: bool = True):
    return SimpleNamespace(
        name="agent.test",
        context=SimpleNamespace(trace_id=trace_id),
        attributes={semconv.AT_TEAM_NAME: team},
        is_recording=lambda: recording,
    )


async def _prepare(
    rail: SymphonyGraphEvolutionRail,
    ctx: AgentCallbackContext,
    *,
    span_id: int = 1,
    trace_id: int = 1,
) -> SymphonyGraphEvolutionInput:
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", span_id, trace_id=trace_id))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    return prepared


@pytest.mark.asyncio
async def test_input_is_frozen_and_invoke_start_freezes_model_depth_and_snapshot() -> None:
    model_a = SimpleNamespace(invoke=AsyncMock())
    model_b = SimpleNamespace(invoke=AsyncMock())
    identity = CapabilityIdentity("skill:a", "skill", "a", "v1", "sha256:a", ("in",), ("out",))
    provider = SimpleNamespace(snapshot_capabilities=lambda: [identity])
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        capability_snapshot_provider=provider,
        edge_evaluator_llm=model_a,
        edge_search_max_depth=7,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.update_edge_evaluator_llm(model_b)
    rail._edge_search_max_depth = 1
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert prepared.edge_evaluator_llm is model_a
    assert prepared.edge_search_max_depth == 7
    assert prepared.capability_snapshot == (identity,)
    with pytest.raises(FrozenInstanceError):
        prepared.query = "changed"  # type: ignore[misc]
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


def test_constructor_rejects_bool_depth_and_clamps_negative_depth() -> None:
    with pytest.raises(TypeError):
        SymphonyGraphEvolutionRail(
            trajectory_span_processor=TrajectorySpanProcessor(),
            edge_search_max_depth=True,
        )
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        edge_search_max_depth=-2,
    )
    assert rail._edge_search_max_depth == 0


def test_constructor_validates_trajectory_history_limit() -> None:
    with pytest.raises(ValueError, match="max_trajectory_spans"):
        SymphonyGraphEvolutionRail(
            trajectory_span_processor=TrajectorySpanProcessor(),
            max_trajectory_spans=0,
        )


@pytest.mark.asyncio
async def test_first_ready_planned_graph_wins_and_is_detached() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    invalid = _ready_graph("invalid")
    del invalid["graph"]["id"]
    await rail._on_after_tool_call(_tool_ctx(ctx, {"success": True, "planned_graph": invalid}), None)
    first = _ready_graph("first")
    await rail._on_after_tool_call(_tool_ctx(ctx, {"success": True, "planned_graph": first}), None)
    first["graph"]["id"] = "mutated"
    await rail._on_after_tool_call(_tool_ctx(ctx, {"success": True, "planned_graph": _ready_graph("second")}), None)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert prepared.planned_graph["graph"]["id"] == "first"  # type: ignore[index]
    assert prepared.quality_flags == ("planned_graph_invalid",)
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_legacy_literal_payload_is_accepted_but_malformed_splits_continuity() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    rail.trajectory_span_processor.on_end(
        _span(
            "tool.call",
            2,
            attributes={semconv.GEN_AI_TOOL_OUTPUT: "{'result': ['safe']}"},
        )
    )
    _, _, issues = rail._drain_for_hook(ctx, required_category="tool")
    assert not issues
    rail.trajectory_span_processor.on_end(
        _span("tool.call", 3, attributes={semconv.GEN_AI_TOOL_OUTPUT: "{'broken': ]"})
    )
    _, _, issues = rail._drain_for_hook(ctx, required_category="tool")
    assert {issue["code"] for issue in issues} == {"tool_payload_json_error"}
    rail.trajectory_span_processor.on_end(_span("llm.call", 4))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert [index for index, _ in prepared.execution_continuities] == [0, 1]
    assert "tool_payload_json_error" in prepared.quality_flags
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_framework_tool_without_span_does_not_split_continuity() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    _, increment, issues = rail._drain_for_hook(_tool_ctx(ctx, {}), required_category="tool")
    assert increment is None and not issues
    rail.trajectory_span_processor.on_end(_span("llm.call", 2))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert [index for index, _ in prepared.execution_continuities] == [0]
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_business_tool_without_span_marks_quality_and_splits_continuity() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    _, increment, issues = rail._drain_for_hook(_tool_ctx(ctx, {}, name="lookup"), required_category="tool")
    assert increment is None
    assert [issue["code"] for issue in issues] == ["missing_required_span"]
    rail.trajectory_span_processor.on_end(_span("llm.call", 2))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert [index for index, _ in prepared.execution_continuities] == [0, 1]
    assert prepared.quality_flags == ("missing_required_span",)
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_missing_required_span_with_wrong_increment_splits_continuity() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 2))
    _, _, issues = rail._drain_for_hook(_tool_ctx(ctx, {}), required_category="tool")
    assert issues[0]["code"] == "missing_required_span"
    rail.trajectory_span_processor.on_end(_span("llm.call", 3))
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert [index for index, _ in prepared.execution_continuities] == [0, 1]
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_parallel_tool_callbacks_claim_one_batched_drain_by_call_id() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    for span_id, name, call_id in ((1, "alpha", "call-a"), (2, "beta", "call-b")):
        rail.trajectory_span_processor.on_end(
            _span(
                f"tool.{name}",
                span_id,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: name,
                    semconv.GEN_AI_TOOL_CALL_ID: call_id,
                    semconv.GEN_AI_TOOL_ID: f"resource-{name}",
                },
            )
        )

    _, first_increment, first_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="alpha", call_id="call-a"),
        required_category="tool",
    )
    assert first_increment is not None
    assert not first_issues
    state = rail._state(capture)
    assert state is not None
    assert [(token.call_id, token.tool_name) for token in state.pending_tool_tokens] == [("call-b", "beta")]

    _, second_increment, second_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="beta", call_id="call-b"),
        required_category="tool",
    )
    assert second_increment is None
    assert not second_issues
    assert not state.pending_tool_tokens
    assert "missing_required_span" not in state.quality_codes
    assert state.current_continuity_index == 0
    assert not state.continuity_break_pending
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_parallel_after_tool_drains_serialize_token_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    for span_id, name, call_id in ((1, "alpha", "call-a"), (2, "beta", "call-b")):
        rail.trajectory_span_processor.on_end(
            _span(
                f"tool.{name}",
                span_id,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: name,
                    semconv.GEN_AI_TOOL_CALL_ID: call_id,
                },
            )
        )

    first_drained = threading.Event()
    release_registration = threading.Event()
    original_remember = rail._remember_pending_tool_tokens

    def delayed_remember(state, increment):
        first_drained.set()
        assert release_registration.wait(timeout=2)
        original_remember(state, increment)

    monkeypatch.setattr(rail, "_remember_pending_tool_tokens", delayed_remember)
    results: dict[str, tuple] = {}

    def drain(label: str, call_id: str, name: str) -> None:
        results[label] = rail._drain_for_hook(
            _tool_ctx(ctx, {}, name=name, call_id=call_id),
            required_category="tool",
            capture=capture,
        )

    first = threading.Thread(target=drain, args=("first", "call-a", "alpha"))
    second = threading.Thread(target=drain, args=("second", "call-b", "beta"))
    first.start()
    assert first_drained.wait(timeout=2)
    second.start()
    release_registration.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert not results["first"][2]
    assert not results["second"][2]
    state = rail._state(capture)
    assert state is not None
    assert not state.pending_tool_tokens
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_tool_drain_does_not_hold_state_lock_while_waiting_for_scope_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = TrajectorySpanProcessor()
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=processor)
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    state = rail._state(capture)
    assert state is not None
    processor.on_end(
        _span(
            "tool.lookup",
            1,
            attributes={
                semconv.GEN_AI_TOOL_NAME: "lookup",
                semconv.GEN_AI_TOOL_CALL_ID: "call-a",
            },
        )
    )

    processor_drained = threading.Event()
    original_drain = processor.drain

    def observed_drain(subscription):
        result = original_drain(subscription)
        processor_drained.set()
        return result

    monkeypatch.setattr(processor, "drain", observed_drain)
    result: list[tuple] = []

    def drain_tool() -> None:
        result.append(
            rail._drain_for_hook(
                _tool_ctx(ctx, {}, name="lookup", call_id="call-a"),
                required_category="tool",
                capture=capture,
            )
        )

    scope_lock = rail._scope_lock(capture.scope_key)
    scope_lock.acquire()
    worker = threading.Thread(target=drain_tool)
    try:
        worker.start()
        assert processor_drained.wait(timeout=2)
        state_lock_available = state.lock.acquire(timeout=0.5)
        if state_lock_available:
            state.lock.release()
        assert state_lock_available
    finally:
        scope_lock.release()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert result and not result[0][2]
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_pending_tool_claim_keeps_new_clean_llm_increment() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    for span_id, name, call_id in ((1, "alpha", "call-a"), (2, "beta", "call-b")):
        rail.trajectory_span_processor.on_end(
            _span(
                f"tool.{name}",
                span_id,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: name,
                    semconv.GEN_AI_TOOL_CALL_ID: call_id,
                },
            )
        )
    rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="alpha", call_id="call-a"),
        required_category="tool",
    )
    rail.trajectory_span_processor.on_end(_span("llm.call", 3))

    _, increment, issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="beta", call_id="call-b"),
        required_category="tool",
    )

    assert increment is not None
    assert not issues
    state = rail._state(capture)
    assert state is not None
    assert state.span_count == 3
    assert not state.pending_tool_tokens
    assert [index for index, _ in rail._project_state_continuities(capture, state)] == [0]
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_wrong_tool_callback_does_not_consume_another_call_id() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    rail.trajectory_span_processor.on_end(
        _span(
            "tool.lookup",
            1,
            attributes={
                semconv.GEN_AI_TOOL_NAME: "lookup",
                semconv.GEN_AI_TOOL_CALL_ID: "call-b",
            },
        )
    )

    _, _, wrong_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-a"),
        required_category="tool",
    )
    assert [issue["code"] for issue in wrong_issues] == ["missing_required_span"]
    state = rail._state(capture)
    assert state is not None
    assert [(token.call_id, token.tool_name) for token in state.pending_tool_tokens] == [("call-b", "lookup")]
    assert state.span_count == 1
    projected = rail._project_state_trajectory(capture, state)
    assert projected is not None
    assert [span["name"] for span in iter_spans(projected)] == ["tool.lookup"]

    _, _, right_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-b"),
        required_category="tool",
    )
    assert not right_issues
    assert not state.pending_tool_tokens
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_parallel_same_name_tools_without_ids_use_counted_fallback() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    for span_id in (1, 2):
        rail.trajectory_span_processor.on_end(
            _span(
                "tool.lookup",
                span_id,
                attributes={semconv.GEN_AI_TOOL_NAME: "lookup"},
            )
        )

    _, first_increment, first_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup"),
        required_category="tool",
    )
    assert first_increment is not None
    assert not first_issues
    state = rail._state(capture)
    assert state is not None
    assert len(state.pending_tool_tokens) == 1

    _, second_increment, second_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup"),
        required_category="tool",
    )
    assert second_increment is None
    assert not second_issues
    assert not state.pending_tool_tokens

    _, _, third_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup"),
        required_category="tool",
    )
    assert [issue["code"] for issue in third_issues] == ["missing_required_span"]
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_resource_id_without_call_id_uses_tool_name_fallback() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    rail.trajectory_span_processor.on_end(
        _span(
            "tool.lookup",
            1,
            attributes={
                semconv.GEN_AI_TOOL_NAME: "lookup",
                semconv.GEN_AI_TOOL_ID: "resource-lookup",
            },
        )
    )

    _, increment, issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-a"),
        required_category="tool",
    )

    assert increment is not None
    assert not issues
    state = rail._state(capture)
    assert state is not None
    assert not state.pending_tool_tokens
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_unclaimed_parallel_tool_token_does_not_cross_invoke_cleanup() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    first_capture = rail._current_capture()
    assert first_capture is not None
    for span_id, call_id in ((1, "call-a"), (2, "call-b")):
        rail.trajectory_span_processor.on_end(
            _span(
                "tool.lookup",
                span_id,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: "lookup",
                    semconv.GEN_AI_TOOL_CALL_ID: call_id,
                },
            )
        )
    rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-a"),
        required_category="tool",
    )
    first_state = rail._state(first_capture)
    assert first_state is not None and len(first_state.pending_tool_tokens) == 1
    await rail.after_invoke(ctx)
    assert not rail._symphony_states

    await rail.before_invoke(ctx)
    _, _, issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-b"),
        required_category="tool",
    )
    assert [issue["code"] for issue in issues] == ["missing_required_span"]
    second_capture = rail._current_capture()
    assert second_capture is not None
    rail._unsubscribe_capture(second_capture)


@pytest.mark.asyncio
async def test_pending_tool_tokens_are_bounded_with_batched_trace() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    for span_id in range(1, 206):
        rail.trajectory_span_processor.on_end(
            _span(
                "tool.lookup",
                span_id,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: "lookup",
                    semconv.GEN_AI_TOOL_CALL_ID: f"call-{span_id}",
                },
            )
        )

    _, increment, issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="call-1"),
        required_category="tool",
    )

    assert increment is not None
    assert not issues
    state = rail._state(capture)
    assert state is not None
    assert state.span_count == 200
    assert len(state.pending_tool_tokens) == 200
    assert state.pending_tool_tokens[0].call_id == "call-6"
    assert state.pending_tool_tokens[-1].call_id == "call-205"
    assert state.discarded_pending_tool_callbacks == 4
    assert "truncated_trace" in state.quality_codes
    for call_id in range(2, 6):
        _, _, delayed_issues = rail._drain_for_hook(
            _tool_ctx(ctx, {}, name="lookup", call_id=f"call-{call_id}"),
            required_category="tool",
        )
        assert not delayed_issues
    assert state.discarded_pending_tool_callbacks == 0

    _, _, exhausted_issues = rail._drain_for_hook(
        _tool_ctx(ctx, {}, name="lookup", call_id="not-captured"),
        required_category="tool",
    )
    assert [issue["code"] for issue in exhausted_issues] == ["missing_required_span"]
    await rail.after_invoke(ctx)
    assert not rail._symphony_states


@pytest.mark.asyncio
async def test_rail_preserves_repeated_skill_occurrences() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("agent.main", 1))
    for span_id, skill in ((2, "alpha"), (3, "beta"), (4, "alpha")):
        rail.trajectory_span_processor.on_end(
            _span(
                "tool.skill_tool",
                span_id,
                parent_span_id=1,
                attributes={
                    semconv.GEN_AI_TOOL_NAME: "skill_tool",
                    semconv.GEN_AI_TOOL_INPUT: json.dumps({"skill_name": skill, "relative_file_path": "SKILL.md"}),
                    semconv.GEN_AI_TOOL_OUTPUT: json.dumps({"success": True}),
                },
            )
        )
    rail._drain_for_hook(ctx)
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert [
        fragment.capability_name for fragment in prepared.execution_fragments if fragment.capability_type == "skill"
    ] == ["alpha", "beta", "alpha"]
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_private_invoke_history_is_bounded_and_reports_truncation() -> None:
    sink = SimpleNamespace(submit=AsyncMock())
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None

    for span_id in range(1, 206):
        rail.trajectory_span_processor.on_end(_span("llm.call", span_id))
        rail._drain_for_hook(ctx)

    state = rail._state(capture)
    assert state is not None
    assert state.span_count == 200
    assert len(state.increments) == 200
    assert len(state.increment_continuities) == 200
    prepared = await rail._prepare_evolution_input(_trajectory(), ctx)
    assert prepared is not None
    assert len(tuple(iter_spans(prepared.trajectory))) == 200
    assert "truncated_trace" in prepared.quality_flags
    await rail.run_evolution(prepared)
    submission = sink.submit.await_args.args[0]
    assert "truncated_trace" in submission.execution_graph["quality_flags"]
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_private_invoke_history_can_be_unbounded() -> None:
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        max_trajectory_spans=None,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None

    for span_id in range(1, 206):
        rail.trajectory_span_processor.on_end(_span("llm.call", span_id))
        rail._drain_for_hook(ctx)

    state = rail._state(capture)
    assert state is not None
    assert state.span_count == 205
    assert len(state.increments) == 205
    assert "truncated_trace" not in state.quality_codes
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_private_history_partially_trims_the_oldest_increment() -> None:
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        max_trajectory_spans=3,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None

    for span_id in (1, 2, 3):
        rail.trajectory_span_processor.on_end(_span("llm.call", span_id))
    rail._drain_for_hook(ctx)
    for span_id in (4, 5):
        rail.trajectory_span_processor.on_end(_span("llm.call", span_id))
    rail._drain_for_hook(ctx)

    state = rail._state(capture)
    assert state is not None
    assert state.span_count == 3
    assert state.increment_span_counts == [1, 2]
    projected = rail._project_state_trajectory(capture, state)
    assert projected is not None
    assert [span["spanId"] for span in iter_spans(projected)] == [f"{span_id:016x}" for span_id in (3, 4, 5)]
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_observability_closes_spans_before_symphony_drains_by_priority() -> None:
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("symphony-priority-integration")
    received: list[SymphonyGraphEvolutionInput] = []

    async def consume(value: SymphonyGraphEvolutionInput) -> None:
        received.append(value)

    observability = AgentObservabilityRail(tracer=tracer)
    symphony = SymphonyGraphEvolutionRail(
        trajectory_span_processor=processor,
        input_consumer=consume,
        async_evolution=False,
    )
    rails = sorted((observability, symphony), key=lambda rail: rail.priority, reverse=True)
    assert [rail.priority for rail in rails] == [10, 5]

    shared_span_context.reset_state()
    root = tracer.start_span("run.root")
    root.set_attribute(semconv.OJ_TRACE_ROOT, True)
    root.set_attribute(semconv.OJ_SESSION_ID, "session-1")
    shared_span_context.set_root_span(root, session_id="session-1")
    agent = SimpleNamespace(
        member_name="solo",
        card=SimpleNamespace(id="member-1", name="solo", description=""),
        deep_config=SimpleNamespace(enable_task_loop=False),
    )
    session = SimpleNamespace(get_session_id=lambda: "session-1", get_agent_id=lambda: "member-1")
    invoke_ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="run", conversation_id="session-1"),
        session=session,
    )
    try:
        for rail in rails:
            await rail.before_invoke(invoke_ctx)

        model_ctx = AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(react_iteration=1),
            session=session,
            extra=invoke_ctx.extra,
        )
        for rail in rails:
            await rail.before_model_call(model_ctx)
        handler = OtelCallbackHandler(
            ObservabilityConfig(enabled=True, backend="otlp"),
            tracer=tracer,
        )
        llm_span = handler._open_llm_span({"messages": [{"role": "user", "content": "run"}], "model": "fake"})
        assert llm_span is not None
        handler._close_llm_span(
            llm_span.otel_llm_state,
            SimpleNamespace(
                content="tool next",
                reasoning_content="",
                finish_reason="stop",
                tool_calls=None,
                usage_metadata=None,
            ),
        )
        for rail in rails:
            await rail.after_model_call(model_ctx)

        tool_ctx = AgentCallbackContext(
            agent=agent,
            inputs=ToolCallInputs(tool_name="lookup", tool_args={"q": "x"}),
            session=session,
        )
        for rail in rails:
            await rail.before_tool_call(tool_ctx)
        tool_ctx.inputs.tool_result = {"answer": 1}
        for rail in rails:
            await rail.after_tool_call(tool_ctx)

        invoke_ctx.inputs.result = {"result_type": "answer", "output": "done"}
        for rail in rails:
            await rail.after_invoke(invoke_ctx)
    finally:
        if root.is_recording():
            root.end()
        shared_span_context.reset_state()

    assert len(received) == 1
    names = {str(span["name"]) for span in iter_spans(received[0].trajectory)}
    assert {"llm.call", "tool.lookup", "agent.solo.react_iteration.1", "agent.solo.invoke"} <= names
    assert "missing_required_span" not in received[0].quality_flags
    assert [(fragment.capability_type, fragment.capability_name) for fragment in received[0].execution_fragments] == [
        ("tool", "lookup")
    ]


@pytest.mark.asyncio
async def test_consecutive_invokes_prepare_only_current_invoke() -> None:
    received: list[SymphonyGraphEvolutionInput] = []

    async def consume(value: SymphonyGraphEvolutionInput) -> None:
        received.append(value)

    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        input_consumer=consume,
        async_evolution=False,
    )
    first = _ctx()
    await rail.before_invoke(first)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    await rail.after_invoke(first)
    second = _ctx()
    await rail.before_invoke(second)
    rail.trajectory_span_processor.on_end(_span("llm.call", 2))
    await rail.after_invoke(second)
    assert len(received) == 2
    assert [len(tuple(iter_spans(value.trajectory))) for value in received] == [1, 1]


@pytest.mark.asyncio
async def test_bad_final_increment_keeps_prior_clean_trace_and_quality_flag() -> None:
    received: list[SymphonyGraphEvolutionInput] = []

    async def consume(value: SymphonyGraphEvolutionInput) -> None:
        received.append(value)

    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        input_consumer=consume,
        async_evolution=False,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    rail._drain_for_hook(ctx)
    rail.trajectory_span_processor.on_end(
        _span("tool.call", 2, attributes={semconv.GEN_AI_TOOL_OUTPUT: "{'broken': ]"})
    )
    await rail.after_invoke(ctx)
    assert len(received) == 1
    assert len(tuple(iter_spans(received[0].trajectory))) == 1
    assert received[0].quality_flags == ("tool_payload_json_error",)


@pytest.mark.asyncio
async def test_same_session_concurrent_contexts_are_isolated() -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx_a = _ctx()
    context_a = Context()
    context_b = Context()

    async def start(ctx: AgentCallbackContext) -> None:
        await rail.before_invoke(ctx)

    context_a.run(asyncio.create_task, start(ctx_a))
    await asyncio.sleep(0)
    ctx_b = _ctx()
    context_b.run(asyncio.create_task, start(ctx_b))
    await asyncio.sleep(0)
    with rail._subscription_lock:
        assert len(rail._active_captures) == 2
    rail.uninit(SimpleNamespace())
    assert not rail._symphony_states


@pytest.mark.asyncio
async def test_team_callbacks_route_by_root_trace_and_root_loss_only_cleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(11)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    sink = SimpleNamespace(submit=AsyncMock())
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
        async_evolution=False,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    roots["value"] = _root(12)
    assert rail._resolve_capture(ctx=ctx) is None
    roots["value"] = None
    await rail.after_invoke(ctx)
    assert capture.subscription not in rail._active_captures
    sink.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_detached_team_root_loss_cleans_unique_session_without_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(13)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    sink = SimpleNamespace(submit=AsyncMock())
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
        async_evolution=False,
    )
    ctx = _ctx()
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    assert len(rail._active_captures) == 1
    roots["value"] = None
    await Context().run(asyncio.create_task, rail.after_invoke(ctx))
    sink.submit.assert_not_awaited()
    assert not rail._active_captures
    assert not rail._symphony_states


@pytest.mark.asyncio
async def test_detached_different_session_never_cleans_unique_active_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(131)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    rail = TeamSymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    owner_ctx = _ctx(session_id="session-a")
    await Context().run(asyncio.create_task, rail.before_invoke(owner_ctx))
    assert len(rail._active_captures) == 1

    roots["value"] = None
    unrelated_ctx = _ctx(session_id="session-b")
    await Context().run(asyncio.create_task, rail.after_invoke(unrelated_ctx))
    assert len(rail._active_captures) == 1
    assert len(rail._symphony_states) == 1
    rail.uninit(SimpleNamespace())


@pytest.mark.asyncio
async def test_detached_team_root_loss_does_not_guess_between_same_session_captures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(14)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    rail = TeamSymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    roots["value"] = _root(15)
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    roots["value"] = None
    await Context().run(asyncio.create_task, rail.after_invoke(ctx))
    assert len(rail._active_captures) == 2
    assert len(rail._symphony_states) == 2
    rail.uninit(SimpleNamespace())


@pytest.mark.asyncio
async def test_overlapping_team_context_never_falls_through_to_other_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(21)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    rail = TeamSymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    context_a = Context()
    context_b = Context()
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release = asyncio.Event()

    async def start(started: asyncio.Event) -> tuple[object, object]:
        await rail.before_invoke(ctx)
        capture = rail._current_capture()
        started.set()
        await release.wait()
        return capture, rail._resolve_capture(ctx=ctx)

    task_a = context_a.run(asyncio.create_task, start(started_a))
    await started_a.wait()
    roots["value"] = _root(22)
    task_b = context_b.run(asyncio.create_task, start(started_b))
    await started_b.wait()
    release.set()
    (capture_a, resolved_a), (capture_b, resolved_b) = await asyncio.gather(task_a, task_b)
    assert capture_a is not capture_b
    assert resolved_a is None
    assert resolved_b is capture_b
    rail.uninit(SimpleNamespace())


@pytest.mark.asyncio
async def test_detached_overlapping_team_invokes_submit_and_cleanup_by_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(31)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    received: list[SymphonyGraphEvolutionInput] = []

    async def consume(value: SymphonyGraphEvolutionInput) -> None:
        received.append(value)

    processor = TrajectorySpanProcessor()
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=processor,
        input_consumer=consume,
        async_evolution=False,
    )
    ctx = _ctx()
    roots["value"] = _root(31)
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    roots["value"] = _root(32)
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    processor.on_end(_span("llm.call", 1, trace_id=31))
    processor.on_end(_span("llm.call", 2, trace_id=32))

    roots["value"] = _root(31)
    await Context().run(asyncio.create_task, rail.after_invoke(ctx))
    roots["value"] = _root(32)
    await Context().run(asyncio.create_task, rail.after_invoke(ctx))

    assert {value.trace_id for value in received} == {f"{31:032x}", f"{32:032x}"}
    assert not rail._active_captures
    assert not rail._symphony_states


@pytest.mark.asyncio
async def test_snapshot_failure_marks_quality_and_before_exception_cleans() -> None:
    provider = SimpleNamespace(snapshot_capabilities=lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        capability_snapshot_provider=provider,
    )
    ctx = _ctx()
    prepared = await _prepare(rail, ctx)
    assert prepared.quality_flags == ("capability_snapshot_error",)
    capture = rail._current_capture()
    assert capture is not None
    rail._unsubscribe_capture(capture)

    class BrokenRail(SymphonyGraphEvolutionRail):
        async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
            await super()._on_before_invoke(ctx)
            raise RuntimeError("boom")

    broken = BrokenRail(trajectory_span_processor=TrajectorySpanProcessor())
    with pytest.raises(RuntimeError):
        await broken.before_invoke(_ctx())
    assert not broken._active_captures and not broken._symphony_states


@pytest.mark.asyncio
async def test_after_invoke_exception_still_cleans_private_state() -> None:
    class BrokenAfterRail(SymphonyGraphEvolutionRail):
        async def _on_after_invoke(self, ctx: AgentCallbackContext, trajectory: Trajectory | None) -> None:
            del ctx, trajectory
            raise RuntimeError("boom")

    rail = BrokenAfterRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    with pytest.raises(RuntimeError):
        await rail.after_invoke(ctx)
    assert not rail._active_captures and not rail._symphony_states


@pytest.mark.asyncio
async def test_after_invoke_session_resolution_error_still_cleans_capture() -> None:
    class FailingSession:
        fail = False

        def get_session_id(self) -> str:
            if self.fail:
                raise RuntimeError("session lookup failed")
            return "session-1"

        @staticmethod
        def get_agent_id() -> str:
            return "member-1"

    session = FailingSession()
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(card=SimpleNamespace(id="member-1")),
        inputs=InvokeInputs(query="run", conversation_id="session-1"),
        session=session,
    )
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    await rail.before_invoke(ctx)
    assert rail._current_capture() is not None
    session.fail = True
    with pytest.raises(RuntimeError, match="session lookup failed"):
        await rail.after_invoke(ctx)
    assert not rail._active_captures
    assert not rail._symphony_states
    assert rail._current_capture() is None


@pytest.mark.asyncio
async def test_run_evolution_sends_every_candidate_to_frozen_model_and_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment_a = SymphonyExecutionFragment("a", "skill", "a", "trace", "1", "root", ("1",), 0)
    fragment_b = SymphonyExecutionFragment("b", "tool", "b", "trace", "2", "root", ("2",), 0)
    candidate = SymphonyEdgeCandidate("c", fragment_a, fragment_b, ("trace#span=1", "trace#span=2"), ("planned",))
    unresolved = SymphonyEdgeDecision("c", "a", "b", "insufficient_evidence", "pending", (), "deterministic", "none")
    judged = replace(
        unresolved,
        status="success",
        reason="consumed",
        evidence_refs=("trace#span=1", "trace#span=2"),
        evidence_method="model_assisted",
        evidence_strength="low",
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(rail_module, "build_symphony_edge_candidates", lambda *a, **kw: (candidate,))
    monkeypatch.setattr(rail_module, "build_model_edge_decisions", lambda value: (unresolved,))

    async def evaluate(**kwargs):
        seen.update(kwargs)
        return (judged,)

    monkeypatch.setattr(rail_module, "evaluate_symphony_edge_candidates", evaluate)
    sink = SimpleNamespace(submit=AsyncMock())
    llm = SimpleNamespace(invoke=AsyncMock())
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
    )
    prepared = SymphonyGraphEvolutionInput(
        trajectory=_trajectory(),
        messages=(),
        execution_fragments=(fragment_a, fragment_b),
        capability_snapshot=(
            CapabilityIdentity("skill:a", "skill", "a", "v1", "sha256:a", ("in",), ("out",)),
            CapabilityIdentity("tool:b", "tool", "b", "v1", "sha256:b", ("in",), ("out",)),
        ),
        query="q",
        outcome="success",
        reason=None,
        trace_id="trace",
        edge_evaluator_llm=llm,
    )
    await rail.run_evolution(prepared)
    assert seen["llm"] is llm
    assert seen["candidates"] == (candidate,)
    submission = sink.submit.await_args.args[0]
    assert submission.execution_graph["graph"]["edges"]
    assert submission.execution_graph["graph"]["nodes"]["skill:a"]["metadata"]["output_ports"] == ["out"]


@pytest.mark.asyncio
async def test_no_relation_decision_is_excluded_but_submission_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SymphonyExecutionFragment("a", "skill", "a", "trace", "1", "root", ("1",), 0)
    target = SymphonyExecutionFragment("b", "tool", "b", "trace", "2", "root", ("2",), 0)
    candidate = SymphonyEdgeCandidate("c", source, target, ("trace#span=1", "trace#span=2"), ("planned",))
    unresolved = SymphonyEdgeDecision("c", "a", "b", "insufficient_evidence", "pending", (), "deterministic", "none")
    no_relation = replace(
        unresolved,
        status="no_relation",
        reason="not consumed",
        evidence_method="model_assisted",
        evidence_strength="none",
    )
    monkeypatch.setattr(rail_module, "build_symphony_edge_candidates", lambda *a, **kw: (candidate,))
    monkeypatch.setattr(rail_module, "build_model_edge_decisions", lambda value: (unresolved,))

    async def evaluate(**kwargs):
        del kwargs
        return (no_relation,)

    monkeypatch.setattr(rail_module, "evaluate_symphony_edge_candidates", evaluate)
    sink = SimpleNamespace(submit=AsyncMock())
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor(), observation_sink=sink)
    await rail.run_evolution(
        SymphonyGraphEvolutionInput(
            trajectory=_trajectory(),
            messages=(),
            execution_fragments=(source, target),
            query="q",
            outcome="success",
            reason=None,
            trace_id="trace",
            edge_evaluator_llm=SimpleNamespace(invoke=AsyncMock()),
        )
    )
    assert sink.submit.await_args.args[0].execution_graph["graph"]["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError("boom")))])
async def test_no_model_or_model_failure_still_submits_empty_graph(model: object | None) -> None:
    sink = SimpleNamespace(submit=AsyncMock())
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
    )
    prepared = SymphonyGraphEvolutionInput(
        trajectory=_trajectory(),
        messages=(),
        query="q",
        outcome="partial",
        reason="invoke_result_unverified",
        trace_id="trace",
        edge_evaluator_llm=model,  # type: ignore[arg-type]
    )
    await rail.run_evolution(prepared)
    submission = sink.submit.await_args.args[0]
    assert submission.execution_graph["graph"]["edges"] == []


@pytest.mark.asyncio
async def test_consumer_and_sink_failures_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    async def broken_consumer(value: SymphonyGraphEvolutionInput) -> None:
        del value
        raise RuntimeError("consumer-secret")

    sink = SimpleNamespace(submit=AsyncMock(side_effect=RuntimeError("sink-secret")))
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        observation_sink=sink,
        input_consumer=broken_consumer,
    )
    prepared = SymphonyGraphEvolutionInput(
        trajectory=_trajectory(),
        messages=(),
        outcome="success",
        reason=None,
        trace_id="trace",
    )
    await rail.run_evolution(prepared)
    sink.submit.assert_awaited_once()
    assert "consumer-secret" not in caplog.text
    assert "sink-secret" not in caplog.text


@pytest.mark.asyncio
async def test_residual_invalid_planned_graph_is_omitted_without_losing_submission() -> None:
    sink = SimpleNamespace(submit=AsyncMock())
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor(), observation_sink=sink)
    invalid = _ready_graph("invalid")
    del invalid["graph"]["id"]
    prepared = SymphonyGraphEvolutionInput(
        trajectory=_trajectory(),
        messages=(),
        planned_graph=invalid,
        outcome="success",
        reason=None,
        trace_id="trace",
    )
    await rail.run_evolution(prepared)
    submission = sink.submit.await_args.args[0]
    assert submission.planned_graph is None
    assert submission.execution_graph["graph"]["edges"] == []


@pytest.mark.asyncio
async def test_candidate_probe_is_bounded_and_truncation_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    source = SymphonyExecutionFragment("a", "skill", "a", "trace", "1", "root", ("1",), 0)
    target = SymphonyExecutionFragment("b", "tool", "b", "trace", "2", "root", ("2",), 0)

    def candidates(*args, **kwargs):
        del args
        seen.update(kwargs)
        return tuple(
            SymphonyEdgeCandidate(
                f"candidate-{index}",
                source,
                target,
                ("trace#span=1", "trace#span=2"),
                ("planned",),
            )
            for index in range(65)
        )

    monkeypatch.setattr(rail_module, "build_symphony_edge_candidates", candidates)
    sink = SimpleNamespace(submit=AsyncMock())
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor(), observation_sink=sink)
    await rail.run_evolution(
        SymphonyGraphEvolutionInput(
            trajectory=_trajectory(), messages=(), outcome="success", reason=None, trace_id="trace"
        )
    )
    assert seen["max_candidates"] == 65
    submission = sink.submit.await_args.args[0]
    assert submission.execution_graph["quality_flags"] == ["edge_candidates_truncated"]
    assert submission.execution_graph["graph"]["edges"] == []


def test_summary_redacts_binary_and_bounds_values() -> None:
    value = {"base64_blob": "A" * 1000, "normal": "B" * 1000, "raw": b"secret"}
    compact = rail_module._compact_trace_value(value)
    assert compact["base64_blob"] == "<redacted>"
    assert compact["raw"] == "<redacted>"
    assert len(compact["normal"].encode()) <= 256


@pytest.mark.asyncio
async def test_background_prepared_input_survives_capture_cleanup() -> None:
    received: list[SymphonyGraphEvolutionInput] = []
    gate = asyncio.Event()

    async def consume(value: SymphonyGraphEvolutionInput) -> None:
        await gate.wait()
        received.append(value)

    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        input_consumer=consume,
        async_evolution=True,
    )
    ctx = _ctx()
    await rail.before_invoke(ctx)
    rail.trajectory_span_processor.on_end(_span("llm.call", 1))
    await rail.after_invoke(ctx)
    assert not rail._symphony_states
    gate.set()
    await rail.drain_pending_host_events(wait=True)
    assert len(received) == 1
    assert len(tuple(iter_spans(received[0].trajectory))) == 1


def test_public_exports_are_available() -> None:
    from openjiuwen.harness.rails import (  # noqa: PLC0415
        SymphonyGraphEvolutionRail as PublicRail,
    )
    from openjiuwen.harness.rails import (
        TeamSymphonyGraphEvolutionRail as PublicTeamRail,
    )

    assert PublicRail is SymphonyGraphEvolutionRail
    assert PublicTeamRail is TeamSymphonyGraphEvolutionRail
