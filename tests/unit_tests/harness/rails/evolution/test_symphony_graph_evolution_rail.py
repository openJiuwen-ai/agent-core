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
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
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


def _graph_snapshot() -> dict[str, str]:
    return {
        "static_revision": "static-start",
        "observation_revision": "observation-start",
    }


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
    query: object = "run",
) -> AgentCallbackContext:
    session = SimpleNamespace(
        get_session_id=lambda: session_id,
        get_agent_id=lambda: member_id,
    )
    return AgentCallbackContext(
        agent=SimpleNamespace(card=SimpleNamespace(id=member_id)),
        inputs=InvokeInputs(
            query=query,  # type: ignore[arg-type]
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
    graph_snapshot = {
        "static_revision": "static-start",
        "observation_revision": "observation-start",
        "merged_revision": "merged-start",
    }
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        capability_snapshot_provider=provider,
        graph_snapshot_provider=lambda: graph_snapshot,
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
    assert prepared.graph_snapshot["static_revision"] == "static-start"
    graph_snapshot["static_revision"] = "mutated"
    assert prepared.graph_snapshot["static_revision"] == "static-start"
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


def test_constructor_requires_graph_snapshot_provider_for_submission() -> None:
    with pytest.raises(ValueError, match="graph_snapshot_provider"):
        SymphonyGraphEvolutionRail(
            trajectory_span_processor=TrajectorySpanProcessor(),
            submit_evolution=AsyncMock(),
        )


def test_prepared_input_rejects_invalid_capture_mode() -> None:
    with pytest.raises(ValueError, match="capture_mode"):
        SymphonyGraphEvolutionInput(
            trajectory=_trajectory(),
            messages=(),
            capture_mode="invalid",  # type: ignore[arg-type]
        )


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
@pytest.mark.parametrize("status", ["needs_input", "no_plan"])
async def test_non_ready_planned_graph_status_is_not_marked_invalid(status: str) -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    graph = _ready_graph("not-ready")
    graph["graph"]["metadata"]["status"] = status
    await rail._on_after_tool_call(_tool_ctx(ctx, {"success": True, "planned_graph": graph}), None)
    capture = rail._current_capture()
    assert capture is not None
    state = rail._state(capture)
    assert state is not None
    assert "planned_graph_invalid" not in state.quality_codes
    rail._unsubscribe_capture(capture)


@pytest.mark.asyncio
async def test_legacy_framework_error_and_truncated_text_are_accepted_but_malformed_splits() -> None:
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
        _span("tool.call", 3, attributes={semconv.GEN_AI_TOOL_OUTPUT: "[ERROR]: request failed"})
    )
    _, _, issues = rail._drain_for_hook(ctx, required_category="tool")
    assert not issues
    rail.trajectory_span_processor.on_end(
        _span(
            "tool.call",
            4,
            attributes={
                semconv.GEN_AI_TOOL_OUTPUT: (
                    '{"success": true, "data": {"skill_content": "large...<truncated 16270 chars>'
                )
            },
        )
    )
    _, _, issues = rail._drain_for_hook(ctx, required_category="tool")
    assert not issues
    rail.trajectory_span_processor.on_end(
        _span("tool.call", 5, attributes={semconv.GEN_AI_TOOL_OUTPUT: "{'broken': ]"})
    )
    _, _, issues = rail._drain_for_hook(ctx, required_category="tool")
    assert {issue["code"] for issue in issues} == {"tool_payload_json_error"}
    rail.trajectory_span_processor.on_end(_span("llm.call", 6))
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
async def test_interrupt_defers_and_exact_interactive_resume_keeps_original_query() -> None:
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        graph_snapshot_provider=_graph_snapshot,
        submit_evolution=callback,
        async_evolution=False,
    )
    interrupted = _ctx(result={"result_type": "interrupt", "component_ids": ["ask-user"]})
    await rail.before_invoke(interrupted)
    rail.trajectory_span_processor.on_end(_span("llm.first", 1))
    rail._drain_for_hook(interrupted)
    await rail.after_invoke(interrupted)

    assert callback.await_count == 0
    assert len(rail._paused_symphony_states) == 1

    user_input = InteractiveInput()
    user_input.update("ask-user", "yes")
    resumed = _ctx(query=user_input, result={"result_type": "answer", "output": "done"})
    await rail.before_invoke(resumed)
    resumed_capture = rail._current_capture()
    assert resumed_capture is not None
    resumed_state = rail._state(resumed_capture)
    assert resumed_state is not None
    assert resumed_state.original_query == "run"
    await rail.after_invoke(resumed)

    assert callback.await_count == 0
    assert not rail._paused_symphony_states


