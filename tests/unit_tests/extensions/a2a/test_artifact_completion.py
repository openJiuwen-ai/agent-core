# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Check actual A2A events for per-artifact completion and status-only endings."""

from collections.abc import AsyncIterator
import asyncio

import pytest
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part
from openjiuwen.extensions.a2a.a2a_agent_executor import A2AAgentExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize("final", [False, True])
async def test_every_terminal_artifact_is_complete(final: bool) -> None:
    result = AgentResult(status=TaskStatus.COMPLETED, artifacts=[
        Artifact(artifactId="a", parts=[Part(text="first")]),
        Artifact(artifactId="b", parts=[Part(text="second")]),
    ])
    async with EventQueue() as queue:
        updater = TaskUpdater(queue, "task", "context")
        await A2AAgentExecutor()._publish_result(updater, result, final=final)
        first = await queue.dequeue_event()
        second = await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        for _ in range(3):
            queue.task_done()
        assert isinstance(first, TaskArtifactUpdateEvent)
        assert isinstance(second, TaskArtifactUpdateEvent)
        assert [(first.artifact.artifact_id, first.last_chunk),
                (second.artifact.artifact_id, second.last_chunk)] == [("a", True), ("b", True)]
        assert isinstance(terminal, TaskStatusUpdateEvent)
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED])
async def test_status_only_or_exhausted_stream_closes_previous_artifacts(status: TaskStatus | None) -> None:
    async def stream(payload: dict[str, object]) -> AsyncIterator[AgentResult]:
        yield AgentResult(status=TaskStatus.WORKING, artifacts=[
            Artifact(artifactId="a", parts=[Part(text="keep this content")]),
            Artifact(parts=[Part(text="generated id")]),
        ])
        if status is not None:
            yield AgentResult(status=status)

    async with EventQueue() as queue:
        updater = TaskUpdater(queue, "task", "context")
        await A2AAgentExecutor(stream_handler=stream)._execute_streaming(updater, {})
        events = [await asyncio.wait_for(queue.dequeue_event(), timeout=2) for _ in range(6)]
        for _ in events:
            queue.task_done()
        artifacts = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 4
        assert all(not event.last_chunk for event in artifacts[:2])
        opened_ids = {event.artifact.artifact_id for event in artifacts[:2]}
        assert len(opened_ids) == 2
        assert {event.artifact.artifact_id for event in artifacts[2:]} == opened_ids
        assert all(event.last_chunk and event.append and not event.artifact.parts for event in artifacts[2:])
        assert isinstance(events[-1], TaskStatusUpdateEvent)
