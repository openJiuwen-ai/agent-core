# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Invoke-local Symphony execution-graph evolution rails.

The rails in this module own only the production side of the graph-evolution
contract: capture one invoke, infer and model-judge occurrence edges, build a
JGF execution graph, and hand the immutable planned/execution pair to a sink.
Runtime graph aggregation and experience generation are intentionally outside
this module.
"""

from __future__ import annotations

import ast
import json
import re
import threading
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from openjiuwen.agent_evolving.trajectory.messages import DEFAULT_EVOLUTION_MESSAGE_FIELDS
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    merge_trajectories,
    read_span_error,
    read_tool_call,
    span_attributes,
    span_identity,
    trim_trajectory,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.extensions.observability import semconv as observability_semconv
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    EvolutionTriggerPoint,
    PreparedEvolutionInput,
    _InvokeCapture,
    _TeamTrajectoryCaptureMixin,
)
from openjiuwen.harness.rails.evolution.symphony_edge_evaluator import (
    SymphonyEdgeEndpointSummary,
    SymphonyEdgeEvaluationSummary,
    evaluate_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    build_model_edge_decisions,
    build_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
    project_symphony_execution_fragments,
)
from openjiuwen.harness.rails.evolution.symphony_execution_graph import (
    CapabilityIdentity,
    CapabilitySnapshotProvider,
    ExecutionOutcome,
    SymphonyGraphObservationSink,
    build_symphony_execution_graph,
    build_symphony_graph_evolution_submission,
)
from openjiuwen.symphony.interfaces.llm import SymphonyLLM

_COMPOSE_TOOL_NAME = "symphony_compose_graph"
_MAX_EDGE_CANDIDATES = 64
_CANDIDATE_PROBE_LIMIT = _MAX_EDGE_CANDIDATES + 1


@dataclass(frozen=True)
class SymphonyGraphEvolutionInput(PreparedEvolutionInput):
    """Detached, invoke-local input safe for background evolution."""

    planned_graph: dict[str, Any] | None = None
    execution_fragments: tuple[SymphonyExecutionFragment, ...] = ()
    execution_continuities: tuple[tuple[int, Trajectory], ...] = ()
    capability_snapshot: tuple[CapabilityIdentity, ...] = ()
    query: str = ""
    outcome: ExecutionOutcome = "partial"
    reason: str | None = "invoke_result_unverified"
    trace_id: str = "unknown"
    session_id: str = "unknown"
    capture_mode: Literal["agent", "team"] = "agent"
    quality_flags: tuple[str, ...] = ()
    edge_evaluator_llm: SymphonyLLM | None = field(default=None, repr=False, compare=False)
    edge_search_max_depth: int = 3


@dataclass(frozen=True)
class _CapturedToolToken:
    """One ended tool span not yet claimed by its after-tool callback."""

    call_id: str | None
    tool_name: str


@dataclass
class _SymphonyInvokeState:
    """State deliberately kept out of the shared EvolutionRail capture."""

    session_id: str
    member_id: str | None
    team_id: str | None
    trace_id: str | None
    capture_mode: Literal["agent", "team"]
    edge_evaluator_llm: SymphonyLLM | None
    edge_search_max_depth: int
    capability_snapshot: tuple[CapabilityIdentity, ...] = ()
    planned_graph: dict[str, Any] | None = None
    increments: list[Trajectory] = field(default_factory=list)
    increment_continuities: list[int] = field(default_factory=list)
    increment_span_counts: list[int] = field(default_factory=list)
    span_count: int = 0
    # Tokens are removed as callbacks claim them and the whole list dies with
    # the invoke state.  The list is capped with the trace window; identities
    # beyond that cap collapse into a count so delayed callbacks remain
    # claimable without retaining unbounded payload-derived state.
    pending_tool_tokens: list[_CapturedToolToken] = field(default_factory=list)
    discarded_pending_tool_callbacks: int = 0
    current_continuity_index: int = 0
    continuity_break_pending: bool = False
    quality_codes: list[str] = field(default_factory=list)
    drain_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


def _tool_payload(tool_result: Any) -> Mapping[str, Any] | None:
    if isinstance(tool_result, Mapping):
        return tool_result
    data = getattr(tool_result, "data", None)
    return data if isinstance(data, Mapping) else None


def _ready_planned_graph(tool_result: Any) -> dict[str, Any] | None:
    """Return a detached first-ready planned graph, otherwise ``None``."""

    if getattr(tool_result, "success", True) is False:
        return None
    payload = _tool_payload(tool_result)
    if payload is None or payload.get("success", True) is False:
        return None
    planned_graph = payload.get("planned_graph")
    if not isinstance(planned_graph, dict):
        return None
    graph = planned_graph.get("graph")
    if not isinstance(graph, dict):
        return None
    metadata = graph.get("metadata")
    if graph.get("type") != "planned_graph" or graph.get("directed") is not True:
        return None
    if not isinstance(metadata, dict) or metadata.get("status") != "ready":
        return None
    if not isinstance(graph.get("nodes"), dict) or not isinstance(graph.get("edges"), list):
        return None
    detached = deepcopy(planned_graph)
    validation_execution = build_symphony_execution_graph(
        trace_id="planned-graph-capture-validation",
        query="",
        outcome="success",
        candidates=(),
        decisions=(),
        capability_snapshot=(),
    )
    # Keep strict JSON and planned-JGF validation in the submission contract.
    build_symphony_graph_evolution_submission(detached, validation_execution)
    return detached


class SymphonyGraphEvolutionRail(EvolutionRail):
    """Produce one execution-graph submission per valid Agent invoke."""

    # The observability rail closes/exports the current callback span at 10.
    # Capture must therefore run afterwards for after hooks (and, symmetrically,
    # subscribe after the invoke root has opened in before hooks).
    priority = 5
    _SUBSCRIPTION_CATEGORIES = ("llm", "tool", "agent", "task", "message")

    def __init__(
        self,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        capability_snapshot_provider: CapabilitySnapshotProvider | None = None,
        edge_evaluator_llm: SymphonyLLM | None = None,
        observation_sink: SymphonyGraphObservationSink | None = None,
        input_consumer: Callable[[SymphonyGraphEvolutionInput], Awaitable[None]] | None = None,
        async_evolution: bool = True,
        edge_search_max_depth: int = 3,
        max_trajectory_spans: int | None = 200,
    ) -> None:
        super().__init__(
            trajectory_span_processor=trajectory_span_processor,
            async_evolution=async_evolution,
            max_trajectory_spans=max_trajectory_spans,
        )
        if capability_snapshot_provider is not None and not callable(
            getattr(capability_snapshot_provider, "snapshot_capabilities", None)
        ):
            raise TypeError("capability_snapshot_provider must provide snapshot_capabilities")
        if observation_sink is not None and not callable(getattr(observation_sink, "submit", None)):
            raise TypeError("observation_sink must provide submit")
        if input_consumer is not None and not callable(input_consumer):
            raise TypeError("input_consumer must be callable")
        if isinstance(edge_search_max_depth, bool) or not isinstance(edge_search_max_depth, int):
            raise TypeError("edge_search_max_depth must be an integer")
        self._capability_snapshot_provider = capability_snapshot_provider
        self._observation_sink = observation_sink
        self._input_consumer = input_consumer
        self._edge_evaluator_llm: SymphonyLLM | None = None
        self.update_edge_evaluator_llm(edge_evaluator_llm)
        self._edge_search_max_depth = max(0, edge_search_max_depth)
        self._symphony_states: dict[object, _SymphonyInvokeState] = {}
        self._symphony_states_lock = threading.RLock()

    @property
    def observation_sink(self) -> SymphonyGraphObservationSink | None:
        return self._observation_sink

    @property
    def input_consumer(self) -> Callable[[SymphonyGraphEvolutionInput], Awaitable[None]] | None:
        return self._input_consumer

    def update_edge_evaluator_llm(self, llm: SymphonyLLM | None) -> None:
        """Use ``llm`` only for invocations beginning after this call."""

        if llm is not None and not callable(getattr(llm, "invoke", None)):
            raise TypeError("edge_evaluator_llm must provide invoke")
        self._edge_evaluator_llm = llm

    @staticmethod
    def _subscription_categories() -> Collection[str]:
        return SymphonyGraphEvolutionRail._SUBSCRIPTION_CATEGORIES

    def _state(self, capture: _InvokeCapture | None) -> _SymphonyInvokeState | None:
        if capture is None:
            return None
        with self._symphony_states_lock:
            return self._symphony_states.get(capture.subscription)

    def _active_capture(self, capture: _InvokeCapture | None) -> bool:
        if capture is None:
            return False
        with self._subscription_lock:
            return self._active_captures.get(capture.subscription) is capture

    def _route_for_callback(self, ctx: AgentCallbackContext | None) -> tuple[str | None, str | None, str | None] | None:
        try:
            return self._capture_route(ctx)  # type: ignore[arg-type]
        except RuntimeError:
            return None

    def _resolve_capture(
        self,
        *,
        ctx: AgentCallbackContext | None = None,
        session_id: str | None = None,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> _InvokeCapture | None:
        """Resolve callbacks by invoke state and, when present, root trace."""

        if ctx is not None:
            session = getattr(ctx, "session", None)
            get_session_id = getattr(session, "get_session_id", None)
            if session_id is None and callable(get_session_id):
                session_id = str(get_session_id())
        route = self._route_for_callback(ctx)
        if isinstance(self, _TeamTrajectoryCaptureMixin) and route is None:
            return None
        route_member, route_team, route_trace = route or (None, None, None)
        member_id = member_id or route_member
        team_id = team_id or route_team

        current = self._current_capture()
        with self._subscription_lock:
            captures = tuple(self._active_captures.values())
        if current is not None and self._active_capture(current) and self._state(current) is not None:
            captures = (current,)
        ordered = ((current,) if current is not None else ()) + tuple(
            capture for capture in captures if capture is not current
        )
        matches: list[_InvokeCapture] = []
        for capture in ordered:
            state = self._state(capture)
            if state is None:
                # During ``before_invoke`` the base capture exists just before
                # the Symphony state is installed.
                if capture is current and self._active_capture(capture):
                    return capture
                continue
            if session_id is not None and state.session_id != str(session_id):
                continue
            if state.capture_mode == "team":
                if route_trace is None or state.trace_id != route_trace:
                    continue
                if team_id is not None and state.team_id != str(team_id):
                    continue
            else:
                if member_id is not None and state.member_id != str(member_id):
                    continue
                if route_trace is not None and state.trace_id is not None and state.trace_id != route_trace:
                    continue
            matches.append(capture)
        unique: list[_InvokeCapture] = []
        for capture in matches:
            if all(existing is not capture for existing in unique):
                unique.append(capture)
        return unique[0] if len(unique) == 1 else None

    def _lifecycle_capture(
        self,
        ctx: AgentCallbackContext,
        routed_capture: _InvokeCapture | None,
    ) -> _InvokeCapture | None:
        """Resolve only a capture that can be released without ambiguity."""

        current = self._current_capture()
        if self._active_capture(current):
            return current
        if self._active_capture(routed_capture):
            return routed_capture
        session = getattr(ctx, "session", None)
        get_session_id = getattr(session, "get_session_id", None)
        if not callable(get_session_id):
            return None
        session_id = str(get_session_id())
        with self._subscription_lock:
            captures = tuple(self._active_captures.values())
        matches: list[_InvokeCapture] = []
        for capture in captures:
            state = self._state(capture)
            if state is not None and state.session_id == session_id:
                matches.append(capture)
        return matches[0] if len(matches) == 1 else None

    def _cleanup_seed(self) -> _InvokeCapture | None:
        """Find a teardown target without reading callback-owned objects."""

        current = self._current_capture()
        return current if self._active_capture(current) else None

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        capture = self._current_capture()
        if capture is None or not self._active_capture(capture):
            return
        route = self._route_for_callback(ctx)
        if route is None:
            raise RuntimeError("Symphony trajectory capture route is unavailable")
        member_id, team_id, trace_id = route
        state = _SymphonyInvokeState(
            session_id=capture.session_id,
            member_id=member_id or capture.member_id,
            team_id=team_id or capture.team_id,
            trace_id=trace_id,
            capture_mode="team" if team_id is not None else "agent",
            edge_evaluator_llm=self._edge_evaluator_llm,
            edge_search_max_depth=self._edge_search_max_depth,
        )
        with self._symphony_states_lock:
            self._symphony_states[capture.subscription] = state
        provider = self._capability_snapshot_provider
        if provider is not None:
            try:
                snapshot = tuple(deepcopy(tuple(provider.snapshot_capabilities())))
                with state.lock:
                    state.capability_snapshot = snapshot
            except MemoryError:
                raise
            except Exception:
                self._remember_quality(state, ({"code": "capability_snapshot_error"},))

    def _unsubscribe_capture(self, capture: _InvokeCapture) -> None:
        try:
            super()._unsubscribe_capture(capture)
        finally:
            with self._symphony_states_lock:
                self._symphony_states.pop(capture.subscription, None)

    def uninit(self, agent: Any) -> None:
        try:
            super().uninit(agent)
        finally:
            with self._symphony_states_lock:
                self._symphony_states.clear()

    @staticmethod
    def _capture_quality_issues(trajectory: Trajectory | None) -> tuple[Mapping[str, object], ...]:
        """Accept strict JSON and legacy literal dict/list tool payloads."""

        if trajectory is None:
            return ()
        issues: list[Mapping[str, object]] = []
        indexed_pattern = re.compile(r"^(gen_ai\.(?:prompt|completion))\.(\d+)\.")
        for span in iter_spans(trajectory):
            attrs = span_attributes(span)
            indexes: dict[str, set[int]] = {}
            for key in attrs:
                match = indexed_pattern.match(str(key))
                if match:
                    indexes.setdefault(match.group(1), set()).add(int(match.group(2)))
            for base, values in indexes.items():
                if values and values != set(range(max(values) + 1)):
                    issues.append(MappingProxyType({"code": "indexed_attribute_gap", "attribute": base}))
            if span_category(span) != "tool":
                continue
            for key in (observability_semconv.GEN_AI_TOOL_INPUT, observability_semconv.GEN_AI_TOOL_OUTPUT):
                value = attrs.get(key)
                if not isinstance(value, str) or not value.strip() or value.strip()[0] not in "[{":
                    continue
                if not _is_structured_tool_payload(value):
                    issues.append(MappingProxyType({"code": "tool_payload_json_error", "attribute": key}))
        return tuple(issues)

    def _drain_for_hook(
        self,
        ctx: AgentCallbackContext,
        *,
        required_category: str | None = None,
        merge: bool = True,
        capture: _InvokeCapture | None = None,
    ) -> tuple[Trajectory | None, Trajectory | None, tuple[Mapping[str, object], ...]]:
        capture = capture or self._resolve_capture(ctx=ctx)
        state = self._state(capture)
        if capture is None or state is None:
            return super()._drain_for_hook(
                ctx,
                required_category=required_category,
                merge=merge,
                capture=capture,
            )
        # A processor drain and the registration/claim of its tool tokens are
        # one invoke-local transaction.  Parallel after-tool callbacks must
        # not observe the empty processor buffer before the first callback has
        # made the other spans in its batch claimable.
        with state.drain_lock:
            return self._drain_for_hook_locked(
                ctx,
                required_category=required_category,
                merge=merge,
                capture=capture,
                state=state,
            )

    def _drain_for_hook_locked(
        self,
        ctx: AgentCallbackContext,
        *,
        required_category: str | None,
        merge: bool,
        capture: _InvokeCapture,
        state: _SymphonyInvokeState,
    ) -> tuple[Trajectory | None, Trajectory | None, tuple[Mapping[str, object], ...]]:
        trajectory, increment, issues = super()._drain_for_hook(
            ctx,
            required_category=required_category,
            merge=merge,
            capture=capture,
        )
        typed_tool_callback = required_category == "tool" and isinstance(ctx.inputs, ToolCallInputs)
        if typed_tool_callback and increment is not None and not issues:
            self._remember_pending_tool_tokens(state, increment)
        if typed_tool_callback and (not issues or _only_missing_required_span(issues)):
            claimed = self._claim_pending_tool_token(state, ctx)
            if claimed and _only_missing_required_span(issues):
                if increment is not None and merge:
                    trajectory = self._merge_clean_increment(capture, increment)
                else:
                    trajectory = self._project_state_trajectory(capture, state)
                return trajectory, increment, ()
            if not claimed and not issues:
                trajectory = None
                issues = (MappingProxyType({"code": "missing_required_span", "category": "tool"}),)
        if required_category == "tool" and increment is None:
            if _is_spanless_framework_tool(ctx) and _only_missing_required_span(issues):
                return self._project_state_trajectory(capture, state), increment, ()
        if issues:
            self._remember_quality(state, issues)
            with state.lock:
                if state.increments:
                    state.continuity_break_pending = True
        return trajectory, increment, issues

    def _remember_pending_tool_tokens(
        self,
        state: _SymphonyInvokeState,
        increment: Trajectory,
    ) -> None:
        tokens: list[_CapturedToolToken] = []
        for span in iter_spans(increment):
            if span_category(span) != "tool":
                continue
            call = read_tool_call(span)
            # Current observability writes both the per-invocation call ID
            # and the stable ToolCard/resource ID.  ``read_tool_call`` keeps
            # legacy compatibility by preferring the latter for ``id``.  Only
            # the canonical call-ID attribute is safe for exact callback
            # correlation; its absence deliberately enables name fallback.
            call_id = _nonempty_text(span_attributes(span).get(observability_semconv.GEN_AI_TOOL_CALL_ID))
            tool_name = _nonempty_text(call.get("name")) or ""
            if call_id is not None or tool_name:
                tokens.append(_CapturedToolToken(call_id, tool_name))
        if tokens:
            with state.lock:
                state.pending_tool_tokens.extend(tokens)
                limit = self._max_trajectory_spans
                if limit is not None and len(state.pending_tool_tokens) > limit:
                    discarded = len(state.pending_tool_tokens) - limit
                    del state.pending_tool_tokens[:discarded]
                    state.discarded_pending_tool_callbacks += discarded
                    if "truncated_trace" not in state.quality_codes:
                        state.quality_codes.append("truncated_trace")

    @staticmethod
    def _claim_pending_tool_token(
        state: _SymphonyInvokeState,
        ctx: AgentCallbackContext,
    ) -> bool:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return False
        callback_id = _tool_call_id(inputs.tool_call)
        tool_name = _nonempty_text(inputs.tool_name) or ""
        with state.lock:
            if callback_id is not None:
                for index, token in enumerate(state.pending_tool_tokens):
                    if token.call_id == callback_id:
                        state.pending_tool_tokens.pop(index)
                        return True
                # Preserve exact-ID tokens for their real callbacks.  Name
                # fallback is safe only when the captured span had no ID.
                for index, token in enumerate(state.pending_tool_tokens):
                    if token.call_id is None and tool_name and token.tool_name == tool_name:
                        state.pending_tool_tokens.pop(index)
                        return True
            elif tool_name:
                for index, token in enumerate(state.pending_tool_tokens):
                    if token.tool_name == tool_name:
                        state.pending_tool_tokens.pop(index)
                        return True
            if state.discarded_pending_tool_callbacks:
                state.discarded_pending_tool_callbacks -= 1
                return True
        return False

    def _merge_clean_increment(self, capture: _InvokeCapture, increment: Trajectory) -> Trajectory:
        projected = super()._merge_clean_increment(capture, increment)
        state = self._state(capture)
        if state is None:
            return projected
        detached = Trajectory.from_otlp(increment.to_otlp())
        detached_span_count = sum(1 for _ in iter_spans(detached))
        with state.lock:
            if state.continuity_break_pending:
                if state.increments:
                    state.current_continuity_index += 1
                state.continuity_break_pending = False
            state.increments.append(detached)
            state.increment_continuities.append(state.current_continuity_index)
            state.increment_span_counts.append(detached_span_count)
            state.span_count += detached_span_count
            self._trim_private_history(state)
        return projected

    def _trim_private_history(self, state: _SymphonyInvokeState) -> None:
        """Keep the invoke-private source bounded like the shared clean window."""

        limit = self._max_trajectory_spans
        if limit is None or state.span_count <= limit:
            return
        overflow = state.span_count - limit
        while overflow and state.increments:
            oldest_count = state.increment_span_counts[0]
            if oldest_count <= overflow:
                state.increments.pop(0)
                state.increment_continuities.pop(0)
                state.increment_span_counts.pop(0)
                state.span_count -= oldest_count
                overflow -= oldest_count
                continue
            retained_count = oldest_count - overflow
            state.increments[0] = trim_trajectory(state.increments[0], retained_count)
            state.increment_span_counts[0] = retained_count
            state.span_count -= overflow
            overflow = 0
        if "truncated_trace" not in state.quality_codes:
            state.quality_codes.append("truncated_trace")

    @staticmethod
    def _remember_quality(
        state: _SymphonyInvokeState,
        issues: Collection[Mapping[str, object]],
    ) -> None:
        codes: list[str] = []
        for issue in issues:
            try:
                codes.append(str(issue.get("code") or "capture_quality_issue"))
            except MemoryError:
                raise
            except Exception:
                codes.append("capture_quality_issue")
        with state.lock:
            for code in codes:
                if code not in state.quality_codes:
                    state.quality_codes.append(code)

    def _project_state_trajectory(
        self, capture: _InvokeCapture | None, state: _SymphonyInvokeState
    ) -> Trajectory | None:
        if capture is None:
            return None
        with state.lock:
            increments = tuple(state.increments)
        if not increments:
            return None
        projected = merge_trajectories(*increments)
        return self._with_scope_metadata(projected, self._scope_metadata(capture))

    def _project_state_continuities(
        self, capture: _InvokeCapture, state: _SymphonyInvokeState
    ) -> tuple[tuple[int, Trajectory], ...]:
        with state.lock:
            pairs = tuple(zip(state.increment_continuities, state.increments))
        grouped: dict[int, list[Trajectory]] = {}
        for continuity, increment in pairs:
            grouped.setdefault(continuity, []).append(increment)
        return tuple(
            (
                continuity,
                self._with_scope_metadata(
                    merge_trajectories(*increments),
                    self._scope_metadata(capture),
                ),
            )
            for continuity, increments in grouped.items()
        )

    async def _on_after_tool_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        del trajectory
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs) or inputs.tool_name != _COMPOSE_TOOL_NAME:
            return
        capture = self._resolve_capture(ctx=ctx)
        state = self._state(capture)
        if state is None:
            return
        try:
            planned_graph = _ready_planned_graph(inputs.tool_result)
        except MemoryError:
            raise
        except (TypeError, ValueError, RecursionError):
            self._remember_quality(state, ({"code": "planned_graph_invalid"},))
            return
        except Exception:
            self._remember_quality(state, ({"code": "planned_graph_capture_error"},))
            return
        if planned_graph is None:
            try:
                payload = _tool_payload(inputs.tool_result)
                has_planned_graph = isinstance(payload, Mapping) and "planned_graph" in payload
            except MemoryError:
                raise
            except Exception:
                self._remember_quality(state, ({"code": "planned_graph_capture_error"},))
                return
            if has_planned_graph:
                self._remember_quality(state, ({"code": "planned_graph_invalid"},))
            return
        with state.lock:
            if state.planned_graph is None:
                state.planned_graph = planned_graph

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Drain, prepare and always release the lifecycle capture."""

        cleanup_capture = self._cleanup_seed()
        try:
            routed_capture = self._resolve_capture(ctx=ctx)
            if self._active_capture(routed_capture):
                cleanup_capture = routed_capture
            lifecycle_capture = self._lifecycle_capture(ctx, routed_capture)
            if lifecycle_capture is not None:
                cleanup_capture = lifecycle_capture
            if lifecycle_capture is None:
                await self._on_after_invoke(ctx, None)
                return
            if routed_capture is not lifecycle_capture:
                return
            self._drain_for_hook(ctx, capture=lifecycle_capture)
            state = self._state(lifecycle_capture)
            trajectory = self._project_state_trajectory(lifecycle_capture, state) if state is not None else None
            await self._on_after_invoke(ctx, trajectory)
            if (
                trajectory is not None
                and self._evolution_trigger == EvolutionTriggerPoint.AFTER_INVOKE
                and self._allow_evolution_trigger(EvolutionTriggerPoint.AFTER_INVOKE, ctx)
            ):
                await self._trigger_evolution(trajectory, ctx)
                await self._on_after_evolution_triggered(trajectory, ctx)
        finally:
            if cleanup_capture is not None:
                self._unsubscribe_capture(cleanup_capture)

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        del ctx
        capture = self._current_capture()
        state = self._state(capture)
        if state is None:
            return
        with state.lock:
            clean_spans = state.span_count
            issues = tuple(state.quality_codes)
            planned_ready = state.planned_graph is not None
        logger.info(
            "[SymphonyGraphEvolutionRail] trace summary scope=%s session=%s root_trace_id=%s "
            "clean_spans=%s planned_graph_ready=%s quality_issue_codes=%s",
            state.capture_mode,
            state.session_id,
            state.trace_id or "unknown",
            clean_spans,
            planned_ready,
            ",".join(issues) if issues else "none",
        )

    def _allow_evolution_trigger(
        self,
        trigger_point: EvolutionTriggerPoint,
        ctx: AgentCallbackContext,
    ) -> bool:
        del trigger_point, ctx
        return self._observation_sink is not None or self._input_consumer is not None

    async def _prepare_evolution_input(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> SymphonyGraphEvolutionInput | None:
        del trajectory
        capture = self._resolve_capture(ctx=ctx)
        state = self._state(capture)
        if capture is None or state is None:
            return None
        invoke_trajectory = self._project_state_trajectory(capture, state)
        if invoke_trajectory is None:
            return None
        continuities = self._project_state_continuities(capture, state)
        try:
            fragments = project_symphony_execution_fragments(
                continuities,
                team_members_only=state.capture_mode == "team",
            )
        except MemoryError:
            raise
        except Exception:
            self._remember_quality(state, ({"code": "execution_fragment_projection_error"},))
            fragments = ()
        messages = tuple(
            deepcopy(
                self._trajectory_to_messages(
                    invoke_trajectory,
                    fields=DEFAULT_EVOLUTION_MESSAGE_FIELDS,
                )
            )
        )
        query, outcome, reason = _invoke_result_contract(ctx)
        with state.lock:
            planned_graph = deepcopy(state.planned_graph)
            snapshot = tuple(deepcopy(state.capability_snapshot))
            quality = tuple(state.quality_codes)
            llm = state.edge_evaluator_llm
            depth = state.edge_search_max_depth
            trace_id = state.trace_id
        return SymphonyGraphEvolutionInput(
            trajectory=Trajectory.from_otlp(invoke_trajectory.to_otlp()),
            messages=messages,
            planned_graph=planned_graph,
            execution_fragments=tuple(fragments),
            execution_continuities=tuple(continuities),
            capability_snapshot=snapshot,
            query=query,
            outcome=outcome,
            reason=reason,
            trace_id=trace_id or _first_fragment_trace(fragments) or "unknown",
            session_id=state.session_id,
            capture_mode=state.capture_mode,
            quality_flags=quality,
            edge_evaluator_llm=llm,
            edge_search_max_depth=depth,
        )

    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        if not isinstance(prepared, SymphonyGraphEvolutionInput):
            raise TypeError("prepared must be a SymphonyGraphEvolutionInput")
        if self._input_consumer is not None:
            try:
                await self._input_consumer(_detach_prepared_input(prepared))
            except Exception as exc:
                logger.warning(
                    "[SymphonyGraphEvolutionRail] input consumer failed (%s)",
                    type(exc).__name__,
                )
        if self._observation_sink is None:
            return

        candidates_probe = build_symphony_edge_candidates(
            prepared.execution_fragments,
            prepared.execution_continuities,
            planned_graph=prepared.planned_graph,
            edge_search_max_depth=prepared.edge_search_max_depth,
            max_candidates=_CANDIDATE_PROBE_LIMIT,
            include_team_member_pairs=prepared.capture_mode == "team",
        )
        candidate_truncated = len(candidates_probe) > _MAX_EDGE_CANDIDATES
        candidates = candidates_probe[:_MAX_EDGE_CANDIDATES]
        decisions = build_model_edge_decisions(candidates)
        decisions = await evaluate_symphony_edge_candidates(
            llm=prepared.edge_evaluator_llm,
            query=prepared.query,
            candidates=candidates,
            decisions=decisions,
            summaries=_build_edge_summaries(candidates, prepared.execution_continuities),
        )
        flags = set(prepared.quality_flags)
        if candidate_truncated:
            flags.add("edge_candidates_truncated")
        execution_graph = build_symphony_execution_graph(
            trace_id=prepared.trace_id or "unknown",
            query=prepared.query,
            outcome=prepared.outcome,
            reason=prepared.reason,
            candidates=candidates,
            decisions=decisions,
            capability_snapshot=prepared.capability_snapshot,
            quality_flags=tuple(sorted(flags)),
        )
        try:
            submission = build_symphony_graph_evolution_submission(prepared.planned_graph, execution_graph)
        except MemoryError:
            raise
        except Exception as exc:
            logger.warning(
                "[SymphonyGraphEvolutionRail] invalid planned graph omitted (%s)",
                type(exc).__name__,
            )
            try:
                submission = build_symphony_graph_evolution_submission(None, execution_graph)
            except MemoryError:
                raise
            except Exception as fallback_exc:
                logger.warning(
                    "[SymphonyGraphEvolutionRail] graph submission build failed (%s)",
                    type(fallback_exc).__name__,
                )
                return
        try:
            await self._observation_sink.submit(submission)
        except Exception as exc:
            logger.warning(
                "[SymphonyGraphEvolutionRail] observation sink failed (%s)",
                type(exc).__name__,
            )


def _is_structured_tool_payload(value: str) -> bool:
    try:
        json.loads(value)
        return True
    except ValueError:
        pass
    try:
        decoded = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return False
    return isinstance(decoded, (dict, list))


def _only_missing_required_span(issues: Sequence[Mapping[str, object]]) -> bool:
    return bool(issues) and all(issue.get("code") == "missing_required_span" for issue in issues)


def _is_spanless_framework_tool(ctx: AgentCallbackContext) -> bool:
    inputs = ctx.inputs
    return isinstance(inputs, ToolCallInputs) and inputs.tool_name == _COMPOSE_TOOL_NAME


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, Mapping):
        return _nonempty_text(tool_call.get("id"))
    return _nonempty_text(getattr(tool_call, "id", None))


