# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""EvolutionRail: Base class for all evolution rails.

All evolution rails inherit from this class and automatically get
trajectory collection capability. Subclasses override extension points
to implement evolution algorithms.

Core design:
- Trajectory collection is automatic (handled by base class)
- Prefers OTLP-first trajectory when tracer spans are available
- Falls back to step-based builder projection (LegacyTrajectory)
- Extension points for evolution: _on_after_model_call, _on_after_tool_call, run_evolution
- DeepAgents imports from core (consistent with existing patterns)
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional, Union

from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryStore,
    LegacyTrajectory,
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryBuilder,
    TrajectoryStep,
    TrajectoryStore,
    ensure_otlp_handlers_registered,
    to_legacy_trajectory,
    trajectory_from_legacy,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail


def _split_response_token_fields(
    response: Any,
) -> tuple[Any, Optional[list], Optional[list], Optional[Any]]:
    """Lift token-level fields out of an LLM response.

    Returns ``(response_for_detail, prompt_token_ids, completion_token_ids,
    logprobs)``. The returned ``response_for_detail`` has those three
    fields stripped to avoid duplicate storage in the trajectory.
    """
    if response is None:
        return None, None, None, None
    response_dict: Any = response
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
        except Exception as exc:
            logger.debug(
                "EvolutionRail: model_dump failed while splitting token fields, "
                "falling back to original response: %s",
                exc,
            )
            dumped = None
        if isinstance(dumped, dict):
            response_dict = dumped
    if not isinstance(response_dict, dict):
        return response, None, None, None
    prompt_token_ids = response_dict.pop("prompt_token_ids", None)
    completion_token_ids = response_dict.pop("completion_token_ids", None)
    logprobs = response_dict.pop("logprobs", None)
    return response_dict, prompt_token_ids, completion_token_ids, logprobs


def _extract_tool_call_id(tool_call: Any) -> str | None:
    """Extract tool_call_id from a tool call object or dict."""
    if tool_call is None:
        return None
    if isinstance(tool_call, dict):
        value = tool_call.get("id") or tool_call.get("tool_call_id")
        return str(value) if value else None
    value = getattr(tool_call, "id", None) or getattr(tool_call, "tool_call_id", None)
    return str(value) if value else None


def _extract_response_tool_call_ids(response: Any) -> list[str]:
    """Collect tool_call ids from an LLM response payload."""
    if response is None:
        return []
    if hasattr(response, "model_dump"):
        try:
            response = response.model_dump()
        except Exception as exc:
            logger.debug(
                "EvolutionRail: model_dump failed while extracting tool_call ids, "
                "falling back to attribute access: %s",
                exc,
            )
    if not isinstance(response, dict):
        tool_calls = getattr(response, "tool_calls", None) or []
    else:
        tool_calls = response.get("tool_calls") or []
    result: list[str] = []
    for tool_call in tool_calls:
        tool_call_id = _extract_tool_call_id(tool_call)
        if tool_call_id:
            result.append(tool_call_id)
    return result