def test_real_tool_interrupt_result_uses_interrupt_ids_and_conflicting_aliases_fail_closed() -> None:
    result = ToolInterruptHandler.build_interrupt_result([("tool-call-1", {"question": "continue?"})])
    assert rail_module._interrupt_component_ids(result) == ("tool-call-1",)
    assert (
        rail_module._interrupt_component_ids(
            {"result_type": "interrupt", "interrupt_ids": ["a"], "component_ids": ["b"]}
        )
        == ()
    )


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
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
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
    execution_graph = callback.await_args.args[1]
    assert "truncated_trace" in execution_graph["quality_flags"]
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
    callback = AsyncMock()
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
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
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_detached_team_root_loss_cleans_unique_session_without_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {"value": _root(13)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    callback = AsyncMock()
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
        async_evolution=False,
    )
    ctx = _ctx()
    await Context().run(asyncio.create_task, rail.before_invoke(ctx))
    assert len(rail._active_captures) == 1
    roots["value"] = None
    await Context().run(asyncio.create_task, rail.after_invoke(ctx))
    callback.assert_not_awaited()
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
        graph_snapshot_provider=lambda: (_ for _ in ()).throw(RuntimeError("private")),
    )
    ctx = _ctx()
    prepared = await _prepare(rail, ctx)
    assert set(prepared.quality_flags) == {"capability_snapshot_error", "graph_snapshot_error"}
    assert prepared.graph_snapshot is None
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
async def test_run_evolution_sends_every_candidate_to_frozen_model_and_callback(
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
    callback = AsyncMock()
    llm = SimpleNamespace(invoke=AsyncMock())
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
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
        graph_snapshot={
            "static_revision": "static-start",
            "observation_revision": "observation-start",
            "merged_revision": "merged-start",
        },
        edge_evaluator_llm=llm,
    )
    await rail.run_evolution(prepared)
    assert seen["llm"] is llm
    assert seen["candidates"] == (candidate,)
    execution_graph = callback.await_args.args[1]
    assert execution_graph["graph"]["edges"]
    assert execution_graph["graph_snapshot"]["static_revision"] == "static-start"
    assert execution_graph["graph"]["nodes"]["skill:a"]["metadata"]["output_ports"] == ["out"]
    assert callback.await_args.kwargs == {"session_id": "unknown", "capture_mode": "agent"}


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
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
    )
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
    assert callback.await_args.args[1]["graph"]["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError("boom")))])
async def test_no_model_or_model_failure_still_submits_empty_graph(model: object | None) -> None:
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
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
    execution_graph = callback.await_args.args[1]
    assert execution_graph["graph"]["edges"] == []


@pytest.mark.asyncio
async def test_consumer_and_callback_failures_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    async def broken_consumer(value: SymphonyGraphEvolutionInput) -> None:
        del value
        raise RuntimeError("consumer-secret")

    callback = AsyncMock(side_effect=RuntimeError("callback-secret"))
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
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
    callback.assert_awaited_once()
    assert "consumer-secret" not in caplog.text
    assert "callback-secret" not in caplog.text


@pytest.mark.asyncio
async def test_residual_invalid_planned_graph_is_omitted_without_losing_submission() -> None:
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
    )
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
    call = callback.await_args
    assert call.args[0] is None
    assert call.args[1]["graph"]["edges"] == []


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
    callback = AsyncMock()
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
    )
    await rail.run_evolution(
        SymphonyGraphEvolutionInput(
            trajectory=_trajectory(), messages=(), outcome="success", reason=None, trace_id="trace"
        )
    )
    assert seen["max_candidates"] == 65
    execution_graph = callback.await_args.args[1]
    assert execution_graph["quality_flags"] == ["edge_candidates_truncated"]
    assert execution_graph["graph"]["edges"] == []


