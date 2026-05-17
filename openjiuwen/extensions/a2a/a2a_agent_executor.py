# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part as A2APart, Task, TaskState, TaskStatus as A2ATaskStatus

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer


class A2AAgentExecutor(AgentExecutor):
    """A2A executor that bridges A2A request/response objects and openjiuwen runtime payloads.

    Streaming vs non-streaming is chosen by constructor arguments, not the request payload:
    if ``stream_handler`` is set it is used; otherwise ``invoke_handler``. If both are set,
    the stream path takes precedence.
    """

    _HALT_STATUSES = frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
            TaskStatus.INPUT_REQUIRED,
        }
    )

    def __init__(
        self,
        invoke_handler: Callable[[dict[str, object]], Awaitable[AgentResult]] | None = None,
        stream_handler: Callable[[dict[str, object]], AsyncIterator[AgentResult]] | None = None,
    ) -> None:
        self._invoke_handler = invoke_handler
        self._stream_handler = stream_handler

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not (context.message and context.task_id and context.context_id):
            return

        task_id = context.task_id
        context_id = context.context_id
        updater = TaskUpdater(event_queue=event_queue, task_id=task_id, context_id=context_id)
        request_payload = A2ATransformer.from_a2a_request(context)

        try:
            # a2a-sdk consumers require a Task snapshot before any TaskStatusUpdateEvent
            # (see InvalidAgentResponseError: "Agent should enqueue Task before TaskStatusUpdateEvent").
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=A2ATaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message],
                )
            )
            working_message = updater.new_agent_message(parts=[A2APart(text="Processing your request...")])
            await updater.start_work(message=working_message)

            if self._stream_handler is not None:
                await self._execute_streaming(updater, request_payload)
            elif self._invoke_handler is not None:
                await self._execute_invoke(updater, request_payload)
            else:
                logger.warning(
                    "A2AAgentExecutor has no invoke_handler or stream_handler; completing task with no output."
                )
                await updater.complete()
        except asyncio.CancelledError:
            await self._cancel_quietly(updater)
            raise
        except Exception:
            logger.exception("A2AAgentExecutor.execute failed for task_id=%s", task_id)
            await self._fail_quietly(updater)
            raise

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        if not task_id:
            return

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context.context_id or "",
        )
        await self._cancel_quietly(updater)

    async def _execute_streaming(
        self,
        updater: TaskUpdater,
        request_payload: dict[str, object],
    ) -> None:
        async for chunk in self._stream_handler(request_payload):
            stop = await self._publish_result(updater, chunk, final=False)
            if stop:
                return
        await updater.complete()

    async def _execute_invoke(
        self,
        updater: TaskUpdater,
        request_payload: dict[str, object],
    ) -> None:
        result = await self._invoke_handler(request_payload)
        await self._publish_result(updater, result, final=True)

    async def _publish_result(
        self,
        updater: TaskUpdater,
        result: AgentResult,
        *,
        final: bool,
    ) -> bool:
        artifacts = result.artifacts
        last_index = len(artifacts) - 1
        for index, artifact in enumerate(artifacts):
            await updater.add_artifact(
                parts=[A2ATransformer.to_a2a_part(part) for part in artifact.parts],
                artifact_id=artifact.artifactId,
                name=artifact.name,
                metadata=artifact.metadata or None,
                last_chunk=final and index == last_index,
            )

        status = result.status or (TaskStatus.COMPLETED if final else TaskStatus.WORKING)
        is_terminal = final or status in self._HALT_STATUSES

        if not is_terminal:
            await updater.start_work()
            return False

        if status == TaskStatus.FAILED:
            await updater.failed()
        elif status == TaskStatus.CANCELED:
            await updater.cancel()
        elif status == TaskStatus.INPUT_REQUIRED:
            await updater.requires_input()
        else:
            await updater.complete()
        return True

    @staticmethod
    async def _cancel_quietly(updater: TaskUpdater) -> None:
        try:
            await updater.cancel()
        except RuntimeError as exc:
            if "already in a terminal state" not in str(exc):
                raise

    @staticmethod
    async def _fail_quietly(updater: TaskUpdater) -> None:
        try:
            await updater.failed()
        except RuntimeError as exc:
            if "already in a terminal state" not in str(exc):
                raise