class EvolutionRail(DeepAgentRail):
    """Base class for all evolution rails.

    Inheriting this class provides automatic trajectory collection.
    Subclasses should override one or more extension points:
      - _on_after_model_call(ctx): Called after each model call, suitable for RL step-level updates
      - _on_after_tool_call(ctx): Called after each tool call, suitable for tool selection optimization
      - run_evolution(trajectory, ctx): Called after conversation round ends, suitable for experience extraction

    DeepAgents hard-depends on core (consistent with deep_agent.py patterns).
    agent_id is retrieved from ctx.agent.card.id at runtime, no need to pass in constructor.
    """

    priority = 60  # Lower than security rails, higher than user rails

    def __init__(
        self,
        trajectory_store: Optional[TrajectoryStore] = None,
        max_trajectory_steps: Optional[int] = 200,
    ):
        """Initialize EvolutionRail.

        Args:
            trajectory_store: Optional trajectory store. If None, uses InMemoryTrajectoryStore.
            max_trajectory_steps: Optional maximum number of recent trajectory steps
                retained by the builder / OTLP projection.
        """
        super().__init__()
        self._trajectory_store = trajectory_store or InMemoryTrajectoryStore()
        self._builder: Optional[TrajectoryBuilder] = None
        self._max_trajectory_steps = max_trajectory_steps
        self._last_llm_ref: Optional[str] = None
        self._tool_call_parent_refs: dict[str, str] = {}
        self._active_trace_id: Optional[str] = None
        self.trajectory_consumer_id = f"{type(self).__name__}:{id(self):x}"
        self.trajectory_state_manager = ensure_otlp_handlers_registered()

    @property
    def trajectory_store(self) -> TrajectoryStore:
        """Get the trajectory store."""
        return self._trajectory_store

    def init(self, agent: Any) -> None:
        """Ensure process-wide trajectory trace handlers are registered."""
        super().init(agent)
        self.trajectory_state_manager = ensure_otlp_handlers_registered()

    def uninit(self, agent: Any) -> None:
        """Run rail cleanup without unregistering process-wide trajectory handlers."""
        super().uninit(agent)

    @property
    def builder(self) -> Optional[TrajectoryBuilder]:
        """Public accessor for the trajectory builder."""
        return self._builder

    # ---- Trajectory collection (final, subclasses should not override) ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize trajectory builder at the start of each invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs):
            return

        session_id = self._resolve_trajectory_session_id(ctx, inputs)
        agent_id = getattr(ctx.agent, "card", None)
        member_id = agent_id.id if agent_id else None
        self._builder = TrajectoryBuilder(
            session_id=session_id,
            source="online",
            member_id=member_id,
            max_steps=self._max_trajectory_steps,
        )
        self._last_llm_ref = None
        self._tool_call_parent_refs = {}
        self._active_trace_id = self._trace_id_from_ctx(ctx)
        self._bind_trajectory_trace(ctx)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Record LLM step and trigger evolution extension point."""
        if self._builder is None:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return

        detail: Optional[LLMCallDetail] = None
        prompt_token_ids = None
        completion_token_ids = None
        logprobs = None
        response_dict: Any = None
        if inputs.messages or inputs.response:
            model_name = "unknown"
            if ctx.agent:
                config = getattr(ctx.agent, "config", None)
                if config:
                    model_name = getattr(config, "model", None) or model_name

            response_dict, prompt_token_ids, completion_token_ids, logprobs = (
                _split_response_token_fields(inputs.response)
            )

            detail = LLMCallDetail(
                model=model_name,
                messages=list(inputs.messages) if inputs.messages else [],
                response=response_dict,
                tools=list(inputs.tools) if inputs.tools else None,
            )

        agent_id = getattr(ctx.agent, "card", None)
        agent_id_str = agent_id.id if agent_id else "unknown"
        llm_ref = f"llm_{len(self._builder.steps) + 1:04d}"

        step = TrajectoryStep(
            kind="llm",
            detail=detail,
            prompt_token_ids=prompt_token_ids,
            completion_token_ids=completion_token_ids,
            logprobs=logprobs,
            meta={
                "operator_id": f"{agent_id_str}/llm_main",
                "agent_id": agent_id_str,
            },
        )
        self._builder.record_step(step)
        self._last_llm_ref = llm_ref
        for tool_call_id in _extract_response_tool_call_ids(response_dict):
            self._tool_call_parent_refs[tool_call_id] = llm_ref

        await self._on_after_model_call(ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record tool step and trigger evolution extension point."""
        if self._builder is None:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_call = getattr(inputs, "tool_call", None)
        tool_call_id = _extract_tool_call_id(tool_call)
        detail: Optional[ToolCallDetail] = None
        if inputs.tool_name:
            detail = ToolCallDetail(
                tool_name=inputs.tool_name,
                call_args=inputs.tool_args,
                call_result=inputs.tool_result,
                tool_call_id=tool_call_id,
            )

        meta = {"operator_id": inputs.tool_name}
        parent_llm_ref = self._tool_call_parent_refs.get(tool_call_id or "") or self._last_llm_ref
        if parent_llm_ref:
            meta["parent_llm_call"] = parent_llm_ref

        step = TrajectoryStep(
            kind="tool",
            detail=detail,
            meta=meta,
        )
        self._builder.record_step(step)

        await self._on_after_tool_call(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Finalize trajectory, save it, and trigger run_evolution."""
        try:
            if self._builder is None:
                return

            trajectory = self._build_trajectory(ctx, finalize=True)
            if trajectory is None:
                return

            self._trajectory_store.save(trajectory)
            await self.run_evolution(to_legacy_trajectory(trajectory), ctx)
        finally:
            # Always release even when builder is missing / early-return, so
            # process-wide state cannot accumulate after abnormal invoke paths.
            trace_id = self._active_trace_id or self._trace_id_from_ctx(ctx)
            if trace_id is not None:
                self.trajectory_state_manager.release_trace(
                    trace_id,
                    consumer_id=self.trajectory_consumer_id,
                )
            self._active_trace_id = None
            self._builder = None

    # ---- Trajectory helpers ----

    @staticmethod
    def _resolve_trajectory_session_id(
        ctx: AgentCallbackContext,
        inputs: InvokeInputs,
    ) -> str:
        session = getattr(ctx, "session", None)
        if session is not None and isinstance(session, Session):
            return session.get_session_id()
        return inputs.conversation_id or ""

    def _build_trajectory(
        self,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        finalize: bool = False,
    ) -> Optional[Trajectory]:
        """Build OTLP-first trajectory, falling back to builder projection."""
        trace_trajectory = self._build_trace_trajectory(ctx, finalize=finalize) if ctx is not None else None
        if trace_trajectory is not None and trace_trajectory.otlp_trace:
            legacy = self._merge_builder_step_projection(to_legacy_trajectory(trace_trajectory))
            return trajectory_from_legacy(legacy, otlp_trace=trace_trajectory.otlp_trace)

        if self._builder is None:
            return None
        return trajectory_from_legacy(self._builder.build())

    def _merge_builder_step_projection(self, legacy: LegacyTrajectory) -> LegacyTrajectory:
        """Overlay builder-collected step fields onto an OTLP-derived legacy view."""
        if self._builder is None or not self._builder.steps:
            return legacy

        builder_steps = list(self._builder.steps)
        if not legacy.steps:
            legacy.steps = builder_steps
            return legacy

        for index, target_step in enumerate(legacy.steps):
            if index >= len(builder_steps):
                break
            source_step = builder_steps[index]
            if target_step.kind != source_step.kind:
                continue
            if source_step.reward is not None and target_step.reward is None:
                target_step.reward = source_step.reward
            if source_step.prompt_token_ids is not None and target_step.prompt_token_ids is None:
                target_step.prompt_token_ids = source_step.prompt_token_ids
            if source_step.completion_token_ids is not None and target_step.completion_token_ids is None:
                target_step.completion_token_ids = source_step.completion_token_ids
            if source_step.logprobs is not None and target_step.logprobs is None:
                target_step.logprobs = source_step.logprobs
            for key, value in source_step.meta.items():
                target_step.meta.setdefault(key, deepcopy(value))
        return legacy

    def _build_trace_trajectory(
        self,
        ctx: AgentCallbackContext,
        *,
        finalize: bool,
    ) -> Optional[Trajectory]:
        trace_id = self._trace_id_from_ctx(ctx)
        if trace_id is None:
            return None

        session_id = self._builder.session_id if self._builder is not None else self._session_id_from_ctx(ctx)
        member_id = self._builder.member_id if self._builder is not None else self._member_id_from_ctx(ctx)
        meta = dict(self._builder.meta) if self._builder is not None else {}
        source = self._trajectory_source_name()

        self.trajectory_state_manager.bind_trace(
            trace_id,
            session_id=session_id,
            source=source,
            member_id=member_id,
            meta=meta,
            consumer_id=self.trajectory_consumer_id,
        )
        return self.trajectory_state_manager.build_trajectory(
            trace_id,
            session_id=session_id,
            source=source,
            member_id=member_id,
            meta=meta,
            max_steps=self._max_trajectory_steps,
            finalize=finalize,
        )

    def _bind_trajectory_trace(self, ctx: AgentCallbackContext) -> None:
        trace_id = self._trace_id_from_ctx(ctx)
        if trace_id is None:
            return
        session_id = self._builder.session_id if self._builder is not None else self._session_id_from_ctx(ctx)
        member_id = self._builder.member_id if self._builder is not None else self._member_id_from_ctx(ctx)
        meta = dict(self._builder.meta) if self._builder is not None else {}
        self.trajectory_state_manager.bind_trace(
            trace_id,
            session_id=session_id,
            source=self._trajectory_source_name(),
            member_id=member_id,
            meta=meta,
            consumer_id=self.trajectory_consumer_id,
        )

    def _trajectory_source_name(self) -> str:
        if self._builder is None:
            return "online"
        return self._builder.source or "online"

    @staticmethod
    def _trace_id_from_ctx(ctx: AgentCallbackContext) -> Optional[str]:
        session = getattr(ctx, "session", None)
        tracer = session.tracer() if session is not None and hasattr(session, "tracer") else None
        span_manager = getattr(tracer, "tracer_agent_span_manager", None)
        if not isinstance(span_manager, SpanManager):
            return None
        if span_manager.trace_id:
            return str(span_manager.trace_id)
        last_span = span_manager.last_span
        span_trace_id = getattr(last_span, "trace_id", None)
        return str(span_trace_id) if span_trace_id else None

    @staticmethod
    def _session_id_from_ctx(ctx: AgentCallbackContext) -> str:
        session = getattr(ctx, "session", None)
        if session is not None and isinstance(session, Session):
            return session.get_session_id()
        inputs = getattr(ctx, "inputs", None)
        if isinstance(inputs, InvokeInputs):
            return inputs.conversation_id or ""
        return ""

    @staticmethod
    def _member_id_from_ctx(ctx: AgentCallbackContext) -> Optional[str]:
        agent_card = getattr(getattr(ctx, "agent", None), "card", None)
        return getattr(agent_card, "id", None)

    # ---- Evolution extension points (override as needed, default no-op) ----

    async def _on_after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called after each model call.

        ctx contains current model input/output, suitable for step-level evolution.
        Override this method to implement RL-style step-level updates.
        """
        pass

    async def _on_after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Called after each tool call.

        ctx contains tool name, args and result, suitable for tool selection evolution.
        Override this method to implement tool selection optimization.
        """
        pass

    async def run_evolution(
        self,
        trajectory: Union[Trajectory, LegacyTrajectory],
        ctx: AgentCallbackContext,
    ) -> None:
        """Called after conversation round ends.

        trajectory contains the complete trajectory for this round,
        suitable for experience extraction algorithms.

        Args:
            trajectory: Complete trajectory for this conversation round
                (LegacyTrajectory preferred; OTLP Trajectory also accepted)
            ctx: Callback context with agent, session, etc.
        """
        pass


__all__ = ["EvolutionRail"]
