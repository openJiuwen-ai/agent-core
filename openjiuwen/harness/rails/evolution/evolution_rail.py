# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""EvolutionRail: Base class for all evolution rails.

All evolution rails inherit from this class and automatically get
trajectory collection capability. Subclasses override extension points
to implement evolution algorithms.

Core design:
- Trajectory collection is automatic (handled by base class)
- Extension points: _on_before_invoke, _on_after_model_call,
  _on_after_tool_call, _on_after_invoke, run_evolution
- Evolution trigger is configurable via evolution_trigger parameter
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
import threading
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Collection, List, Mapping, Optional, Union

from openjiuwen.agent_evolving.trajectory.messages import (
    DEFAULT_EVOLUTION_MESSAGE_FIELDS,
    MessageField,
    trajectory_to_messages,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import (
    MEMBER_ID,
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    iter_spans,
    merge_trajectories,
    span_attributes,
    span_sort_key,
    trim_trajectory,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.extensions.observability import semconv as observability_semconv
from openjiuwen.extensions.observability.span_context import get_root_span
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.evolution.contracts import EvolutionHostEventMeta


def _split_response_token_fields(
    response: Any,
) -> tuple[Any, Optional[list], Optional[list], Optional[Any]]:
    """Lift token-level fields out of an LLM response.

    Returns ``(response_for_detail, prompt_token_ids, completion_token_ids,
    logprobs)``. The returned ``response_for_detail`` has those three
    fields stripped to avoid duplicate storage in the trajectory.

    The response is typically an ``AssistantMessage`` (Pydantic) carrying
    ``prompt_token_ids`` / ``completion_token_ids`` / ``logprobs`` as
    direct attributes (see ``AssistantMessage.model_dump``). Dicts are
    also accepted; other shapes are passed through untouched.
    """
    if response is None:
        return None, None, None, None
    response_dict: Any = response
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            response_dict = dumped
    if not isinstance(response_dict, dict):
        return response, None, None, None
    # ``ModelCallInputs.response`` may be a caller-owned dict.  Enrichment is
    # deliberately immutable, so never pop fields from the callback object.
    response_dict = deepcopy(response_dict)
    prompt_token_ids = response_dict.pop("prompt_token_ids", None)
    completion_token_ids = response_dict.pop("completion_token_ids", None)
    logprobs = response_dict.pop("logprobs", None)
    return response_dict, prompt_token_ids, completion_token_ids, logprobs


def _normalize_member_role(role: Any) -> Optional[str]:
    """Return a stable string value for a team member role."""
    if role is None:
        return None
    role_value = getattr(role, "value", role)
    if role_value is None:
        return None
    role_text = str(role_value)
    return role_text or None


def _normalize_skill_names(raw: Optional[Union[str, list[str]]]) -> set[str]:
    """Normalize skill names into a set.

    A string is treated as a single skill name; a list is treated as multiple names.
    """
    if raw is None:
        return set()
    if isinstance(raw, str):
        name = raw.strip()
        return {name} if name else set()
    if isinstance(raw, list):
        return {name.strip() for name in raw if name.strip()}
    return set()


@dataclass
class _InvokeCapture:
    """State bound to one callback context and one processor subscription."""

    subscription: object
    scope_key: tuple[str, ...]
    session_id: str
    member_id: str | None
    team_id: str | None = None
    context_token: Any = None


class _TeamTrajectoryCaptureMixin:
    """Route one Agent rail subscription to the active Team trace.

    Team skill rails stay mounted on the leader Agent, but their execution
    evidence is team-scoped.  The active root span is the only source of truth
    for both the trace route and the team identity; silently falling back to a
    local Agent subscription would lose member spans and produce a misleading
    clean window.
    """

    _TEAM_SUBSCRIPTION_CATEGORIES = (
        "llm",
        "tool",
        "agent",
        "task",
        "message",
        "member",
        "team",
    )

    @staticmethod
    def _team_span_identity() -> tuple[str, str]:
        """Return the active root trace id and team name, or fail explicitly."""

        root = get_root_span()
        if root is None:
            raise RuntimeError("team trajectory capture requires an active root span")

        recording = getattr(root, "is_recording", None)
        try:
            recording = recording() if callable(recording) else recording
        except Exception as exc:
            raise RuntimeError("team trajectory capture requires a recording root span") from exc
        if not recording:
            raise RuntimeError("team trajectory capture requires a recording root span")

        context = getattr(root, "context", None)
        trace_id = getattr(context, "trace_id", None)
        if not isinstance(trace_id, int) or trace_id <= 0:
            raise RuntimeError("team trajectory capture requires a non-zero trace_id")

        attributes = getattr(root, "attributes", None) or {}
        try:
            team_name = attributes.get(observability_semconv.AT_TEAM_NAME)
        except AttributeError as exc:
            raise RuntimeError("team trajectory capture requires AT_TEAM_NAME on the root span") from exc
        team_id = str(team_name).strip() if team_name is not None else ""
        if not team_id:
            raise RuntimeError("team trajectory capture requires AT_TEAM_NAME on the root span")
        return f"{trace_id:032x}", team_id

    @staticmethod
    def _subscription_categories() -> Collection[str]:
        return _TeamTrajectoryCaptureMixin._TEAM_SUBSCRIPTION_CATEGORIES

    def _capture_route(self, ctx: AgentCallbackContext) -> tuple[str | None, str | None, str]:
        del ctx
        trace_id, team_id = self._team_span_identity()
        return None, team_id, trace_id

    @staticmethod
    def _scope_key(
        *,
        session_id: str,
        member_id: str | None,
        team_id: str | None,
    ) -> tuple[str, ...]:
        del member_id
        if not team_id:
            raise RuntimeError("team trajectory capture requires a team_id")
        return ("team", team_id, str(session_id))


@dataclass(frozen=True)
class PreparedEvolutionInput:
    """Detached input shared by synchronous and background evolution paths."""

    trajectory: Trajectory
    messages: tuple[dict[str, Any], ...]
    skill_name: str | None = None


class EvolutionTriggerPoint(Enum):
    """Configurable trigger points for evolution in EvolutionRail."""

    AFTER_INVOKE = "after_invoke"
    AFTER_MODEL_CALL = "after_model_call"
    AFTER_TOOL_CALL = "after_tool_call"
    AFTER_TASK_ITERATION = "after_task_iteration"
    NONE = "none"


class EvolutionRail(DeepAgentRail):
    """Base class for all evolution rails.

    Inheriting this class provides automatic trajectory collection.
    Subclasses should override one or more extension points:
      - _on_before_invoke(ctx): Initialization at invoke start
      - _on_after_model_call(ctx, trajectory): Updates after LLM calls
      - _on_after_tool_call(ctx, trajectory): Updates after tool calls
      - _on_after_invoke(ctx, trajectory): Custom logic after final draining
      - _on_after_task_iteration(ctx, trajectory): Updates after each task-loop iteration
      - run_evolution(prepared): Called when evolution_trigger fires

    The evolution trigger point is configurable via ``evolution_trigger``.
    """

    priority = 60  # Lower than security rails, higher than user rails
    _DEFAULT_MEMBER_ROLE: Optional[str] = None

    def __init__(
        self,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        disabled_skills: Optional[Union[str, list[str]]] = None,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        max_trajectory_spans: Optional[int] = 200,
    ):
        """Initialize EvolutionRail.

        Args:
            max_trajectory_spans: Optional maximum number of recent spans retained
                in each scope-local clean window.
            evolution_trigger: When to automatically trigger run_evolution.
                AFTER_INVOKE (default): after invoke completes
                AFTER_TASK_ITERATION: after each task-loop iteration, before next round
                AFTER_MODEL_CALL: after each model call
                AFTER_TOOL_CALL: after each tool call
                NONE: subclass triggers manually via run_evolution()
            async_evolution: When True (default), run_evolution runs in a background task
                after snapshotting ctx data. When False, run_evolution runs synchronously
                with the active ctx (backward-compatible).
            max_concurrent_evolution: Max concurrent run_evolution executions.
                Limits LLM competition with the main agent flow. Default is 1.
            disabled_skills: Optional deny-list of skill names excluded from self-optimization.
                Supports a single skill name (str) or multiple names (list[str]).
            trajectory_span_processor: Required shared observability processor.
        """
        super().__init__()
        if not isinstance(trajectory_span_processor, TrajectorySpanProcessor):
            raise TypeError("trajectory_span_processor must be a TrajectorySpanProcessor")
        if max_trajectory_spans is not None and max_trajectory_spans <= 0:
            raise ValueError("max_trajectory_spans must be positive or None")
        self._trajectory_span_processor = trajectory_span_processor
        self._max_trajectory_spans = max_trajectory_spans
        self._evolution_trigger = evolution_trigger
        self._disabled_skills: set[str] = _normalize_skill_names(disabled_skills)
        self._member_role: Optional[str] = None
        self._scope_windows: dict[tuple[str, ...], Trajectory] = {}
        self._scope_locks: dict[tuple[str, ...], threading.RLock] = {}
        self._window_lock = threading.RLock()
        self._subscription_lock = threading.RLock()
        self._active_captures: dict[object, _InvokeCapture] = {}
        self._invoke_capture: ContextVar[_InvokeCapture | None] = ContextVar(
            f"{type(self).__name__}.trajectory_capture", default=None
        )

        self._async_evolution = async_evolution
        self._bg_tasks: set[BackgroundTask] = set()
        self._pending_host_events: list[OutputSchema] = []
        self._evolution_sem = asyncio.Semaphore(max_concurrent_evolution)

    @property
    def trajectory_span_processor(self) -> TrajectorySpanProcessor:
        """Return the explicitly injected observability processor."""

        return self._trajectory_span_processor

    @property
    def disabled_skills(self) -> set[str]:
        """Set of skill names excluded from self-optimization."""
        return self._disabled_skills

    def uninit(self, agent: Any) -> None:
        """Release subscriptions and in-memory clean windows owned by this rail."""
        with self._subscription_lock:
            captures = list(self._active_captures.values())
            self._active_captures.clear()
        for capture in captures:
            self._trajectory_span_processor.unsubscribe(capture.subscription)
        self._invoke_capture.set(None)
        with self._window_lock:
            self._scope_windows.clear()
            self._scope_locks.clear()
        super().uninit(agent)

    @classmethod
    def _normalize_name_set(cls, raw: Optional[Union[str, list[str]]]) -> set[str]:
        """Normalize skill names into a set."""
        return _normalize_skill_names(raw)

    def set_member_role(self, member_role: Any) -> None:
        """Set the role copied to projected resource metadata."""

        self._member_role = _normalize_member_role(member_role)

    # ---- Trajectory collection (final, subclasses should not override) ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Create one invoke-local processor subscription."""
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs):
            return

        session_id = self._resolve_trajectory_session_id(ctx, inputs)
        if not session_id:
            raise ValueError("trajectory session_id is required")
        member_id, team_id, trace_id = self._capture_route(ctx)
        scope_key = self._scope_key(session_id=session_id, member_id=member_id, team_id=team_id)
        subscription = self._trajectory_span_processor.subscribe(
            include_span_categories=self._subscription_categories(),
            trace_id=trace_id,
        )
        capture = _InvokeCapture(
            subscription=subscription,
            scope_key=scope_key,
            session_id=session_id,
            member_id=member_id,
            team_id=team_id,
        )
        capture.context_token = self._invoke_capture.set(capture)
        with self._subscription_lock:
            self._active_captures[subscription] = capture
        try:
            await self._on_before_invoke(ctx)
        except Exception:
            self._unsubscribe_capture(capture)
            raise

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Drain the current subscription, enrich RL fields, then hook."""
        inputs = ctx.inputs
        capture = self._resolve_capture(ctx=ctx)
        trajectory, increment, issues = self._drain_for_hook(
            ctx,
            required_category="llm",
            merge=False,
            capture=capture,
        )
        if capture is not None and isinstance(inputs, ModelCallInputs):
            if increment is not None and not issues:
                enriched_increment = self._enrich_latest_llm(increment, inputs.response)
                trajectory = self._merge_clean_increment(capture, enriched_increment)
        await self._on_after_model_call(ctx, trajectory)
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_MODEL_CALL and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_MODEL_CALL, ctx
        ):
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Drain the current subscription, then invoke the tool hook."""
        trajectory, _, _ = self._drain_for_hook(ctx, required_category="tool")
        await self._on_after_tool_call(ctx, trajectory)
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TOOL_CALL and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_TOOL_CALL, ctx
        ):
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Drain the current subscription, then invoke the iteration hook."""
        trajectory, _, _ = self._drain_for_hook(ctx)
        await self._on_after_task_iteration(ctx, trajectory)

        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TASK_ITERATION and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_TASK_ITERATION, ctx
        ):
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Final-drain an invoke and release its isolated subscription."""
        capture = self._resolve_capture(ctx=ctx)
        try:
            trajectory, _, _ = self._drain_for_hook(ctx, capture=capture)
            await self._on_after_invoke(ctx, trajectory)

            # Trigger evolution if configured for after_invoke
            if self._evolution_trigger == EvolutionTriggerPoint.AFTER_INVOKE and self._allow_evolution_trigger(
                EvolutionTriggerPoint.AFTER_INVOKE, ctx
            ):
                if trajectory is not None:
                    await self._trigger_evolution(trajectory, ctx)
                    await self._on_after_evolution_triggered(trajectory, ctx)
        finally:
            if capture is not None:
                self._unsubscribe_capture(capture)

    # ---- Trajectory helper methods ----

    @staticmethod
    def _resolve_trajectory_session_id(
        ctx: AgentCallbackContext,
        inputs: InvokeInputs,
    ) -> str:
        """Resolve the runtime session id used for trajectory accumulation."""
        session = getattr(ctx, "session", None)
        if session is not None and hasattr(session, "get_session_id"):
            return str(session.get_session_id())
        return inputs.conversation_id or ""

    @staticmethod
    def _subscription_categories() -> Collection[str]:
        """Return categories selected by this rail's invoke subscription."""

        return ("llm", "tool")

    def _capture_route(self, ctx: AgentCallbackContext) -> tuple[str | None, str | None, str | None]:
        """Resolve one internally consistent subscription and scope route."""

        team_id = getattr(ctx, "team_id", None)
        if team_id is None:
            team = getattr(ctx, "team", None)
            team_id = getattr(team, "id", None) if team is not None else None

        member_id = None
        session = getattr(ctx, "session", None)
        get_agent_id = getattr(session, "get_agent_id", None)
        if callable(get_agent_id):
            try:
                member_id = get_agent_id()
            except Exception:
                member_id = None
        if not member_id:
            agent_card = getattr(getattr(ctx, "agent", None), "card", None)
            member_id = getattr(agent_card, "id", None)
        return (
            str(member_id) if member_id else None,
            str(team_id) if team_id else None,
            self._single_agent_trace_id(),
        )

    @staticmethod
    def _single_agent_trace_id() -> str | None:
        """Return an active single-agent root trace for cross-task routing."""
        root = get_root_span()
        if root is None or not str(getattr(root, "name", "")).startswith("agent."):
            return None
        recording = getattr(root, "is_recording", None)
        if callable(recording):
            try:
                recording = recording()
            except Exception:
                recording = False
        if not recording:
            return None
        trace_id = getattr(getattr(root, "context", None), "trace_id", None)
        if not isinstance(trace_id, int) or trace_id <= 0:
            return None
        return f"{trace_id:032x}"

    @staticmethod
    def _scope_key(
        *,
        session_id: str,
        member_id: str | None,
        team_id: str | None,
    ) -> tuple[str, ...]:
        """Return the clean-window key for one invoke."""

        del team_id
        return ("agent", session_id, member_id or "")

    def _scope_lock(self, key: tuple[str, ...]) -> threading.RLock:
        with self._window_lock:
            return self._scope_locks.setdefault(key, threading.RLock())

    def _scope_metadata(self, capture: _InvokeCapture) -> dict[str, Any]:
        """Build canonical resource metadata for one captured scope."""

        return self._trajectory_metadata(
            session_id=capture.session_id,
            member_id=capture.member_id,
            team_id=capture.team_id,
        )

    def _trajectory_metadata(
        self,
        *,
        session_id: str,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """Build metadata shared by callback projections and the public getter."""

        metadata: dict[str, Any] = {
            TRAJECTORY_ID: str(uuid.uuid4()),
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            TRAJECTORY_SOURCE: "online",
            SESSION_ID: str(session_id),
        }
        if member_id:
            metadata[MEMBER_ID] = str(member_id)
        if team_id:
            metadata[TEAM_ID] = str(team_id)
        if self._member_role:
            metadata["agentteam.agent.role"] = self._member_role
        return metadata

    @staticmethod
    def _with_scope_metadata(trajectory: Trajectory, metadata: Mapping[str, Any]) -> Trajectory:
        """Replace producer resource attributes with the canonical envelope."""

        payload = trajectory.to_otlp()
        for resource_span in payload.get("resourceSpans") or []:
            resource = resource_span.setdefault("resource", {})
            resource["attributes"] = attributes_from_map(metadata)
        return Trajectory.from_otlp(payload)

    def _project_window(self, capture: _InvokeCapture) -> Trajectory | None:
        """Return an immutable, detached snapshot with canonical metadata."""

        with self._scope_lock(capture.scope_key):
            window = self._scope_windows.get(capture.scope_key)
            if window is None:
                return None
            payload = window.to_otlp()
        projected = Trajectory.from_otlp(payload)
        return self._with_scope_metadata(projected, self._scope_metadata(capture))

    def get_trajectory(
        self,
        *,
        session_id: str,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> Trajectory | None:
        """Return the current clean window for an Agent or Team scope."""

        if team_id is not None and member_id is not None:
            raise ValueError("member_id must be omitted for a Team trajectory scope")
        key = (
            ("team", str(team_id), str(session_id))
            if team_id is not None
            else ("agent", str(session_id), str(member_id or ""))
        )
        with self._scope_lock(key):
            window = self._scope_windows.get(key)
            if window is None:
                return None
            projected = Trajectory.from_otlp(window.to_otlp())
        return self._with_scope_metadata(
            projected,
            self._trajectory_metadata(
                session_id=str(session_id),
                member_id=member_id if team_id is None else None,
                team_id=team_id,
            ),
        )

    def _current_capture(self) -> _InvokeCapture | None:
        return self._invoke_capture.get()

    @staticmethod
    def _capture_matches(
        capture: _InvokeCapture,
        *,
        session_id: str | None,
        member_id: str | None,
        team_id: str | None,
    ) -> bool:
        """Return whether a capture matches every provided scope locator."""
        if session_id is not None and capture.session_id != str(session_id):
            return False
        if member_id is not None and capture.member_id != str(member_id):
            return False
        if team_id is not None and capture.team_id != str(team_id):
            return False
        return True

    def _find_active_capture(self, scope_key: tuple[str, ...]) -> _InvokeCapture | None:
        """Return the unique active capture for an exact scope key."""

        with self._subscription_lock:
            matches = [
                capture
                for capture in self._active_captures.values()
                if capture.scope_key == scope_key
            ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "[%s] multiple active trajectory captures match scope=%s",
                type(self).__name__,
                scope_key,
            )
        return None

    def _resolve_capture(
        self,
        *,
        ctx: AgentCallbackContext | None = None,
        session_id: str | None = None,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> _InvokeCapture | None:
        """Resolve an invoke capture locally or by its explicit execution scope."""

        if ctx is not None:
            session = getattr(ctx, "session", None)
            if session_id is None:
                get_session_id = getattr(session, "get_session_id", None)
                if callable(get_session_id):
                    session_id = str(get_session_id())
            if member_id is None:
                get_agent_id = getattr(session, "get_agent_id", None)
                if callable(get_agent_id):
                    member_id = str(get_agent_id())
            if team_id is None:
                get_team_id = getattr(session, "get_team_id", None)
                if callable(get_team_id):
                    team_id = str(get_team_id())
            if member_id is None or team_id is None:
                try:
                    route_member_id, route_team_id, _ = self._capture_route(ctx)
                except RuntimeError:
                    route_member_id = route_team_id = None
                member_id = member_id or route_member_id
                team_id = team_id or route_team_id

        capture = self._current_capture()
        if capture is not None:
            with self._subscription_lock:
                is_active = self._active_captures.get(capture.subscription) is capture
            if is_active and self._capture_matches(
                capture,
                session_id=session_id,
                member_id=member_id,
                team_id=team_id,
            ):
                return capture
        if not session_id:
            return None

        scope_key = None
        try:
            scope_key = self._scope_key(
                session_id=str(session_id),
                member_id=member_id,
                team_id=team_id,
            )
        except RuntimeError:
            # Team capture resolution is allowed to fail when no Team root is
            # active; callers treat that as no matching invoke capture.
            scope_key = None
        if scope_key is None:
            return None
        return self._find_active_capture(scope_key)

    def _unsubscribe_capture(self, capture: _InvokeCapture) -> None:
        self._trajectory_span_processor.unsubscribe(capture.subscription)
        with self._subscription_lock:
            self._active_captures.pop(capture.subscription, None)
        if self._invoke_capture.get() is capture:
            if capture.context_token is not None:
                try:
                    self._invoke_capture.reset(capture.context_token)
                except ValueError:
                    self._invoke_capture.set(None)
            else:
                self._invoke_capture.set(None)

    def _merge_clean_increment(
        self,
        capture: _InvokeCapture,
        increment: Trajectory,
    ) -> Trajectory:
        with self._scope_lock(capture.scope_key):
            current = self._scope_windows.get(capture.scope_key)
            merged = merge_trajectories(current, increment) if current is not None else increment
            merged = trim_trajectory(merged, self._max_trajectory_spans)
            self._scope_windows[capture.scope_key] = merged
        return self._project_window(capture)

    def _reset_current_scope(self) -> None:
        """Discard the active scope window at an explicit lifecycle boundary."""

        capture = self._current_capture()
        if capture is None:
            return
        with self._scope_lock(capture.scope_key):
            self._scope_windows.pop(capture.scope_key, None)

    @staticmethod
    def _capture_quality_issues(trajectory: Trajectory | None) -> tuple[Mapping[str, object], ...]:
        """Report parse/continuity failures that the processor cannot infer."""

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
                try:
                    json.loads(value)
                except ValueError:
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
        """Drain one subscription and apply the clean-window quality gate."""

        capture = capture or self._resolve_capture(ctx=ctx)
        if capture is None:
            return None, None, ()
        increment, issues = self._trajectory_span_processor.drain(capture.subscription)
        if increment is not None:
            self._record_execution_increment(capture, increment)
            issues = tuple(issues) + self._capture_quality_issues(increment)
        if issues:
            return None, increment, issues
        if required_category is not None:
            has_required = bool(
                increment is not None
                and any(span_category(span) == required_category for span in iter_spans(increment))
            )
            if not has_required:
                # A model/tool callback with no corresponding ended span is a
                # capture-quality failure, not a business error.
                return (
                    None,
                    increment,
                    (MappingProxyType({"code": "missing_required_span", "category": required_category}),),
                )
        if increment is not None and merge:
            return self._merge_clean_increment(capture, increment), increment, ()
        if increment is not None:
            return self._project_window(capture), increment, ()
        return self._project_window(capture), None, ()

    @staticmethod
    def _record_execution_increment(
        capture: _InvokeCapture,
        increment: Trajectory,
    ) -> None:
        """Recorder extension point; the base evolution rail does not archive."""

        del capture, increment

    @staticmethod
    def _enrich_latest_llm(trajectory: Trajectory, response: Any) -> Trajectory:
        """Return a copy with token fields attached to the latest LLM span."""

        _, prompt_ids, completion_ids, logprobs = _split_response_token_fields(response)
        if prompt_ids is None and completion_ids is None and logprobs is None:
            return trajectory
        payload = trajectory.to_otlp()
        target: dict[str, Any] | None = None
        # ``iter_spans`` intentionally returns detached projections.  Locate
        # the mutable copy in the private payload tree instead, then wrap the
        # resulting payload in a fresh immutable Trajectory below.
        for resource_span in payload.get("resourceSpans") or []:
            for scope_span in resource_span.get("scopeSpans") or []:
                for span in scope_span.get("spans") or []:
                    if not isinstance(span, dict):
                        continue
                    if span_category(span) != "llm":
                        continue
                    if target is None or span_sort_key(span) >= span_sort_key(target):
                        target = span
        if target is None:
            return trajectory
        attrs = span_attributes(target)
        if prompt_ids is not None:
            attrs[RL_PROMPT_TOKEN_IDS] = deepcopy(prompt_ids)
        if completion_ids is not None:
            attrs[RL_COMPLETION_TOKEN_IDS] = deepcopy(completion_ids)
        if logprobs is not None:
            attrs[RL_LOGPROBS] = deepcopy(logprobs)
        target["attributes"] = attributes_from_map(attrs)
        return Trajectory.from_otlp(payload)

    async def _trigger_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Prepare one detached input, then execute it sync or in background."""
        prepared = await self._prepare_evolution_input(trajectory, ctx)
        if prepared is None:
            return
        if self._async_evolution:
            task = asyncio.create_task(
                self._safe_run_evolution(prepared),
                name=f"evolution-{prepared.skill_name or 'unknown'}",
            )
            self._bg_tasks.add(BackgroundTask.from_asyncio_task(task, group="evolution"))
            self._bg_tasks = {item for item in self._bg_tasks if not item.done()}
        else:
            await self._safe_run_evolution(prepared)

    # ---- Evolution extension points (override as needed, default no-op) ----

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called at the start of each invoke.

        ctx contains the invoke inputs and agent context.
        Override this method to initialize RL-specific state.
        """
        pass

    async def _on_after_model_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Called after each model call.

        ctx contains current model input/output, suitable for step-level evolution.
        Override this method to implement RL-style step-level updates.
        """
        del ctx, trajectory

    async def _on_after_tool_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Called after each tool call.

        ctx contains tool name, args and result, suitable for tool selection evolution.
        Override this method to implement tool selection optimization.
        """
        del ctx, trajectory

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Called at the end of each invoke after final drain and gate.

        Override this method to implement custom post-invoke logic from the
        supplied clean projection (e.g., threshold detection or follow-up).
        """
        del ctx, trajectory

    async def _on_after_evolution_triggered(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Called after an after-invoke evolution trigger is scheduled or run.

        Subclasses override this to consume state that must remain visible to
        ``_allow_evolution_trigger`` and snapshotting during the trigger.
        """
        pass

    async def _on_after_task_iteration(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Extension point for after_task_iteration hook.

        Override this method to implement custom per-iteration logic
        using only the supplied clean projection.
        """
        del ctx, trajectory

    def _allow_evolution_trigger(
        self,
        trigger_point: EvolutionTriggerPoint,
        ctx: AgentCallbackContext,
    ) -> bool:
        """Return whether the current trigger point is allowed to launch evolution."""
        return True

    async def _prepare_evolution_input(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[PreparedEvolutionInput]:
        """Phase 1: Synchronously capture detached input while ctx is alive.

        Subclasses override to capture additional immutable state (e.g.
        presented experience entries). Called in after_invoke before spawning
        a background task.
        """
        del ctx
        messages = self._trajectory_to_messages(
            trajectory,
            fields=DEFAULT_EVOLUTION_MESSAGE_FIELDS,
        )
        return PreparedEvolutionInput(
            trajectory=trajectory,
            messages=tuple(deepcopy(messages)),
        )

    @classmethod
    def _normalize_callback_messages(cls, messages: List[Any]) -> List[dict]:
        """Normalize callback-visible messages into JSON-safe dicts."""
        result: List[dict] = []
        for message in messages:
            if isinstance(message, dict):
                result.append(message)
                continue

            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "")

            item: dict[str, Any] = {"role": role, "content": content}

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": getattr(tool_call, "id", ""),
                        "name": getattr(tool_call, "name", ""),
                        "arguments": getattr(tool_call, "arguments", ""),
                    }
                    for tool_call in tool_calls
                ]

            name = getattr(message, "name", None)
            if name:
                item["name"] = name

            result.append(item)
        return result

    @staticmethod
    def _trajectory_to_messages(
        trajectory: Optional[Trajectory],
        *,
        fields: Collection[MessageField] = DEFAULT_EVOLUTION_MESSAGE_FIELDS,
    ) -> List[dict]:
        """Derive detached messages with explicitly selected semantic fields."""
        if trajectory is None:
            return []
        return trajectory_to_messages(trajectory, fields=fields)

    @staticmethod
    def _extract_tool_args(tool_args: Any) -> dict[str, Any]:
        """Return tool args as a dict, accepting JSON-encoded tool calls."""
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _extract_tool_content(inputs: ToolCallInputs) -> str:
        """Extract textual content from common tool result shapes."""
        result = inputs.tool_result
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            content = data.get("skill_content") or data.get("content") or ""
            if content:
                return content if isinstance(content, str) else str(content)

        tool_msg = inputs.tool_msg
        if tool_msg is not None and hasattr(tool_msg, "content"):
            content = tool_msg.content
            return content if isinstance(content, str) else str(content)

        content = getattr(result, "content", "")
        return content if isinstance(content, str) else str(content)

    async def _safe_run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        """Phase 2: Safely execute evolution in background.

        Catches exceptions to prevent polluting the main lifecycle flow.
        Acquires semaphore to limit concurrent evolution LLM calls.
        """
        if not isinstance(prepared, PreparedEvolutionInput):
            raise TypeError("prepared must be a PreparedEvolutionInput")
        outcome: dict[str, str] | None = None
        try:
            total_timeout = self._get_evolution_total_timeout_secs()
            # Suppression must cover the actual execution body so spans from
            # optimizer/judge/review calls are exporter-visible but never routed
            # into the triggering Agent subscription.
            with self._trajectory_span_processor.suppress():
                if total_timeout is None:
                    async with self._evolution_sem:
                        await self.run_evolution(prepared)
                else:
                    async with asyncio.timeout(total_timeout):
                        async with self._evolution_sem:
                            await self.run_evolution(prepared)
        except TimeoutError:
            total_timeout = self._get_evolution_total_timeout_secs()
            timeout_text = f"{total_timeout:.2f}".rstrip("0").rstrip(".") if total_timeout is not None else "unknown"
            outcome = {
                "status": "timed_out",
                "message": f"background evolution timed out after {timeout_text}s",
            }
            logger.warning("[EvolutionRail] background evolution timed out after %ss", timeout_text)
        except Exception as exc:
            outcome = {"status": "failed", "message": str(exc)}
            logger.warning("[EvolutionRail] background evolution failed: %s", exc)
        finally:
            if outcome is not None:
                self._emit_background_outcome_event(outcome)

    def _get_evolution_total_timeout_secs(self) -> Optional[float]:
        """Optional total timeout for one background evolution task."""
        return None

    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        """Called with one detached input when evolution is triggered."""
        del prepared

    async def drain_pending_approval_events(
        self,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> list[OutputSchema]:
        """Compatibility wrapper for draining buffered host events."""
        return await self.drain_pending_host_events(wait=wait, timeout=timeout)

    async def drain_pending_host_events(
        self,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> list[OutputSchema]:
        """Return and clear buffered host events.

        Waits for background tasks if requested, then collects events from
        the shared host-event buffer.

        Args:
            wait: If True, wait for all pending background tasks to complete
                  before draining. Ensures no events are missed.
            timeout: Maximum seconds to wait (None = no limit).
        """
        if wait and timeout is None:
            timeout = self._get_evolution_total_timeout_secs()
        if wait and self._bg_tasks:
            pending = [t for t in self._bg_tasks if not t.done()]
            if pending:
                if timeout is not None:
                    import anyio

                    with anyio.move_on_after(timeout):
                        for task in pending:
                            await task.wait()
                else:
                    for task in pending:
                        await task.wait()
                self._bg_tasks = {t for t in self._bg_tasks if not t.done()}

        events = self._collect_pending_host_events()
        if events:
            logger.debug("[EvolutionRail] drained %d pending events", len(events))
        return events

    def _collect_pending_approval_events(self) -> list[OutputSchema]:
        """Compatibility wrapper for draining the shared host-event buffer."""
        return self._collect_pending_host_events()

    def emit_host_event(self, event: OutputSchema) -> None:
        """Buffer one host-visible event for post-invoke draining."""
        self._pending_host_events.append(event)

    @staticmethod
    def _register_runtime_tools(agent: Any, tools: list[Any]) -> None:
        """Register rail-owned runtime tools with Runner and the agent ability manager."""
        if not tools:
            return
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None and hasattr(ability_manager, "add_ability"):
            for tool in tools:
                ability_manager.add_ability(tool.card, tool)
            return
        Runner.resource_mgr.add_tool(tools)
        if ability_manager is not None:
            for tool in tools:
                ability_manager.add(tool.card)

    @staticmethod
    def _unregister_runtime_tools(agent: Any, tools: list[Any]) -> None:
        """Remove rail-owned runtime tools from Runner and the agent ability manager."""
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None and hasattr(ability_manager, "remove_ability"):
            for tool in tools:
                name = getattr(tool.card, "name", None)
                if name:
                    ability_manager.remove_ability(name)
            return
        for tool in tools:
            name = getattr(tool.card, "name", None)
            if name and ability_manager is not None:
                ability_manager.remove(name)
            tool_id = getattr(tool.card, "id", None)
            if tool_id:
                Runner.resource_mgr.remove_tool(tool_id)

    def _collect_pending_host_events(self) -> list[OutputSchema]:
        """Return and clear the shared host-event buffer."""
        events = list(self._pending_host_events)
        self._pending_host_events.clear()
        return events

    def _emit_background_outcome_event(self, outcome: dict[str, str]) -> None:
        """Expose background evolution outcomes through the host-event buffer."""
        meta = EvolutionHostEventMeta(
            event_kind="outcome",
            rail_kind=outcome.get("rail_kind", "base"),
            stage=outcome.get("stage"),
            skill_name=outcome.get("skill_name"),
            request_id=outcome.get("request_id"),
            signal_type=outcome.get("signal_type"),
            source=outcome.get("source"),
            status=outcome["status"],
        )
        self.emit_host_event(
            OutputSchema(
                type="llm_reasoning",
                index=0,
                payload={
                    "content": f"[Evolution] {outcome['message']}\n",
                    "evolution_meta": meta.to_payload(),
                },
            )
        )

    async def cleanup_background_tasks(self) -> None:
        """Wait for background evolution tasks, then clear the registry.

        Host watchers call this when they see terminal progress (cancelled/noop/done).
        Rails may still be finishing evaluate at that moment, so prefer waiting over
        immediate cancel. Only force-cancel after a wait timeout.
        """
        pending = [task for task in self._bg_tasks if not task.done()]
        self._bg_tasks.clear()
        for task in pending:
            try:
                await asyncio.wait_for(task.wait(), timeout=120.0)
            except TimeoutError:
                logger.warning("[EvolutionRail] background task still running after wait timeout; cancelling")
                await task.cancel(reason="evolution_rail_wait_timeout")
            except Exception as exc:
                logger.warning("[EvolutionRail] background task wait failed: %s", exc)


__all__ = ["EvolutionRail", "EvolutionTriggerPoint"]