def test_summary_redacts_binary_and_bounds_values() -> None:
    value = {"base64_blob": "A" * 1000, "normal": "B" * 1000, "raw": b"secret"}
    compact = rail_module._compact_trace_value(value)
    assert compact["base64_blob"] == "<redacted>"
    assert compact["raw"] == "<redacted>"
    assert len(compact["normal"].encode()) <= 2048


def test_edge_summary_covers_expanded_fragment_head_and_tail() -> None:
    trace_id = "1" * 32

    def tool_span(span_id: int, name: str, content: str) -> dict:
        return {
            "traceId": trace_id,
            "spanId": f"{span_id:016x}",
            "name": f"tool.{name}",
            "startTimeUnixNano": str(span_id),
            "endTimeUnixNano": str(span_id + 1),
            "attributes": attributes_from_map(
                {
                    semconv.GEN_AI_TOOL_NAME: name,
                    semconv.GEN_AI_TOOL_INPUT: json.dumps({"content": content}),
                    semconv.GEN_AI_TOOL_OUTPUT: json.dumps({"success": True}),
                }
            ),
        }

    source_ids = tuple(f"{index:016x}" for index in range(1, 4))
    target_ids = tuple(f"{index:016x}" for index in range(10, 25))
    spans = [tool_span(index, f"source-{index}", "source") for index in range(1, 4)]
    for position, span_id in enumerate(range(10, 25), start=1):
        marker = f"target-{position}"
        content = marker
        if position == 5:
            content = f'{{"guide":"{"x" * 1500} weather-consumed-18-31C"}}'
        spans.append(tool_span(span_id, marker, content))
    trajectory = Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map({TRAJECTORY_ID: "summary"})},
                    "scopeSpans": [{"spans": spans}],
                }
            ]
        }
    )
    source = SymphonyExecutionFragment("source", "skill", "weather", trace_id, source_ids[0], "branch", source_ids, 0)
    target = SymphonyExecutionFragment(
        "target", "skill", "travel-guide-generator", trace_id, target_ids[0], "branch", target_ids, 0
    )
    candidate = SymphonyEdgeCandidate(
        "candidate",
        source,
        target,
        (f"{trace_id}#span={source.anchor_span_id}", f"{trace_id}#span={target.anchor_span_id}"),
        ("planned",),
    )

    summary = rail_module._build_edge_summaries((candidate,), ((0, trajectory),))["candidate"].endpoint_b

    assert "target-1" in summary.fragment
    assert "weather-consumed-18-31C" in summary.input
    assert "target-15" in summary.output
    assert "target-8" not in f"{summary.fragment}{summary.input}{summary.output}"


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


def _resume_input(*components: str) -> InteractiveInput:
    response = InteractiveInput()
    for component in components:
        response.update(component, "confirmed")
    return response


