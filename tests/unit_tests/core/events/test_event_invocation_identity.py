# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Invocation-local exclusions must preserve other hooks and the CORE tail."""

import asyncio

import pytest

from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel, SessionContext


@pytest.mark.asyncio
async def test_exclude_one_handler_keeps_other_handlers_and_core():
    bus = ExtensionEventBus()
    trace = []
    async def core(event, ops, next_handler):
        trace.append("core")
        return "result"
    bus.register_event_type("test.identity", lambda value, pattern: True, core)
    async def first(event, ops, next_handler):
        trace.append("first")
        return await next_handler(event)
    async def second(event, ops, next_handler):
        trace.append("second")
        return await next_handler(event)
    first_id = bus.register_event_handler("test.identity", first, {}, HandlerLevel.USER)
    bus.register_event_handler("test.identity", second, {}, HandlerLevel.USER)
    await bus.trigger(Event("ops.build", None))
    assert await bus.trigger(Event("test.identity", {}), SessionContext("a"), exclude_handler_ids=[first_id]) == "result"
    assert trace == ["second", "core"]


@pytest.mark.asyncio
async def test_handler_identity_is_bound_for_scope_and_cannot_escape_task():
    bus = ExtensionEventBus()
    captured = []
    async def core(event, ops, next_handler):
        return None
    bus.register_event_type("test.identity", lambda value, pattern: True, core)
    async def user(event, ops, next_handler):
        assert ops.handler_id == handler_id
        assert ops.invocation_active
        async def child():
            assert not ops.invocation_active
        await asyncio.create_task(child())
        captured.append(ops)
        return await next_handler(event)
    handler_id = bus.register_event_handler("test.identity", user, {}, HandlerLevel.USER)
    await bus.trigger(Event("ops.build", None))
    await bus.trigger(Event("test.identity", {}), SessionContext("a"))
    assert len(captured) == 1 and not captured[0].invocation_active


@pytest.mark.asyncio
async def test_ops_build_rejects_call_local_exclusion():
    bus = ExtensionEventBus()
    with pytest.raises(Exception):
        await bus.trigger(Event("ops.build", None), exclude_handler_ids=[])
