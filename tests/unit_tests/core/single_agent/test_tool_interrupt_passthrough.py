# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for tool-interrupt control flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest


def _interrupt() -> ToolInterruptException:
    return ToolInterruptException(
        request=InterruptRequest(
            message="Approve this tool call?",
            metadata={"source": "test"},
        ),
        tool_call=ToolCall(
            id="call-1",
            type="function",
            name="interrupting_tool",
            arguments='{"value": 1}',
        ),
    )


@pytest.mark.asyncio
async def test_ability_manager_does_not_wrap_tool_interrupt(monkeypatch) -> None:
    interrupt = _interrupt()
    tool = SimpleNamespace(invoke=AsyncMock(side_effect=interrupt))
    manager = AbilityManager(owner_id="interrupt-test")
    manager.add(
        ToolCard(
            id="interrupting_tool",
            name="interrupting_tool",
            description="Interrupt before completing",
        )
    )
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_kwargs: tool)

    with pytest.raises(ToolInterruptException) as exc_info:
        await manager._execute_single_tool_call(
            interrupt.tool_call,
            session=None,
        )

    assert exc_info.value is interrupt
