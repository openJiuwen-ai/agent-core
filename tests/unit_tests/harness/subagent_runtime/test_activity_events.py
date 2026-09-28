# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent activity emission."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.subagent_runtime.activity_events import (
    SUBAGENT_ACTIVITY_EVENT_TYPE,
    ActivityEmitter,
    build_activity_payload,
    emit_subagent_activity,
)
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentActivity


@dataclass
class MockSession:
    write_stream: AsyncMock = field(default_factory=AsyncMock)


def _activity(**overrides) -> SubagentActivity:
    payload = {
        "subagent_id": "sid-1",
        "task_id": "task-1",
        "seq": 1,
        "kind": "tool_call",
        "summary": "read_file(path=README.md)",
        "tool_name": "read_file",
        "tool_call_id": "call-1",
        "at_ms": 1.0,
    }
    payload.update(overrides)
    return SubagentActivity.from_dict(payload)


@pytest.mark.asyncio
async def test_emit_subagent_activity_writes_stream() -> None:
    session = MockSession()
    activity = _activity()
    await emit_subagent_activity(session, projection=build_activity_payload(activity))
    session.write_stream.assert_awaited_once()
    schema = session.write_stream.await_args.args[0]
    assert schema.type == SUBAGENT_ACTIVITY_EVENT_TYPE
    assert schema.payload["subagent_activity"]["kind"] == "tool_call"


@pytest.mark.asyncio
async def test_emitter_drains_queue_to_parent_stream() -> None:
    session = MockSession()
    emitter = ActivityEmitter(session, config=SubagentRuntimeConfig(activity_queue_size=4))
    emitter.start()
    emitter.offer(_activity(seq=1))
    emitter.offer(_activity(seq=2, kind="tool_result", summary="done"))
    await asyncio.sleep(0.05)
    assert session.write_stream.await_count == 2
    await emitter.close()


@pytest.mark.asyncio
async def test_emitter_drops_oldest_when_queue_full() -> None:
    session = MockSession()
    emitter = ActivityEmitter(session, config=SubagentRuntimeConfig(activity_queue_size=2))
    emitter.start()
    emitter.offer(_activity(seq=1))
    emitter.offer(_activity(seq=2))
    emitter.offer(_activity(seq=3))
    assert emitter.dropped >= 1
    await emitter.close()


@pytest.mark.asyncio
async def test_emitter_disables_after_repeated_failures() -> None:
    session = MockSession()
    session.write_stream = AsyncMock(side_effect=RuntimeError("stream closed"))
    emitter = ActivityEmitter(session, config=SubagentRuntimeConfig(activity_queue_size=4))
    emitter.start()
    for seq in range(1, 5):
        emitter.offer(_activity(seq=seq))
    await asyncio.sleep(0.1)
    assert emitter.disabled is True
    await emitter.close()