def _emit_interrupt_segment(rail: SymphonyGraphEvolutionRail, trace: int) -> None:
    processor = rail.trajectory_span_processor
    processor.on_end(_span("agent.root", 1, trace_id=trace))
    processor.on_end(
        _span("agent.worker", 2, trace_id=trace, parent_span_id=1, attributes={semconv.AT_MEMBER_ID: "worker"})
    )
    processor.on_end(
        _span(
            "tool.skill_tool",
            3,
            trace_id=trace,
            parent_span_id=2,
            attributes={
                semconv.GEN_AI_TOOL_NAME: "skill_tool",
                semconv.GEN_AI_TOOL_INPUT: json.dumps({"skill_name": "alpha", "relative_file_path": "SKILL.md"}),
                semconv.GEN_AI_TOOL_OUTPUT: json.dumps({"success": True}),
            },
        )
    )
    processor.on_end(
        _span("tool.lookup", 4, trace_id=trace, parent_span_id=2, attributes={semconv.GEN_AI_TOOL_NAME: "lookup"})
    )
    processor.on_end(_span("llm.call", 5, trace_id=trace, parent_span_id=2))


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SymphonyGraphEvolutionRail, TeamSymphonyGraphEvolutionRail])
@pytest.mark.parametrize("resumes", [1, 2])
@pytest.mark.parametrize("id_field", ["component_ids", "interrupt_ids"])
async def test_interrupt_lifecycle_matrix_preserves_complete_input(
    monkeypatch: pytest.MonkeyPatch,
    rail_type: type,
    resumes: int,
    id_field: str,
) -> None:
    roots = {"value": _root(1)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    initial_model = SimpleNamespace(invoke=AsyncMock())
    identity = CapabilityIdentity("skill:alpha", "skill", "alpha", "v1", "sha256:a", ("in",), ("out",))
    snapshot = _graph_snapshot()
    received = []
    callback = AsyncMock()
    consumer = AsyncMock(side_effect=received.append)
    rail = rail_type(
        trajectory_span_processor=TrajectorySpanProcessor(),
        capability_snapshot_provider=SimpleNamespace(snapshot_capabilities=lambda: [identity]),
        graph_snapshot_provider=lambda: snapshot,
        submit_evolution=callback,
        input_consumer=consumer,
        async_evolution=False,
        edge_evaluator_llm=initial_model,
        edge_search_max_depth=7,
    )
    trigger = AsyncMock(wraps=rail._trigger_evolution)
    monkeypatch.setattr(rail, "_trigger_evolution", trigger)
    saved = None
    for segment in range(resumes + 1):
        roots["value"] = _root(segment + 1)
        interrupt = segment < resumes
        result = (
            ToolInterruptHandler.build_interrupt_result([("ask-user", {"question": "continue?"})])
            if interrupt and id_field == "interrupt_ids"
            else {"result_type": "interrupt", id_field: ["ask-user"]}
            if interrupt
            else {"result_type": "answer", "output": "done"}
        )
        ctx = _ctx(query="original task" if segment == 0 else _resume_input("ask-user"), result=result)
        await rail.before_invoke(ctx)
        state = rail._state(rail._current_capture())
        assert state is not None
        if saved is not None:
            assert state is saved
            assert len(state.increments) == segment
            assert state.current_continuity_index == 0
        _emit_interrupt_segment(rail, segment + 1)
        await rail._on_after_tool_call(_tool_ctx(ctx, {"planned_graph": _ready_graph(f"plan-{segment}")}), None)
        if segment == 0:
            await rail._on_after_tool_call(_tool_ctx(ctx, {"planned_graph": {"graph": {}}}), None)
            rail.update_edge_evaluator_llm(SimpleNamespace(invoke=AsyncMock()))
            rail._edge_search_max_depth = 1
            snapshot["static_revision"] = "changed"
        saved = state
        await rail.after_invoke(ctx)
        if interrupt:
            trigger.assert_not_awaited()
            callback.assert_not_awaited()
            consumer.assert_not_awaited()
            assert len(rail._paused_symphony_states) == 1
    assert trigger.await_count == callback.await_count == consumer.await_count == 1
    prepared = received[0]
    assert prepared.query == "original task"
    assert prepared.edge_evaluator_llm is initial_model
    assert prepared.edge_search_max_depth == 7
    assert prepared.capability_snapshot == (identity,)
    assert prepared.graph_snapshot == {**_graph_snapshot(), "merged_revision": None}
    assert prepared.planned_graph["graph"]["id"] == "plan-0"
    assert prepared.quality_flags == ("planned_graph_invalid",)
    assert prepared.trace_ids == tuple(f"{index:032x}" for index in range(1, resumes + 2))
    assert len(prepared.interrupt_continuations) == resumes
    assert {fragment.trace_id for fragment in prepared.execution_fragments} == set(prepared.trace_ids)
    assert {index for index, _ in prepared.execution_continuities} == {0}
    assert len(list(iter_spans(prepared.trajectory))) == 5 * (resumes + 1)
    if rail_type is TeamSymphonyGraphEvolutionRail:
        assert [fragment.capability_name for fragment in prepared.execution_fragments] == ["worker"] * (resumes + 1)
        assert callback.await_args.kwargs["capture_mode"] == "team"
    else:
        assert {fragment.capability_type for fragment in prepared.execution_fragments} >= {"skill", "tool"}
    assert callback.await_args.args[1]["trace_ids"] == list(prepared.trace_ids)
    assert not rail._paused_symphony_states and not rail._symphony_states and not rail._active_captures


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SymphonyGraphEvolutionRail, TeamSymphonyGraphEvolutionRail])
@pytest.mark.parametrize(
    "case", ["wrong", "multiple", "new_task", "cancel", "error", "conflict", "before", "after", "uninit"]
)
async def test_interrupt_lifecycle_invalidations_never_revive(
    monkeypatch: pytest.MonkeyPatch,
    rail_type: type,
    case: str,
) -> None:
    roots = {"value": _root(1)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    callback = AsyncMock()
    rail = rail_type(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
        async_evolution=False,
    )
    ctx = _ctx(result={"result_type": "interrupt", "interrupt_ids": ["ask"]})
    await rail.before_invoke(ctx)
    _emit_interrupt_segment(rail, 1)
    await rail.after_invoke(ctx)
    assert rail._paused_symphony_states
    roots["value"] = _root(2)
    if case == "uninit":
        rail.uninit(SimpleNamespace())
    else:
        query = (
            "new task"
            if case == "new_task"
            else _resume_input("wrong")
            if case == "wrong"
            else (_resume_input("ask", "other") if case == "multiple" else _resume_input("ask"))
        )
        result = {"result_type": "interrupt", "interrupt_ids": ["ask"]}
        if case == "cancel":
            result = {"result_type": "cancelled"}
        elif case == "error":
            result = {"result_type": "error"}
        elif case == "conflict":
            result["success"] = False
        elif case == "new_task":
            result = {"result_type": "answer", "output": "new done"}
        follow = _ctx(query=query, result=result)
        if case == "before":
            original = rail._on_before_invoke

            async def broken_before(context):
                await original(context)
                raise RuntimeError("before failed")

            with monkeypatch.context() as patch:
                patch.setattr(rail, "_on_before_invoke", broken_before)
                with pytest.raises(RuntimeError, match="before failed"):
                    await rail.before_invoke(follow)
        else:
            await rail.before_invoke(follow)
            _emit_interrupt_segment(rail, 2)
            if case == "after":
                with monkeypatch.context() as patch:
                    patch.setattr(rail, "_on_after_invoke", AsyncMock(side_effect=RuntimeError("after failed")))
                    with pytest.raises(RuntimeError, match="after failed"):
                        await rail.after_invoke(follow)
            else:
                await rail.after_invoke(follow)
    assert not rail._paused_symphony_states
    prior_calls = callback.await_count
    assert prior_calls == (1 if case == "new_task" else 0)
    # A rejected restore that itself interrupts must not establish a new chain.
    roots["value"] = _root(3)
    final = _ctx(query=_resume_input("ask"))
    await rail.before_invoke(final)
    _emit_interrupt_segment(rail, 3)
    await rail.after_invoke(final)
    assert callback.await_count == prior_calls
    assert not rail._paused_symphony_states and not rail._active_captures and not rail._symphony_states


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["needs_input", "no_plan"])
@pytest.mark.parametrize("malformed", ["nodes", "edges", "dangling"])
async def test_nonready_plan_still_requires_valid_graph(status: str, malformed: str) -> None:
    rail = SymphonyGraphEvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    ctx = _ctx()
    await rail.before_invoke(ctx)
    graph = _ready_graph("nonready")
    graph["graph"]["metadata"]["status"] = status
    if malformed == "dangling":
        graph["graph"]["edges"] = [{"source": "missing", "target": "also-missing"}]
    else:
        graph["graph"][malformed] = "invalid"
    await rail._on_after_tool_call(_tool_ctx(ctx, {"planned_graph": graph}), None)
    assert "planned_graph_invalid" in rail._state(rail._current_capture()).quality_codes
    await rail.after_invoke(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "owner", "capture_mode"])