def _invoke_result_contract(ctx: AgentCallbackContext) -> tuple[str, ExecutionOutcome, str | None]:
    inputs = ctx.inputs
    query = inputs.query if isinstance(inputs, InvokeInputs) else ""
    query_text = query if isinstance(query, str) else ""
    result = inputs.result if isinstance(inputs, InvokeInputs) else None
    if isinstance(result, Mapping):
        status = str(result.get("status") or "").lower()
        result_type = str(result.get("result_type") or "").lower()
        if result.get("success") is False or status in {"failed", "failure", "error"} or result_type == "error":
            return query_text, "failed", "invoke_result_failed"
        if status == "partial":
            return query_text, "partial", "invoke_result_partial"
        if result.get("success") is True or status in {"success", "succeeded", "completed"} or result_type == "answer":
            return query_text, "success", None
    return query_text, "partial", "invoke_result_unverified"


def _first_fragment_trace(fragments: Sequence[SymphonyExecutionFragment]) -> str | None:
    return next((fragment.trace_id for fragment in fragments if fragment.trace_id), None)


def _detach_prepared_input(prepared: SymphonyGraphEvolutionInput) -> SymphonyGraphEvolutionInput:
    """Give a diagnostic consumer an independent view of mutable payloads."""

    return SymphonyGraphEvolutionInput(
        trajectory=Trajectory.from_otlp(prepared.trajectory.to_otlp()),
        messages=tuple(deepcopy(prepared.messages)),
        skill_name=prepared.skill_name,
        planned_graph=deepcopy(prepared.planned_graph),
        execution_fragments=prepared.execution_fragments,
        execution_continuities=tuple(
            (index, Trajectory.from_otlp(trajectory.to_otlp())) for index, trajectory in prepared.execution_continuities
        ),
        capability_snapshot=prepared.capability_snapshot,
        query=prepared.query,
        outcome=prepared.outcome,
        reason=prepared.reason,
        trace_id=prepared.trace_id,
        session_id=prepared.session_id,
        capture_mode=prepared.capture_mode,
        quality_flags=prepared.quality_flags,
        edge_evaluator_llm=prepared.edge_evaluator_llm,
        edge_search_max_depth=prepared.edge_search_max_depth,
    )


