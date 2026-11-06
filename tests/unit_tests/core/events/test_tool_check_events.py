# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``tool.check`` CORE identity checks before calling the rail executor."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.tool_check import (
    TOOL_CHECK,
    ToolCheckInput,
    ToolCheckOutput,
    ToolCheckTriggerContext,
)
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, SessionContext
from openjiuwen.core.session import with_session
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


@pytest.mark.asyncio
async def test_core_returns_executor_output_and_stores_raw_result() -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    event_input = ToolCheckInput(tool_call_id="c1", tool_name="bash")
    callback_ctx = AgentCallbackContext(agent=object())
    raw_result = object()
    expected = ToolCheckOutput(decision="ask", reason="why")
    executor = AsyncMock(return_value=(raw_result, expected))
    context = ToolCheckTriggerContext(
        callback_ctx=callback_ctx,
        invoke_original=executor,
    )

    output = await bus.trigger(
        Event(TOOL_CHECK, event_input),
        trigger_context=context,
    )

    assert output is expected
    assert context.raw_result is raw_result
    executor.assert_awaited_once_with(callback_ctx)


@pytest.mark.asyncio
async def test_core_does_not_resolve_session_before_calling_executor() -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    session = create_agent_session(session_id="tool-check-current-session")
    callback_ctx = AgentCallbackContext(agent=object())
    executor = AsyncMock(return_value=(None, ToolCheckOutput(decision="allow")))
    context = ToolCheckTriggerContext(callback_ctx=callback_ctx, invoke_original=executor)

    @with_session(session)
    async def trigger():
        return await bus.trigger(
            Event(TOOL_CHECK, ToolCheckInput(tool_call_id="c1", tool_name="bash")),
            SessionContext(session.get_session_id()),
            trigger_context=context,
        )

    await trigger()

    assert executor.await_args.args[0].session is None


@pytest.mark.asyncio
async def test_core_rejects_missing_context() -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    event_input = ToolCheckInput(tool_call_id="c1", tool_name="bash")
    executor = AsyncMock(return_value=(object(), ToolCheckOutput(decision="allow")))

    with pytest.raises(BaseError):
        await bus.trigger(Event(TOOL_CHECK, event_input), trigger_context=None)
    assert executor.await_count == 0
