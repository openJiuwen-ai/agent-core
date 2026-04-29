# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen.core.context_engine.schema.context_state import (
    CONTEXT_COMPRESSION_STATE_TYPE,
    ContextCompressionState,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema


async def capture_context_compression_states(
    session: Session,
    action: Callable[[], Awaitable[Any]],
) -> tuple[Any, list[ContextCompressionState]]:
    chunks = []

    async def _collect_stream():
        async for chunk in session.stream_iterator():
            chunks.append(chunk)

    collect_task = asyncio.create_task(_collect_stream())
    try:
        result = await action()
    finally:
        await session.close_stream()

    await collect_task
    states = []
    for chunk in chunks:
        if not isinstance(chunk, OutputSchema) or chunk.type != CONTEXT_COMPRESSION_STATE_TYPE:
            continue
        if isinstance(chunk.payload, ContextCompressionState):
            states.append(chunk.payload)
        else:
            states.append(ContextCompressionState.model_validate(chunk.payload))
    return result, states


def assert_context_state_pair(
    states: list[ContextCompressionState],
    *,
    processor_type: str,
    phase: str = "add_messages",
    final_status: str = "completed",
) -> None:
    assert [state.status for state in states] == ["started", final_status]
    assert all(state.processor == processor_type for state in states)
    assert all(state.phase == phase for state in states)
    assert states[0].before.time
    assert states[1].duration_ms is not None
    assert states[1].summary
