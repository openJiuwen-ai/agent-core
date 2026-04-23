# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""EvolutionRail: Base class for all evolution rails.

All evolution rails inherit from this class and automatically get
trajectory collection capability. Subclasses override extension points
to implement evolution algorithms.

Core design:
- Trajectory collection is automatic (handled by base class)
- Extension points for evolution: _on_after_model_call, _on_after_tool_call, run_evolution
- DeepAgents imports from core (consistent with existing patterns)
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    TrajectoryBuilder,
    TrajectoryStep,
    TrajectoryStore,
    InMemoryTrajectoryStore,
    LLMCallDetail,
    ToolCallDetail,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
    InvokeInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail


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

    def __init__(self, trajectory_store: Optional[TrajectoryStore] = None):
        """Initialize EvolutionRail.

        Args:
            trajectory_store: Optional trajectory store. If None, uses InMemoryTrajectoryStore.
        """
        super().__init__()
        self._trajectory_store = trajectory_store or InMemoryTrajectoryStore()
        self._builder: Optional[TrajectoryBuilder] = None

    @property
    def trajectory_store(self) -> TrajectoryStore:
        """Get the trajectory store."""
        return self._trajectory_store

    # ---- Trajectory collection (final, subclasses should not override) ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize trajectory builder at the start of each invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs):
            return

        session_id = inputs.conversation_id or ""
        self._builder = TrajectoryBuilder(
            session_id=session_id,
            source="online",
        )

        # Trigger extension point for subclasses
        await self._on_before_invoke(ctx)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Record LLM step and trigger evolution extension point."""
        if self._builder is None:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return

        # Build LLMCallDetail
        detail: Optional[LLMCallDetail] = None
        if inputs.messages or inputs.response:
            model_name = "unknown"
            if model_name == "unknown" and ctx.agent:
                config = getattr(ctx.agent, "config", None)
                if config:
                    model_name = getattr(config, "model", None) or model_name

            detail = LLMCallDetail(
                model=model_name,
                messages=list(inputs.messages) if inputs.messages else [],
                response=inputs.response,
                tools=list(inputs.tools) if inputs.tools else None,
            )

        # Get agent_id from ctx.agent.card.id
        agent_id = getattr(ctx.agent, "card", None)
        agent_id_str = agent_id.id if agent_id else "unknown"

        step = TrajectoryStep(
            kind="llm",
            detail=detail,
            meta={
                "operator_id": f"{agent_id_str}/llm_main",
                "agent_id": agent_id_str,
            },
        )
        self._builder.record_step(step)

        # Trigger extension point
        await self._on_after_model_call(ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record tool step and trigger evolution extension point."""
        if self._builder is None:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        # Build ToolCallDetail
        detail: Optional[ToolCallDetail] = None
        if inputs.tool_name:
            detail = ToolCallDetail(
                tool_name=inputs.tool_name,
                call_args=inputs.tool_args,
                call_result=inputs.tool_result,
                tool_call_id=None,
            )

        step = TrajectoryStep(
            kind="tool",
            detail=detail,
            meta={
                "operator_id": inputs.tool_name,
            },
        )
        self._builder.record_step(step)

        # Trigger extension point
        await self._on_after_tool_call(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Finalize trajectory, save it, and trigger run_evolution."""
        if self._builder is None:
            return

        # Build trajectory
        trajectory = self._builder.build()

        # Save trajectory
        self._trajectory_store.save(trajectory)

        # Trigger evolution
        await self.run_evolution(trajectory, ctx)

        # Reset builder
        self._builder = None

    # ---- Evolution extension points (override as needed, default no-op) ----

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called at the start of each invoke.

        ctx contains the invoke inputs and agent context.
        Override this method to initialize RL-specific state.
        """
        pass

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
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Called after conversation round ends.

        trajectory contains the complete trajectory for this round,
        suitable for experience extraction algorithms.

        Args:
            trajectory: Complete trajectory for this conversation round
            ctx: Callback context with agent, session, etc.
        """
        pass


__all__ = ["EvolutionRail"]
