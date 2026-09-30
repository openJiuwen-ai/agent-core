# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The native harness streams the model-facing text next to the structured tool result."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import ToolOutput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness_protocol import DeliveryMode, ItemLifecycleEvent
from openjiuwen.harness_providers.base import PendingTurn
from openjiuwen.harness_providers.native import DeepAgentHarness
from openjiuwen.harness_providers.native.harness import _ObservationRail, _TurnState


@pytest.mark.asyncio
@pytest.mark.level0
async def test_observation_rail_adds_rendered_result_without_touching_structured_result() -> None:
    session = SimpleNamespace(write_stream=AsyncMock())
    tool_result = ToolOutput(success=True, data={"matching_files": ["/a.py"]})
    ctx = SimpleNamespace(
        session=session,
        exception=None,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="call-1"),
            tool_name="glob",
            tool_result=tool_result,
            tool_msg=ToolMessage(content="/a.py", tool_call_id="call-1"),
        ),
    )

    await _ObservationRail().after_tool_call(ctx)

    payload = session.write_stream.await_args.args[0].payload
    assert payload["tool_result"] == tool_result.model_dump(mode="json", by_alias=True)
    assert payload["rendered_result"] == "/a.py"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_tool_result_chunk_carries_rendered_result_into_protocol_data() -> None:
    harness = DeepAgentHarness(lambda _context: None)
    emitted: list[Any] = []

    async def _capture(payload: Any, **_kwargs: Any) -> None:
        emitted.append(payload)

    harness._emit = _capture
    turn = PendingTurn(content="hi", message_id="m1", turn_id="t1", accepted_mode=DeliveryMode.AUTO)
    state = _TurnState("t1")
    chunk = OutputSchema(
        type="tool_result",
        index=0,
        payload={
            "tool_call_id": "call-1",
            "tool_name": "glob",
            "tool_result": {"success": True},
            "rendered_result": "/a.py",
        },
    )

    await harness._consume_chunk(turn, state, chunk)

    event = next(item for item in emitted if isinstance(item, ItemLifecycleEvent))
    assert event.data["result"] == {"success": True}
    assert event.data["rendered_result"] == "/a.py"
    block = state.tool_messages[-1].content[0]
    assert block.content == {"success": True}
    assert block.data["rendered_result"] == "/a.py"
