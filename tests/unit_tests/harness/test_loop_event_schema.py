# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Schema tests for harness loop events."""
from __future__ import annotations

import asyncio

from openjiuwen.core.single_agent.rail.base import (
    TaskIterationInputs,
)
from openjiuwen.harness.schema.loop_event import (
    DeepLoopEvent,
    DeepLoopEventType,
    create_loop_event,
)


async def _collect(queue: asyncio.PriorityQueue) -> list:
    out = []
    while not queue.empty():
        out.append(await queue.get())
    return out


def test_deep_loop_event_priority_order() -> None:
    queue: asyncio.PriorityQueue[DeepLoopEvent] = (
        asyncio.PriorityQueue()
    )
    queue.put_nowait(
        DeepLoopEvent(
            priority=10,
            seq=2,
            event_type=DeepLoopEventType.FOLLOWUP,
            content="follow-up",
        )
    )
    queue.put_nowait(
        DeepLoopEvent(
            priority=1,
            seq=1,
            event_type=DeepLoopEventType.STEER,
            content="steer",
        )
    )
    queue.put_nowait(
        DeepLoopEvent(
            priority=10,
            seq=1,
            event_type=DeepLoopEventType.FOLLOWUP,
            content="follow-up-2",
        )
    )

    items = asyncio.run(_collect(queue))
    assert [it.content for it in items] == [
        "steer",
        "follow-up-2",
        "follow-up",
    ]


def test_task_iteration_inputs_defaults() -> None:
    loop_event = DeepLoopEvent(
        priority=10,
        seq=1,
        event_type=DeepLoopEventType.FOLLOWUP,
        content="task",
    )
    inputs = TaskIterationInputs(
        iteration=1,
        loop_event=loop_event,
    )

    assert inputs.iteration == 1
    assert inputs.loop_event.content == "task"
    assert inputs.conversation_id is None
    assert inputs.result is None


def test_create_loop_event_default_priorities() -> None:
    abort_event = create_loop_event(
        seq=1,
        event_type=DeepLoopEventType.ABORT,
        content="stop",
    )
    steer_event = create_loop_event(
        seq=2,
        event_type=DeepLoopEventType.STEER,
        content="guide",
    )
    followup_event = create_loop_event(
        seq=3,
        event_type=DeepLoopEventType.FOLLOWUP,
        content="next",
    )

    assert abort_event.priority < steer_event.priority < followup_event.priority
