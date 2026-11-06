# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``tool.call`` event contract and CORE forwarding to AbilityManager."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.tool_call import (
    TOOL_CALL,
    ToolCallEventInput,
    ToolCallEventOutput,
    ToolCallTriggerContext,
)
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.single_agent import create_agent_session


def _tool_calls() -> list[ToolCall]:
    return [
        ToolCall(id="call-1", type="function", name="echo", arguments='{"text": "a"}', index=0),
        ToolCall(id="call-2", type="function", name="upper", arguments='{"text": "b"}', index=1),
    ]


async def _rig(results: list | None = None):
    tool_calls = _tool_calls()
    if results is None:
        results = [(f"result-{tc.id}", ToolMessage(content=f"msg-{tc.id}", tool_call_id=tc.id)) for tc in tool_calls]
    invoke_original = AsyncMock(return_value=results)
    session = create_agent_session(session_id="tool-call-events")
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    trigger_context = ToolCallTriggerContext(
        tool_calls=tuple(tool_calls),
        invoke_original=invoke_original,
    )
    event = Event(
        TOOL_CALL,
        tuple(ToolCallEventInput(tool_call_id=tc.id, tool_name=tc.name, arguments=tc.arguments) for tc in tool_calls),
    )
    return SimpleNamespace(
        bus=bus,
        invoke_original=invoke_original,
        results=results,
        tool_calls=tool_calls,
        session=session,
        event=event,
        trigger_context=trigger_context,
    )


async def _trigger(rig):
    return await rig.bus.trigger(rig.event, rig.session, trigger_context=rig.trigger_context)


@pytest.mark.asyncio
async def test_empty_chain_forwards_whole_batch_once_and_keeps_object_identity() -> None:
    rig = await _rig()

    outputs = await _trigger(rig)

    assert rig.invoke_original.await_count == 1
    sent = rig.invoke_original.await_args.args[0]
    assert all(item is original for item, original in zip(sent, rig.tool_calls, strict=True))
    assert isinstance(outputs, tuple)
    assert [output.tool_call_id for output in outputs] == ["call-1", "call-2"]
    for output, (result, message) in zip(outputs, rig.results, strict=True):
        assert output.result is result
        assert output.text == message.content
    assert rig.trigger_context.raw_results is rig.results


@pytest.mark.asyncio
async def test_handler_rewrites_arguments_on_a_copy_matched_by_tool_name() -> None:
    rig = await _rig()
    seen: list[str] = []

    async def rewrite(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        seen.append("upper")
        items = tuple(
            dataclasses.replace(item, arguments={"text": "rewritten"}) if item.tool_name == "upper" else item
            for item in event.event_input
        )
        return await event_next(Event(event.event_type, items))

    async def never(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        seen.append("missing")
        return await event_next(event)

    rig.bus.register_event_handler(TOOL_CALL, rewrite, {"tool_name": "upper"}, HandlerLevel.USER)
    rig.bus.register_event_handler(TOOL_CALL, never, {"tool_name": "missing"}, HandlerLevel.USER)

    await _trigger(rig)

    assert seen == ["upper"]
    sent = rig.invoke_original.await_args.args[0]
    assert sent[0] is rig.tool_calls[0]
    assert sent[1] is not rig.tool_calls[1]
    assert json.loads(sent[1].arguments) == {"text": "rewritten"}
    assert (sent[1].id, sent[1].name, sent[1].index) == ("call-2", "upper", 1)
    assert rig.tool_calls[1].arguments == '{"text": "b"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda items: items[:1],
        lambda items: tuple(reversed(items)),
        lambda items: (items[0], dataclasses.replace(items[1], tool_name="echo")),
        lambda items: (items[0], dataclasses.replace(items[1], tool_call_id="forged")),
    ],
    ids=["drop", "reorder", "rename", "forge-id"],
)
async def test_core_rejects_forwarded_batch_that_differs_from_trigger_context(mutate) -> None:
    rig = await _rig()

    async def tamper(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        return await event_next(Event(event.event_type, mutate(event.event_input)))

    rig.bus.register_event_handler(TOOL_CALL, tamper, {}, HandlerLevel.USER)

    with pytest.raises(BaseError):
        await _trigger(rig)
    assert rig.invoke_original.await_count == 0


@pytest.mark.asyncio
async def test_core_requires_tool_call_trigger_context() -> None:
    rig = await _rig()

    with pytest.raises(BaseError):
        await rig.bus.trigger(rig.event, rig.session)
    assert rig.invoke_original.await_count == 0


@pytest.mark.asyncio
async def test_invalid_handler_output_returns_cached_core_result_without_replay() -> None:
    rig = await _rig()

    async def corrupt(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        outputs = await event_next(event)
        return tuple(dataclasses.replace(output, text=123) for output in outputs)

    rig.bus.register_event_handler(TOOL_CALL, corrupt, {}, HandlerLevel.USER)

    outputs = await _trigger(rig)

    assert rig.invoke_original.await_count == 1
    assert all(isinstance(output, ToolCallEventOutput) for output in outputs)
    assert [output.text for output in outputs] == [message.content for _, message in rig.results]
