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
from enum import Enum
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
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
    InvokeInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail


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
      - _on_after_model_call(ctx): Step-level updates after LLM calls
      - _on_after_tool_call(ctx): Tool-level updates after tool calls
      - _on_after_invoke(ctx): Custom logic after invoke, before builder cleared
      - _on_after_task_iteration(ctx): Custom logic after each task-loop iteration
      - run_evolution(trajectory, ctx): Called when evolution_trigger fires

    The evolution trigger point is configurable via ``evolution_trigger``.
    """

    priority = 60  # Lower than security rails, higher than user rails

    def __init__(
        self,
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        accumulate_trajectory: bool = False,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
    ):
        """Initialize EvolutionRail.

        Args:
            trajectory_store: Optional trajectory store. If None, uses InMemoryTrajectoryStore.
            team_trajectory_store: Optional shared team trajectory store. When set,
                each member's trajectory is also saved here for team-level aggregation.
            accumulate_trajectory: Whether to keep the trajectory builder across invoke rounds.
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
        """
        super().__init__()
        self._trajectory_store = trajectory_store or InMemoryTrajectoryStore()
        self._builder: Optional[TrajectoryBuilder] = None
        self._accumulate_trajectory = accumulate_trajectory
        self._evolution_trigger = evolution_trigger
        self._team_trajectory_store = team_trajectory_store

        self._async_evolution = async_evolution
        self._bg_tasks: set[BackgroundTask] = set()
        self._pending_approval_events: list[OutputSchema] = []
        self._evolution_sem = asyncio.Semaphore(max_concurrent_evolution)

    @property
    def trajectory_store(self) -> TrajectoryStore:
        """Get the trajectory store."""
        return self._trajectory_store

    @property
    def builder(self) -> Optional[TrajectoryBuilder]:
        """Public accessor for the trajectory builder.

        Subclasses (TeamSkillRail, RLRail) need to access the builder
        for custom evolution triggering outside of after_invoke.
        """
        return self._builder

    # ---- Trajectory collection (final, subclasses should not override) ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize trajectory builder at the start of each invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs):
            return

        # If accumulating across rounds and builder already exists, keep it
        if self._builder is not None and self._should_accumulate_trajectory():
            await self._on_before_invoke(ctx)
            return

        session_id = inputs.conversation_id or ""
        # Capture member_id for team trajectory aggregation
        agent_id = getattr(ctx.agent, "card", None)
        member_id = agent_id.id if agent_id else None
        self._builder = TrajectoryBuilder(
            session_id=session_id,
            source="online",
            member_id=member_id,
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

        # Trigger evolution if configured
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_MODEL_CALL:
            trajectory = self._build_trajectory()
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

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

        # Trigger evolution if configured
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TOOL_CALL:
            trajectory = self._build_trajectory()
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Called after each task-loop iteration."""
        await self._on_after_task_iteration(ctx)

        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TASK_ITERATION:
            trajectory = self._build_trajectory()
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Finalize trajectory for this invoke round."""
        if self._builder is None:
            return

        trajectory = self._build_trajectory()
        if trajectory is None:
            return

        self._trajectory_store.save(trajectory)

        if self._team_trajectory_store is not None:
            self._team_trajectory_store.save(trajectory)

        # Extension point: called after saving, before builder is cleared
        await self._on_after_invoke(ctx)

        # Trigger evolution if configured for after_invoke
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_INVOKE:
            await self._trigger_evolution(trajectory, ctx)

        if not self._should_accumulate_trajectory():
            self._builder = None

    # ---- Trajectory strategy hooks ----

    def _should_accumulate_trajectory(self) -> bool:
        """Whether to keep the existing trajectory builder across invoke rounds.

        Returns False (default): each invoke round gets a fresh builder.
        Returns True: builder survives across rounds; subclass triggers
                      evolution at custom timing.

        Subclasses can either set _accumulate_trajectory = True or override
        this method for dynamic logic.
        """
        return self._accumulate_trajectory

    # ---- Trajectory helper methods ----

    def _build_trajectory(self) -> Optional[Trajectory]:
        """Build trajectory from current builder with snapshot.

        Returns trajectory on success, None if no builder.
        """
        if self._builder is None:
            return None
        trajectory = self._builder.build()
        # Snapshot steps to avoid shared-reference mutation
        trajectory.steps = list(trajectory.steps)
        return trajectory

    def _save_trajectory(self, trajectory: Trajectory) -> None:
        """Save trajectory to store."""
        self._trajectory_store.save(trajectory)

    async def _trigger_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Internal: trigger evolution with async/sync handling."""
        if self._async_evolution:
            snapshot = await self._snapshot_for_evolution(trajectory, ctx)
            if snapshot is not None:
                from openjiuwen.core.common.background_tasks import create_background_task

                bg_task = await create_background_task(
                    self._safe_run_evolution(snapshot),
                    name=f"evolution-{snapshot.get('skill_name', 'unknown')}",
                    group="evolution",
                )
                self._bg_tasks.add(bg_task)
                # Prune completed tasks to prevent unbounded growth
                self._bg_tasks = {t for t in self._bg_tasks if not t.done()}
        else:
            await self.run_evolution(trajectory, ctx)

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

    async def _on_after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called at the end of each invoke, before builder is cleared.

        The trajectory has been saved, but the builder is still available.
        Override this method to implement custom post-invoke logic
        (e.g., threshold detection, follow_up triggering).
        """
        pass

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Extension point for after_task_iteration hook.

        Override this method to implement custom per-iteration logic
        while the trajectory builder is still populated.
        """
        pass

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[dict]:
        """Phase 1: Synchronously capture snapshot while ctx is alive.

        Subclasses override to capture additional data (e.g. parsed_messages,
        session state). Called in after_invoke before spawning background task.
        """
        return {"trajectory": trajectory}

    async def _safe_run_evolution(self, snapshot: dict) -> None:
        """Phase 2: Safely execute evolution in background.

        Catches exceptions to prevent polluting the main lifecycle flow.
        Acquires semaphore to limit concurrent evolution LLM calls.
        """
        try:
            trajectory = snapshot["trajectory"]
            async with self._evolution_sem:
                await self.run_evolution(trajectory, ctx=None, snapshot=snapshot)
        except Exception as exc:
            logger.warning("[EvolutionRail] background evolution failed: %s", exc)

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        """Called when evolution_trigger fires or subclass calls manually.

        In async mode: ctx=None, snapshot contains captured data.
        In sync mode: ctx is active, snapshot=None (backward-compatible).

        Args:
            trajectory: Complete trajectory for this conversation round
            ctx: Callback context (None in async mode)
            snapshot: Captured data from _snapshot_for_evolution (None in sync mode)
        """
        pass

    async def drain_pending_approval_events(
        self,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> list[OutputSchema]:
        """Return and clear buffered approval events.

        Waits for background tasks if requested, then collects events from
        the subclass-specific buffer.

        Args:
            wait: If True, wait for all pending background tasks to complete
                  before draining. Ensures no events are missed.
            timeout: Maximum seconds to wait (None = no limit).
        """
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

        events = self._collect_pending_approval_events()
        if events:
            logger.debug("[EvolutionRail] drained %d pending events", len(events))
        return events

    def _collect_pending_approval_events(self) -> list[OutputSchema]:
        """Hook: return and clear subclass-specific event buffer.

        Subclasses override to drain their own pending approval events.
        Default returns empty list for direct EvolutionRail usage.
        """
        return []

    async def cleanup_background_tasks(self) -> None:
        """Cancel and clear all background tasks. Called by host on shutdown."""
        for task in self._bg_tasks:
            if not task.done():
                await task.cancel(reason="evolution_rail_shutdown")
        self._bg_tasks.clear()


__all__ = ["EvolutionRail", "EvolutionTriggerPoint"]
