# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent event-handler implementation.

Routes core EventQueue events through the TaskScheduler
pipeline and updates TaskPlan state accordingly.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.modules.event_handler import (
    EventHandler,
    EventHandlerInput,
)
from openjiuwen.core.controller.schema.event import (
    InputEvent,
    TaskInteractionEvent,
)
from openjiuwen.core.controller.schema.task import (
    Task as CoreTask,
    TaskStatus as CoreTaskStatus,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.deepagents.deep_agent_event_executor import (
    DEEP_TASK_TYPE,
)
from openjiuwen.deepagents.schema.state import (
    load_state,
)

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import (
        DeepAgent,
    )


class DeepAgentEventHandler(EventHandler):
    """EventHandler that drives the outer task loop.

    Creates core Tasks via TaskManager so that the
    TaskScheduler can pick them up and dispatch to
    DeepAgentEventExecutor.

    Attributes:
        _deep_agent: Back-reference to the owning
            DeepAgent instance.
        _last_result: Result of the most recent
            iteration (used by the outer loop).
        _completion_event: Signalled when the
            scheduled task finishes.
        _completion_result: Result dict set by
            handle_task_completion / handle_task_failed.
    """

    def __init__(
        self, deep_agent: "DeepAgent"
    ) -> None:
        super().__init__()
        self._deep_agent = deep_agent
        self._last_result: Optional[
            Dict[str, Any]
        ] = None
        self._completion_event: asyncio.Event = (
            asyncio.Event()
        )
        self._completion_result: Optional[
            Dict[str, Any]
        ] = None

    @property
    def last_result(
        self,
    ) -> Optional[Dict[str, Any]]:
        """Result from the last handle_input call."""
        return self._last_result

    async def handle_input(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Create a core Task and wait for completion.

        Uses the TaskPlan's next pending task ID as the
        core task_id so that executor/handler can map
        back to the correct TaskItem. Falls back to a
        random UUID when no TaskPlan exists.

        Args:
            inputs: Contains the InputEvent and
                Session.

        Returns:
            The result dict from the executor.
        """
        agent = self._deep_agent
        event = inputs.event
        session = inputs.session

        # Extract query from InputEvent
        query = self._extract_query(event)
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )

        coordinator = agent.loop_coordinator
        if coordinator is None:
            logger.warning(
                "handle_input called without "
                "LoopCoordinator"
            )
            return None

        # Resolve task_id from TaskPlan if available
        if not task_id and session is not None:
            ctx = AgentCallbackContext(
                agent=agent, session=session,
            )
            state = load_state(ctx)
            if state.task_plan is not None:
                next_task = (
                    state.task_plan.get_next_task()
                )
                if next_task is not None:
                    task_id = next_task.id

        # Fallback to random UUID
        if not task_id:
            task_id = uuid.uuid4().hex

        session_id = (
            session.get_session_id()
            if session
            else "default"
        )

        # Create a core Task with SUBMITTED status
        core_task = CoreTask(
            session_id=session_id,
            task_id=task_id,
            task_type=DEEP_TASK_TYPE,
            description=query,
            status=CoreTaskStatus.SUBMITTED,
        )

        # Reset completion signal
        self._completion_event.clear()
        self._completion_result = None

        # Add task to TaskManager for scheduling
        if self._task_manager is not None:
            await self._task_manager.add_task(
                core_task
            )

        # Wait for task completion signal
        await self._completion_event.wait()

        result = self._completion_result or {}
        if not result:
            result = {"status": "completed"}

        self._last_result = result
        return result

    async def handle_task_interaction(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Handle a steer event.

        Injects the steer message into the agent's
        context for the next iteration.

        Args:
            inputs: Contains TaskInteractionEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        msg = ""
        if isinstance(event, TaskInteractionEvent):
            if event.interaction:
                first = event.interaction[0]
                msg = getattr(
                    first, "text", str(first)
                )
        logger.info(f"Steer received: {msg[:100]}")
        return {
            "status": "steer_acknowledged",
            "msg": msg,
        }

    async def handle_task_completion(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Signal completion and update TaskPlan.

        Args:
            inputs: Contains TaskCompletionEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )

        # Extract result from task_result data
        result: Dict[str, Any] = {}
        task_result = getattr(
            event, "task_result", None
        )
        if task_result:
            for df in task_result:
                data = getattr(df, "data", None)
                if isinstance(data, dict):
                    result = data
                    break
                text = getattr(df, "text", None)
                if text:
                    result["output"] = text

        if task_id:
            session = inputs.session
            ctx = AgentCallbackContext(
                agent=self._deep_agent,
                session=session,
            )
            state = load_state(ctx)
            if state.task_plan is not None:
                state.task_plan.mark_completed(
                    task_id
                )

        # Signal the waiting handle_input
        self._completion_result = result
        self._completion_event.set()

        return {
            "status": "completed",
            "task_id": task_id,
        }

    async def handle_task_failed(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Signal failure and update TaskPlan.

        Args:
            inputs: Contains TaskFailedEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )
        error_msg = getattr(
            event, "error_message", "unknown"
        )
        if task_id:
            session = inputs.session
            ctx = AgentCallbackContext(
                agent=self._deep_agent,
                session=session,
            )
            state = load_state(ctx)
            if state.task_plan is not None:
                state.task_plan.mark_failed(
                    task_id, str(error_msg)
                )

        # Signal the waiting handle_input
        self._completion_result = {
            "error": str(error_msg),
        }
        self._completion_event.set()

        return {
            "status": "failed",
            "task_id": task_id,
            "error": str(error_msg),
        }

    @staticmethod
    def _extract_query(event: Any) -> str:
        """Pull text from an InputEvent."""
        if isinstance(event, InputEvent):
            for df in event.input_data:
                text = getattr(df, "text", None)
                if text:
                    return str(text)
                data = getattr(df, "data", None)
                if isinstance(data, dict):
                    return str(
                        data.get("query", data)
                    )
        return ""


__all__ = [
    "DeepAgentEventHandler",
]