async def test_same_component_isolated_by_resume_scope(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    roots = {"value": _root(1)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    received = []
    rail = SymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        input_consumer=AsyncMock(side_effect=received.append),
        async_evolution=False,
    )
    contexts = [
        _ctx(query="first", result={"result_type": "interrupt", "component_ids": ["shared"]}),
        _ctx(
            query="second",
            session_id="other" if scope == "session" else "session-1",
            member_id="other" if scope == "owner" else "member-1",
            result={"result_type": "interrupt", "component_ids": ["shared"]},
        ),
    ]
    if scope == "capture_mode":
        contexts[1].team_id = "member-1"
    for index, ctx in enumerate(contexts, 1):
        roots["value"] = _root(index)
        await rail.before_invoke(ctx)
        _emit_interrupt_segment(rail, index)
        await rail.after_invoke(ctx)
    assert len(rail._paused_symphony_states) == 2
    for index, ctx in enumerate(contexts, 3):
        roots["value"] = _root(index)
        ctx.inputs = InvokeInputs(query=_resume_input("shared"), result={"result_type": "answer", "output": "done"})
        await rail.before_invoke(ctx)
        _emit_interrupt_segment(rail, index)
        await rail.after_invoke(ctx)
    assert [item.query for item in received] == ["first", "second"]
    assert received[0].trace_ids == (f"{1:032x}", f"{3:032x}")
    assert received[1].trace_ids == (f"{2:032x}", f"{4:032x}")
    assert not rail._paused_symphony_states


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SymphonyGraphEvolutionRail, TeamSymphonyGraphEvolutionRail])
@pytest.mark.parametrize("count", [2, 3])
async def test_concurrent_same_scope_interrupt_key_never_selects_a_winner(
    monkeypatch: pytest.MonkeyPatch,
    rail_type: type,
    count: int,
) -> None:
    roots = {"value": _root(1)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    callback = AsyncMock()
    rail = rail_type(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
        async_evolution=False,
    )
    contexts = [_ctx(result={"result_type": "interrupt", "component_ids": ["shared"]}) for _ in range(count)]
    for index, ctx in enumerate(contexts, 1):
        roots["value"] = _root(index)
        await Context().run(asyncio.create_task, rail.before_invoke(ctx))
        _emit_interrupt_segment(rail, index)
    assert len(rail._active_captures) == count
    for index, ctx in enumerate(contexts, 1):
        roots["value"] = _root(index)
        await Context().run(asyncio.create_task, rail.after_invoke(ctx))
    roots["value"] = _root(count + 1)
    resume = _ctx(query=_resume_input("shared"))
    await rail.before_invoke(resume)
    _emit_interrupt_segment(rail, count + 1)
    await rail.after_invoke(resume)
    callback.assert_not_awaited()
    assert not rail._paused_symphony_states and not rail._active_captures


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SymphonyGraphEvolutionRail, TeamSymphonyGraphEvolutionRail])
async def test_resume_retains_pending_continuity_gap_and_quality(
    monkeypatch: pytest.MonkeyPatch,
    rail_type: type,
) -> None:
    roots = {"value": _root(1)}
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots["value"])
    received = []
    rail = rail_type(
        trajectory_span_processor=TrajectorySpanProcessor(),
        input_consumer=AsyncMock(side_effect=received.append),
        async_evolution=False,
    )
    ctx = _ctx(result={"result_type": "interrupt", "component_ids": ["ask"]})
    await rail.before_invoke(ctx)
    _emit_interrupt_segment(rail, 1)
    rail._drain_for_hook(ctx)
    # A real malformed tool payload is rejected after an earlier clean drain.
    rail.trajectory_span_processor.on_end(
        _span(
            "tool.bad",
            6,
            trace_id=1,
            parent_span_id=2,
            attributes={semconv.GEN_AI_TOOL_NAME: "bad", semconv.GEN_AI_TOOL_OUTPUT: "{broken"},
        )
    )
    await rail.after_invoke(ctx)
    paused = next(iter(rail._paused_symphony_states.values()))
    assert paused.continuity_break_pending
    assert "tool_payload_json_error" in paused.quality_codes
    roots["value"] = _root(2)
    restored = _ctx(query=_resume_input("ask"))
    await rail.before_invoke(restored)
    _emit_interrupt_segment(rail, 2)
    await rail.after_invoke(restored)
    assert len(received) == 1
    prepared = received[0]
    assert {index for index, _ in prepared.execution_continuities} == {0, 1}
    assert "tool_payload_json_error" in prepared.quality_flags
    assert len(list(iter_spans(prepared.trajectory))) == 10
    assert prepared.interrupt_continuations[0].continuity_index == 0


