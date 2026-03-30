# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent task-executor implementation.

Wraps inner ReActAgent execution as a TaskExecutor
so it can be driven by the core TaskScheduler.
"""
from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Tuple,
    TYPE_CHECKING,
)

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.controller.modules.task_scheduler import (
    TaskExecutor,
    TaskExecutorDependencies,
)
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputChunk,
    ControllerOutputPayload,
)
from openjiuwen.core.controller.schema.dataframe import (
    JsonDataFrame,
    TextDataFrame,
)
from openjiuwen.core.controller.schema.event import (
    EventType,
    InputEvent,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    TaskIterationInputs,
)

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import (
        DeepAgent,
    )

DEEP_TASK_TYPE = "deep_agent_task"


class TaskLoopEventExecutor(TaskExecutor):
    """TaskExecutor that delegates to the inner ReActAgent.

    Attributes:
        _deep_agent: Back-reference to the owning
            DeepAgent instance.
    """

    def __init__(
        self,
        dependencies: TaskExecutorDependencies,
        deep_agent: "DeepAgent",
    ) -> None:
        super().__init__(dependencies)
        self._deep_agent = deep_agent

    async def execute_ability(
        self,
        task_id: str,
        session: Session,
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Execute a task via the inner ReActAgent.

        Args:
            task_id: Task identifier (core Task.task_id).
            session: Current session.

        Yields:
            ControllerOutputChunk for each output.
        """
        agent = self._deep_agent
        if agent.react_agent is None:
            logger.warning(
                "execute_ability called without "
                "react_agent"
            )
            return

        tasks = await self._task_manager.get_task(
            task_filter=self._make_filter(task_id)
        )

        query: Any = task_id
        raw_input: Any = None

        if tasks:
            core_task = tasks[0]
            desc = core_task.description
            if desc:
                query = desc

            if core_task.inputs:
                for evt in core_task.inputs:
                    if isinstance(evt, InputEvent):
                        raw_input = self._extract_interactive_input(evt)
                        if raw_input is not None:
                            break

        state = self._get_state(session)
        plan_task = self._get_plan_task(state, task_id)
        if plan_task:
            query = (
                f"{plan_task.title}: {plan_task.description}"
                if plan_task.description
                else plan_task.title
            )

        cid = session.get_session_id()

        # Read is_follow_up from task metadata written by handler.
        is_follow_up = False
        if tasks:
            meta = tasks[0].metadata or {}
            is_follow_up = bool(meta.get("is_follow_up", False))

        handler = agent.event_handler
        if handler is not None:
            queues = getattr(
                handler, "interaction_queues", None
            )
            if queues is not None:
                steering = queues.drain_steering()
                if steering:
                    combined = "\n".join(steering)
                    query = (
                        f"{query}\n\n"
                        f"[STEERING] {combined}"
                    )

        coordinator = agent.loop_coordinator
        iteration = (
            (coordinator.current_iteration + 1)
            if coordinator
            else 1
        )

        query_preview = str(query)[:120]
        if UserConfig.is_sensitive():
            logger.info(
                f"[OuterLoop] iteration={iteration} "
                f"task_id={task_id}"
            )
        else:
            logger.info(
                f"[OuterLoop] iteration={iteration} "
                f"task_id={task_id}, "
                f"query={query_preview}"
            )

        # Build iteration context for lifecycle.
        # Rails may modify iter_inputs.query in
        # before_task_iteration (e.g. task_instruction template).
        loop_event = InputEvent.from_user_input(query)
        iter_inputs = TaskIterationInputs(
            iteration=iteration,
            loop_event=loop_event,
            conversation_id=cid,
            query=query,
            is_follow_up=is_follow_up,
        )
        ctx = AgentCallbackContext(
            agent=agent,
            inputs=iter_inputs,
            session=session,
        )

        # Mark task in-progress in TaskPlan
        if self._get_plan_task(state, task_id):
            state.task_plan.mark_in_progress(task_id)

        # Fire BEFORE_TASK_ITERATION.
        # Rails may modify iter_inputs.query here.
        await ctx.fire(
            AgentCallbackEvent.BEFORE_TASK_ITERATION
        )

        # Use raw_input (InteractiveInput) if present, otherwise use query.
        effective_query = raw_input or iter_inputs.query or query
        effective: Dict[str, Any] = {
            "query": effective_query,
        }
        if cid:
            effective["conversation_id"] = cid
        if tasks and tasks[0].metadata:
            metadata = tasks[0].metadata
            if metadata.get("run_kind") is not None:
                effective["run_kind"] = metadata.get("run_kind")
            if metadata.get("run_context") is not None:
                effective["run_context"] = metadata.get("run_context")

        try:
            result = await agent.react_agent.invoke(
                effective, session, _streaming=True
            )

            # Mark completed in TaskPlan (skip for interrupt)
            if result.get("result_type") != "interrupt":
                if self._get_plan_task(state, task_id):
                    summary = str(result.get("output", ""))[:200]
                    state.task_plan.mark_completed(task_id, summary)

            # Fire AFTER_TASK_ITERATION
            iter_inputs.result = result
            ctx.inputs = iter_inputs
            await ctx.fire(
                AgentCallbackEvent
                .AFTER_TASK_ITERATION
            )

            # increment_iteration is called in
            # DeepAgent._run_task_loop after yield.

            if UserConfig.is_sensitive():
                logger.info(
                    f"[OuterLoop] iteration={iteration} "
                    f"task_id={task_id} completed"
                )
            else:
                logger.info(
                    f"[OuterLoop] iteration={iteration} "
                    f"task_id={task_id} completed, "
                    f"output="
                    f"{str(result.get('output', ''))[:200]}"
                )

            payload = ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[JsonDataFrame(data=result)],
                metadata={"task_id": task_id},
            )
            yield ControllerOutputChunk(
                index=0,
                payload=payload,
                last_chunk=True,
            )
        except Exception as exc:
            logger.error(
                f"Task {task_id} execution failed: "
                f"{exc}",
                exc_info=True,
            )

            # Mark failed in TaskPlan
            if self._get_plan_task(state, task_id):
                state.task_plan.mark_failed(task_id, str(exc))

            payload = ControllerOutputPayload(
                type=EventType.TASK_FAILED,
                data=[TextDataFrame(text=str(exc))],
                metadata={"task_id": task_id},
            )
            yield ControllerOutputChunk(
                index=0,
                payload=payload,
                last_chunk=True,
            )

    async def can_pause(
        self,
        task_id: str,
        session: Session,
    ) -> Tuple[bool, str]:
        """Pause is not supported."""
        _ = task_id
        _ = session
        return False, "pause not supported"

    async def pause(
        self,
        task_id: str,
        session: Session,
    ) -> bool:
        """Pause is not supported."""
        _ = task_id
        _ = session
        return False

    async def can_cancel(
        self,
        task_id: str,
        session: Session,
    ) -> Tuple[bool, str]:
        """Cancellation is always allowed."""
        _ = task_id
        _ = session
        return True, ""

    async def cancel(
        self,
        task_id: str,
        session: Session,
    ) -> bool:
        """Cancel a task by marking it FAILED.

        Args:
            task_id: Task to cancel.
            session: Current session.

        Returns:
            True if cancellation succeeded.
        """
        state = self._get_state(session)
        if self._get_plan_task(state, task_id):
            state.task_plan.mark_failed(task_id, "cancelled")
        coordinator = self._deep_agent.loop_coordinator
        if coordinator:
            coordinator.request_abort()
        return True

    def _get_state(self, session: Session) -> Any:
        """Load DeepAgentState from session."""
        return self._deep_agent.load_state(session)

    def _get_plan_task(self, state: Any, task_id: str) -> Any:
        """Return the TaskPlan item for task_id, or None."""
        if state and state.task_plan:
            return state.task_plan.get_task(task_id)
        return None

    @staticmethod
    def _make_filter(task_id: str) -> Any:
        """Build a TaskFilter for a single task_id."""
        from openjiuwen.core.controller.modules.task_manager import (
            TaskFilter,
        )
        return TaskFilter(task_id=task_id)

    @staticmethod
    def _extract_interactive_input(event: Any) -> Any:
        """Extract InteractiveInput from InputEvent if present."""
        from openjiuwen.core.session import InteractiveInput

        if not isinstance(event, InputEvent):
            return None
        for df in event.input_data:
            data = getattr(df, "data", None)
            if isinstance(data, dict):
                query = data.get("query")
                if isinstance(query, InteractiveInput):
                    return query
        return None


def build_deep_executor(
    deep_agent: "DeepAgent",
) -> Callable[
    [TaskExecutorDependencies],
    "TaskLoopEventExecutor",
]:
    """Create a builder factory for registry.

    Args:
        deep_agent: The owning DeepAgent instance.

    Returns:
        A callable that accepts dependencies and
        returns a TaskLoopEventExecutor.
    """
    def _builder(
        deps: TaskExecutorDependencies,
    ) -> TaskLoopEventExecutor:
        return TaskLoopEventExecutor(
            deps, deep_agent
        )
    return _builder


__all__ = [
    "DEEP_TASK_TYPE",
    "TaskLoopEventExecutor",
    "build_deep_executor",
]
