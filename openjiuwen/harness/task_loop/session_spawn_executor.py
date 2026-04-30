# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionSpawnExecutor — executes SESSION_SPAWN_TASK_TYPE tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Tuple

from openjiuwen.core.common.logging import logger
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
from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.controller.modules.task_manager import TaskFilter

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session
    from openjiuwen.harness.deep_agent import DeepAgent

from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE


class SessionSpawnExecutor(TaskExecutor):
    """Executor for SESSION_SPAWN_TASK_TYPE tasks.

    This executor creates a subagent instance and invokes it with the
    task description, then yields the result as a TASK_COMPLETION event.
    """

    def __init__(
        self, dependencies: TaskExecutorDependencies, deep_agent: "DeepAgent"
    ) -> None:
        super().__init__(dependencies)
        self._deep_agent = deep_agent

    async def execute_ability(
        self, task_id: str, session: "Session"
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Execute subagent task.

        Args:
            task_id: Task identifier.
            session: Current session.

        Yields:
            ControllerOutputChunk with task result or error.
        """
        tasks = await self._task_manager.get_task(TaskFilter(task_id=task_id))
        if not tasks:
            yield self._build_error_chunk(task_id, "Task not found")
            return

        meta = tasks[0].metadata or {}
        subagent_type = meta.get("subagent_type", "general-purpose")
        query = meta.get("task_description", "")
        cid = meta.get("sub_session_id", "")

        logger.info(
            f"[SessionSpawnExecutor] Executing task_id={task_id}, "
            f"subagent_type={subagent_type}, sub_session_id={cid}"
        )

        try:
            subagent = self._deep_agent.create_subagent(subagent_type, cid)
            result = await subagent.invoke({"query": query, "conversation_id": cid})
            payload = result.get("output", "") if isinstance(result, dict) else str(result)

            logger.info(
                f"[SessionSpawnExecutor] task_id={task_id} completed, "
                f"output_len={len(payload)}"
            )

            yield ControllerOutputChunk(
                index=0,
                payload=ControllerOutputPayload(
                    type=EventType.TASK_COMPLETION,
                    data=[JsonDataFrame(data={"output": payload})],
                    metadata={
                        "task_id": task_id,
                        "task_type": SESSION_SPAWN_TASK_TYPE,
                    },
                ),
                last_chunk=True,
            )
        except Exception as exc:
            logger.exception(
                f"[SessionSpawnExecutor] task_id={task_id} failed: {exc}"
            )
            yield self._build_error_chunk(task_id, str(exc))

    def _build_error_chunk(
        self, task_id: str, error: str
    ) -> ControllerOutputChunk:
        """Build error chunk for failed task."""
        return ControllerOutputChunk(
            index=0,
            payload=ControllerOutputPayload(
                type=EventType.TASK_FAILED,
                data=[TextDataFrame(text=error)],
                metadata={"task_id": task_id, "task_type": SESSION_SPAWN_TASK_TYPE},
            ),
            last_chunk=True,
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Session spawn tasks do not support pause."""
        return False, "Session spawn tasks do not support pause"

    async def pause(self, task_id: str, session: Session) -> bool:
        """Not supported."""
        return False

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Session spawn tasks support cancellation."""
        return True, ""

    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel session spawn task.

        Args:
            task_id: Task to cancel.
            session: Current session.

        Returns:
            True if cancellation succeeded.
        """
        # already canceled in TaskScheduler
        logger.info(f"[SessionSpawnExecutor] Cancelling task_id={task_id}")
        return True


def build_session_spawn_executor(deep_agent: "DeepAgent"):
    """Factory function for SessionSpawnExecutor.

    Args:
        deep_agent: Parent DeepAgent instance.

    Returns:
        Factory function that creates SessionSpawnExecutor.
    """

    def _factory(deps: TaskExecutorDependencies) -> SessionSpawnExecutor:
        return SessionSpawnExecutor(deps, deep_agent)

    return _factory


__all__ = [
    "SESSION_SPAWN_TASK_TYPE",
    "SessionSpawnExecutor",
    "build_session_spawn_executor",
]
