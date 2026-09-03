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
from openjiuwen.core.single_agent.rail.base import (
    bind_usage_delegation,
    build_usage_delegation_attribution,
    reset_usage_delegation,
)
from openjiuwen.harness.kv_cache import kv_cache_subagent_lifecycle
from openjiuwen.harness.subagent_lifecycle import (
    cleanup_subagent_task_resources,
    prepare_subagent_task_resources,
)

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
        browser_capabilities = meta.get("browser_capabilities")
        affinity_enabled = kv_cache_subagent_lifecycle.affinity_enabled(self._deep_agent)
        parent_session_id = meta.get("parent_session_id") or session.get_session_id()
        if affinity_enabled:
            cid = kv_cache_subagent_lifecycle.resolve_sub_session_id(
                task_id=task_id,
                parent_session_id=parent_session_id,
                metadata=meta,
            )
        else:
            cid = meta.get("sub_session_id", "")

        logger.info(
            f"[SessionSpawnExecutor] Executing task_id={task_id}, "
            f"subagent_type={subagent_type}, sub_session_id={cid}"
        )

        subagent = None
        child_session = None
        try:
            if subagent_type == "browser_agent":
                subagent = self._deep_agent.create_subagent(
                    subagent_type,
                    cid,
                    browser_capabilities=list(browser_capabilities or []),
                )
            else:
                subagent = self._deep_agent.create_subagent(subagent_type, cid)
            await prepare_subagent_task_resources(subagent)
            parent_invocation_id = meta.get("parent_invocation_id")
            subagent_inputs = {
                "query": query,
                "conversation_id": cid,
            }
            invoke_kwargs = {}
            if affinity_enabled:
                subagent_inputs["parent_session_id"] = parent_session_id
                subagent_inputs["delegation_id"] = task_id
                if parent_invocation_id:
                    subagent_inputs["parent_invocation_id"] = parent_invocation_id
                child_session = kv_cache_subagent_lifecycle.create_subagent_session(
                    session,
                    sub_session_id=cid,
                    parent_cache_id=parent_session_id,
                    card=subagent.card,
                )
                await child_session.pre_run(inputs=subagent_inputs)
                invoke_kwargs["session"] = child_session
            delegation_token = bind_usage_delegation(
                build_usage_delegation_attribution(
                    agent_id=getattr(getattr(subagent, "card", None), "id", None),
                    parent_session_id=parent_session_id,
                    delegation_id=task_id,
                    parent_attribution=meta.get("parent_usage_attribution"),
                    parent_invocation_id=parent_invocation_id,
                )
            )
            try:
                result = await subagent.invoke(subagent_inputs, **invoke_kwargs)
            finally:
                reset_usage_delegation(delegation_token)
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
        finally:
            if subagent is not None:
                await cleanup_subagent_task_resources(subagent)
            if child_session is not None:
                await kv_cache_subagent_lifecycle.evict_subagent(child_session)
                await child_session.post_run()

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
        if not kv_cache_subagent_lifecycle.affinity_enabled(self._deep_agent):
            return True
        tasks = await self._task_manager.get_task(TaskFilter(task_id=task_id))
        if not tasks:
            logger.warning(
                "[SessionSpawnExecutor] Skip KV evict for cancelled task: task_id=%s not found",
                task_id,
            )
            return True

        meta = tasks[0].metadata or {}
        parent_session_id = meta.get("parent_session_id") or session.get_session_id()
        sub_session_id = kv_cache_subagent_lifecycle.resolve_sub_session_id(
            task_id=task_id,
            parent_session_id=parent_session_id,
            metadata=meta,
        )
        child_session = kv_cache_subagent_lifecycle.create_subagent_session(
            session,
            sub_session_id=sub_session_id,
            parent_cache_id=parent_session_id,
        )
        await kv_cache_subagent_lifecycle.evict_subagent(child_session)
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
