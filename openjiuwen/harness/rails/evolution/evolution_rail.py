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
import warnings
from enum import Enum
from typing import Any, List, Optional, Union

from openjiuwen.agent_evolving.signal.from_conv import ConversationSignalDetector
from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryStore,
    LLMCallDetail,
    MemberTrajectorySnapshot,
    ToolCallDetail,
    Trajectory,
    TrajectoryBuilder,
    TrajectorySink,
    TrajectoryStep,
    TrajectoryStore,
)
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.evolution.contracts import EvolutionHostEventMeta, EvolutionSnapshot


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
    _DEFAULT_MEMBER_ROLE: Optional[str] = None

    def __init__(
        self,
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        max_trajectory_steps: Optional[int] = 200,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        disabled_skills: Optional[Union[str, list[str]]] = None,
    ):
        """Initialize EvolutionRail.

        Args:
            trajectory_store: Optional trajectory store. If None, uses InMemoryTrajectoryStore.
            team_trajectory_store: Deprecated shared team trajectory store. Passing it
                emits a warning and no longer enables online dual-write aggregation.
            max_trajectory_steps: Optional maximum number of recent trajectory steps
                retained in the cross-invoke builder window.
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
        """
        super().__init__()
        if team_trajectory_store is not None:
            warnings.warn(
                "team_trajectory_store is deprecated; use trajectory_source/trajectory_sink instead",
                DeprecationWarning,
                stacklevel=2,
            )
        self._trajectory_store = trajectory_store or InMemoryTrajectoryStore()
        self._builder: Optional[TrajectoryBuilder] = None
        self._max_trajectory_steps = max_trajectory_steps
        self._evolution_trigger = evolution_trigger
        self._trajectory_sink: Optional[TrajectorySink] = None
        self._disabled_skills: set[str] = _normalize_skill_names(disabled_skills)
        self._team_id: Optional[str] = None
        self._member_role: Optional[str] = None

        self._async_evolution = async_evolution
        self._bg_tasks: set[BackgroundTask] = set()
        self._pending_host_events: list[OutputSchema] = []
        self._evolution_sem = asyncio.Semaphore(max_concurrent_evolution)

    @property
    def trajectory_store(self) -> TrajectoryStore:
        """Get the trajectory store."""
        return self._trajectory_store

    @property
    def disabled_skills(self) -> set[str]:
        """Set of skill names excluded from self-optimization."""
        return self._disabled_skills

    @classmethod
    def _normalize_name_set(cls, raw: Optional[Union[str, list[str]]]) -> set[str]:
        """Normalize skill names into a set."""
        return _normalize_skill_names(raw)

    @property
    def builder(self) -> Optional[TrajectoryBuilder]:
        """Public accessor for the trajectory builder.

        Subclasses (TeamSkillRail, RLRail) need to access the builder
        for custom evolution triggering outside of after_invoke.
        """
        return self._builder

    def set_trajectory_sink(
        self,
        sink: Optional[TrajectorySink],
        *,
        team_id: Optional[str],
        member_role: Optional[str] = None,
    ) -> None:
        """Bind this rail to a runtime trajectory sink."""
        if sink is not None and not team_id:
            raise ValueError("team_id is required when binding a trajectory sink")
        self._trajectory_sink = sink
        self._team_id = team_id
        role = self._DEFAULT_MEMBER_ROLE if member_role is None else member_role
        self._member_role = _normalize_member_role(role)

    # ---- Trajectory collection (final, subclasses should not override) ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize trajectory builder at the start of each invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs):
            return

        session_id = self._resolve_trajectory_session_id(ctx, inputs)

        # Reuse the builder across invoke rounds in the same session.
        if self._builder is not None and self._builder.session_id == session_id:
            logger.debug(
                "[EvolutionRail] reusing trajectory builder session_id=%s",
                session_id,
            )
            await self._on_before_invoke(ctx)
            return

        # Capture member_id for team trajectory aggregation
        agent_id = getattr(ctx.agent, "card", None)
        member_id = agent_id.id if agent_id else None
        meta = {"member_role": self._member_role} if self._member_role else None
        self._builder = TrajectoryBuilder(
            session_id=session_id,
            source="online",
            member_id=member_id,
            meta=meta,
            max_steps=self._max_trajectory_steps,
        )
        logger.debug(
            "[EvolutionRail] created trajectory builder session_id=%s, member_id=%s, member_role=%s",
            session_id,
            member_id,
            self._member_role,
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
        prompt_token_ids = None
        completion_token_ids = None
        logprobs = None
        if inputs.messages or inputs.response:
            model_name = "unknown"
            if model_name == "unknown" and ctx.agent:
                config = getattr(ctx.agent, "config", None)
                if config:
                    model_name = getattr(config, "model", None) or model_name

            response_dict, prompt_token_ids, completion_token_ids, logprobs = _split_response_token_fields(
                inputs.response
            )

            detail = LLMCallDetail(
                model=model_name,
                messages=list(inputs.messages) if inputs.messages else [],
                response=response_dict,
                tools=list(inputs.tools) if inputs.tools else None,
            )

        # Get agent_id from ctx.agent.card.id
        agent_id = getattr(ctx.agent, "card", None)
        agent_id_str = agent_id.id if agent_id else "unknown"

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

        # Trigger extension point
        await self._on_after_model_call(ctx)

        # Trigger evolution if configured
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_MODEL_CALL and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_MODEL_CALL, ctx
        ):
            trajectory = self._build_trajectory()
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record tool step and trigger evolution extension point."""
        if self._builder is None:
            logger.debug("[EvolutionRail] after_tool_call skipped because trajectory builder is empty")
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
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TOOL_CALL and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_TOOL_CALL, ctx
        ):
            trajectory = self._build_trajectory()
            if trajectory is not None:
                await self._trigger_evolution(trajectory, ctx)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Called after each task-loop iteration."""
        await self._on_after_task_iteration(ctx)

        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_TASK_ITERATION and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_TASK_ITERATION, ctx
        ):
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

        self._publish_trajectory_snapshot(trajectory)

        # Extension point: called after saving, before builder is cleared
        await self._on_after_invoke(ctx)

        # Trigger evolution if configured for after_invoke
        if self._evolution_trigger == EvolutionTriggerPoint.AFTER_INVOKE and self._allow_evolution_trigger(
            EvolutionTriggerPoint.AFTER_INVOKE, ctx
        ):
            await self._trigger_evolution(trajectory, ctx)
            await self._on_after_evolution_triggered(trajectory, ctx)

    # ---- Trajectory helper methods ----

    @staticmethod
    def _resolve_trajectory_session_id(
        ctx: AgentCallbackContext,
        inputs: InvokeInputs,
    ) -> str:
        """Resolve the runtime session id used for trajectory accumulation."""
        session = getattr(ctx, "session", None)
        if session is not None and isinstance(session, Session):
            return session.get_session_id()
        return inputs.conversation_id or ""

    def _reset_trajectory_builder(self) -> None:
        """Reset the current trajectory builder.

        Subclasses use this at their own lifecycle boundary, for example
        after uploading an RL episode. The base rail does not expose public
        reset because generic evolution rails should keep a session window.
        """
        self._builder = None

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

    def _publish_trajectory_snapshot(self, trajectory: Trajectory) -> None:
        sink = self._trajectory_sink
        team_id = self._team_id
        if sink is None or not team_id:
            return
        member_id = trajectory.meta.get("member_id")
        if not member_id:
            return
        member_role = _normalize_member_role(trajectory.meta.get("member_role")) or self._member_role
        sink.publish_member_trajectory(
            MemberTrajectorySnapshot.make(
                team_id=team_id,
                member_id=str(member_id),
                member_role=member_role,
                trajectory=trajectory,
            )
        )

    async def _trigger_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Internal: trigger evolution with async/sync handling."""
        if self._async_evolution:
            snapshot = await self._snapshot_for_evolution(trajectory, ctx)
            if snapshot is not None:
                snapshot_contract = EvolutionSnapshot.from_legacy_dict(snapshot)
                from openjiuwen.core.common.background_tasks import create_background_task

                bg_task = await create_background_task(
                    self._safe_run_evolution(snapshot),
                    name=f"evolution-{snapshot_contract.skill_name or 'unknown'}",
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

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Extension point for after_task_iteration hook.

        Override this method to implement custom per-iteration logic
        while the trajectory builder is still populated.
        """
        pass

    def _allow_evolution_trigger(
        self,
        trigger_point: EvolutionTriggerPoint,
        ctx: AgentCallbackContext,
    ) -> bool:
        """Return whether the current trigger point is allowed to launch evolution."""
        return True

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[dict]:
        """Phase 1: Synchronously capture snapshot while ctx is alive.

        Subclasses override to capture additional data (e.g. messages,
        session state). Called in after_invoke before spawning background task.
        """
        messages = self._collect_messages_from_trajectory(trajectory)
        return EvolutionSnapshot(trajectory=trajectory, messages=messages).to_legacy_dict()

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

    @classmethod
    def _collect_messages_from_trajectory(cls, trajectory: Optional[Trajectory]) -> List[dict]:
        """Derive message-like dicts from recorded trajectory steps."""
        if trajectory is None:
            return []
        raw = ConversationSignalDetector.convert_trajectory_to_messages(trajectory)
        normalized = cls._normalize_callback_messages(raw)
        deduped: List[dict] = []
        for message in normalized:
            if message not in deduped:
                deduped.append(message)
        return deduped

    async def _safe_run_evolution(self, snapshot: dict) -> None:
        """Phase 2: Safely execute evolution in background.

        Catches exceptions to prevent polluting the main lifecycle flow.
        Acquires semaphore to limit concurrent evolution LLM calls.
        """
        outcome: dict[str, str] | None = None
        try:
            trajectory = snapshot["trajectory"]
            total_timeout = self._get_evolution_total_timeout_secs()
            if total_timeout is None:
                async with self._evolution_sem:
                    await self.run_evolution(trajectory, ctx=None, snapshot=snapshot)
            else:
                async with asyncio.timeout(total_timeout):
                    async with self._evolution_sem:
                        await self.run_evolution(trajectory, ctx=None, snapshot=snapshot)
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
        """Cancel and clear all background tasks. Called by host on shutdown."""
        for task in self._bg_tasks:
            if not task.done():
                await task.cancel(reason="evolution_rail_shutdown")
        self._bg_tasks.clear()


__all__ = ["EvolutionRail", "EvolutionTriggerPoint"]
