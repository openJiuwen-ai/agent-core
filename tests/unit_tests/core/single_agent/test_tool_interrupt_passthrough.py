# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool interrupts must remain control flow through AbilityManager."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest


@pytest.mark.asyncio
async def test_tool_interrupt_is_not_wrapped(monkeypatch):
    call = ToolCall(id="call-1", type="function", name="interrupting_tool", arguments="{}")
    interrupt = ToolInterruptException(
        request=InterruptRequest(message="Approve?"),
        tool_call=call,
    )
    tool = SimpleNamespace(invoke=AsyncMock(side_effect=interrupt))
    manager = AbilityManager(owner_id="interrupt-test")
    manager.add(ToolCard(id="interrupting_tool", name="interrupting_tool"))
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_kwargs: tool)

    with pytest.raises(ToolInterruptException) as raised:
        await manager._execute_single_tool_call(call, session=None)

    assert raised.value is interrupt