@pytest.mark.asyncio
async def test_team_member_spans_and_repeated_completion_do_not_duplicate_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: _root(1))
    callback = AsyncMock()
    rail = TeamSymphonyGraphEvolutionRail(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
        async_evolution=False,
    )
    leader = _ctx(member_id="leader")
    await rail.before_invoke(leader)
    _emit_interrupt_segment(rail, 1)
    for index, member in enumerate(("worker", "reviewer"), 10):
        rail.trajectory_span_processor.on_end(
            _span("agent.member", index, trace_id=1, parent_span_id=1, attributes={semconv.AT_MEMBER_ID: member})
        )
    callback.assert_not_awaited()
    await rail.after_invoke(leader)
    for member in ("worker", "reviewer", "leader"):
        await rail.after_invoke(_ctx(member_id=member))
    assert callback.await_count == 1
    assert callback.await_args.kwargs["capture_mode"] == "team"


def test_prepared_input_preserves_legacy_positional_constructor() -> None:
    trajectory = _trajectory()
    identity = CapabilityIdentity("skill:a", "skill", "a", "v1", "sha256:a", ("in",), ("out",))
    # Historical positional order includes inherited skill_name before the
    # original Symphony fields, with capability_snapshot in position seven.
    prepared = SymphonyGraphEvolutionInput(
        trajectory,
        (),
        None,
        _ready_graph("plan"),
        (),
        (),
        (identity,),
        _graph_snapshot(),
        "original task",
        "success",
        None,
        "trace",
        "session",
        "agent",
        ("quality",),
        None,
        7,
    )
    assert prepared.capability_snapshot == (identity,)
    assert prepared.graph_snapshot == _graph_snapshot()
    assert prepared.query == "original task"
    assert prepared.edge_search_max_depth == 7
    assert prepared.trace_ids == prepared.interrupt_continuations == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SymphonyGraphEvolutionRail, TeamSymphonyGraphEvolutionRail])