def _build_edge_summaries(
    candidates: Sequence[SymphonyEdgeCandidate],
    continuities: Sequence[tuple[int, Trajectory]],
) -> dict[str, SymphonyEdgeEvaluationSummary]:
    """Build bounded local summaries; candidate provenance is never included."""

    span_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for _, trajectory in continuities:
        for span in iter_spans(trajectory):
            identity = span_identity(span)
            if identity is not None:
                span_map[identity] = span

    def endpoint(fragment: SymphonyExecutionFragment, *, keep_last: bool) -> SymphonyEdgeEndpointSummary:
        events: list[str] = []
        errors: list[str] = []
        for span_id in fragment.span_ids:
            span = span_map.get((fragment.trace_id, span_id))
            if span is None:
                continue
            error = read_span_error(span)
            if error:
                errors.append(_truncate_trace_text(str(error), 256))
            call = read_tool_call(span)
            if isinstance(call, Mapping) and call:
                safe = {
                    key: _compact_trace_value(call[key])
                    for key in ("name", "id", "input", "output", "error")
                    if key in call
                }
                if safe:
                    events.append(_truncate_trace_text(json.dumps(safe, ensure_ascii=False, sort_keys=True), 384))
        selected = events[-3:] if keep_last else events[:3]
        slots = [*selected, "", "", ""][:3]
        error_text = errors[-1] if keep_last and errors else errors[0] if errors else ""
        return SymphonyEdgeEndpointSummary(
            fragment=slots[0],
            capability=f"{fragment.capability_type}:{fragment.capability_name or ''}",
            input=slots[1],
            output=slots[2],
            error=error_text,
        )

    return {
        candidate.candidate_id: SymphonyEdgeEvaluationSummary(
            endpoint_a=endpoint(candidate.source_fragment, keep_last=True),
            endpoint_b=endpoint(candidate.target_fragment, keep_last=False),
        )
        for candidate in candidates
    }


def _truncate_trace_text(value: str, max_bytes: int) -> str:
    return value.encode("utf-8", errors="replace")[:max_bytes].decode("utf-8", errors="ignore")


def _compact_trace_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>"
            if any(token in str(key).lower() for token in ("base64", "binary", "bytes"))
            else _compact_trace_value(item)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, (list, tuple)):
        return [_compact_trace_value(item) for item in value[:12]]
    if isinstance(value, bytes):
        return "<redacted>"
    if isinstance(value, str):
        if len(value) > 256 and all(char.isalnum() or char in "+/=_-" for char in value[:128]):
            return "<redacted>"
        return _truncate_trace_text(value, 256)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate_trace_text(str(value), 256)


class TeamSymphonyGraphEvolutionRail(_TeamTrajectoryCaptureMixin, SymphonyGraphEvolutionRail):
    """Produce a graph from the active Team root trace and member spans."""

    _DEFAULT_MEMBER_ROLE = "leader"


__all__ = [
    "SymphonyGraphEvolutionInput",
    "SymphonyGraphEvolutionRail",
    "TeamSymphonyGraphEvolutionRail",
]
