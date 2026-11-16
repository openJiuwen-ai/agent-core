# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The event boundary preserves current and future executor options."""

from inspect import signature
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import patch

import pytest

from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.tool_call_events import (
    EventfulAbilityManager,
    react_tool_call_scope,
    with_tool_call_event,
)


def test_eventful_executor_keeps_the_upstream_signature():
    assert signature(EventfulAbilityManager.execute) == signature(AbilityManager.execute)
    assert get_type_hints(EventfulAbilityManager.execute) == get_type_hints(AbilityManager.execute)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_executor_options_and_single_execution_survive_the_boundary(enabled):
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    manager = SimpleNamespace(
        tool_call_events_enabled=enabled,
    )
    call = ToolCall(id="call-1", type="function", name="echo", arguments="{}")
    callback = object()
    seen = []

    @with_tool_call_event
    async def execute(owner, ctx, tool_call, session, *, on_tool_result=None):
        seen.append((owner, ctx, tool_call, session, on_tool_result))
        return [({"ok": True}, ToolMessage(tool_call_id=call.id, content="done"))]

    context = object()

    @react_tool_call_scope
    async def run(agent):
        return await execute(agent.ability_manager, context, [call], None, on_tool_result=callback)

    agent = SimpleNamespace(ability_manager=manager)
    with (
        patch.object(Runner, "get_extension_bus", return_value=bus),
        patch.object(bus, "trigger", wraps=bus.trigger) as trigger,
    ):
        result = await run(agent)
    assert len(seen) == 1
    assert seen[0] == (manager, context, [call], None, callback)
    assert trigger.await_count == int(enabled)
    assert result[0][0] == {"ok": True}
    assert result[0][1].tool_call_id == call.id