async def test_conflict_invalidates_third_pause_waiting_to_acquire_lock(
    monkeypatch: pytest.MonkeyPatch, rail_type: type
) -> None:
    roots = threading.local()
    roots.value = _root(1)
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: roots.value)
    callback = AsyncMock()
    rail = rail_type(
        trajectory_span_processor=TrajectorySpanProcessor(),
        submit_evolution=callback,
        graph_snapshot_provider=_graph_snapshot,
        async_evolution=False,
    )
    contexts = [_ctx(result={"result_type": "interrupt", "component_ids": ["shared"]}) for _ in range(3)]
    for index, ctx in enumerate(contexts, 1):
        roots.value = _root(index)
        await Context().run(asyncio.create_task, rail.before_invoke(ctx))
        _emit_interrupt_segment(rail, index)
    roots.value = _root(1)
    await Context().run(asyncio.create_task, rail.after_invoke(contexts[0]))
    entered = threading.Event()
    release = threading.Event()
    original_lock = rail._symphony_states_lock
    original_pause = rail._pause_interrupt_state
    errors = []

    class ControlledLock:
        def __enter__(self):
            if getattr(roots, "block_pause", False):
                roots.block_pause = False
                entered.set()
                assert release.wait(5), "conflict thread did not release the waiting pause"
            original_lock.acquire()
            return self

        def __exit__(self, *args):
            original_lock.release()

    def pause_after_outer_check(state, inputs):
        if threading.current_thread().name == "third-pause":
            roots.block_pause = True
        original_pause(state, inputs)

    monkeypatch.setattr(rail, "_symphony_states_lock", ControlledLock())
    monkeypatch.setattr(rail, "_pause_interrupt_state", pause_after_outer_check)

    def third_completion():
        roots.value = _root(3)
        try:
            asyncio.run(rail.after_invoke(contexts[2]))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=third_completion, name="third-pause")
    worker.start()
    try:
        assert await asyncio.to_thread(entered.wait, 5), "third pause did not reach the lock boundary"
        roots.value = _root(2)
        await Context().run(asyncio.create_task, rail.after_invoke(contexts[1]))
        assert not rail._paused_symphony_states
    finally:
        release.set()
        await asyncio.to_thread(worker.join, 5)
    assert not worker.is_alive() and not errors
    assert not rail._paused_symphony_states
    roots.value = _root(4)
    restored = _ctx(query=_resume_input("shared"))
    await rail.before_invoke(restored)
    _emit_interrupt_segment(rail, 4)
    await rail.after_invoke(restored)
    callback.assert_not_awaited()


def test_public_exports_are_available() -> None:
    from openjiuwen.harness.rails import (  # noqa: PLC0415
        SymphonyGraphEvolutionRail as PublicRail,
    )
    from openjiuwen.harness.rails import (
        TeamSymphonyGraphEvolutionRail as PublicTeamRail,
    )

    assert PublicRail is SymphonyGraphEvolutionRail
    assert PublicTeamRail is TeamSymphonyGraphEvolutionRail
