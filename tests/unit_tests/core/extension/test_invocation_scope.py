# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime owns package invocation validity without changing the EventBus."""

import asyncio
from contextvars import copy_context

import pytest

from openjiuwen.core.events import EventHandlerOpsBuilder
from openjiuwen.core.extension.runtime import Runtime


def _ops():
    return EventHandlerOpsBuilder().build()


@pytest.mark.asyncio
async def test_scope_rejects_children_forged_ops_and_copied_context_after_return():
    runtime = Runtime()
    other_runtime = Runtime()
    ops = _ops()
    copied = []

    async def handler(event, current, next_handler):
        assert runtime.is_active_invocation(current)
        assert not runtime.is_active_invocation(_ops())
        assert not other_runtime.is_active_invocation(current)

        async def child():
            assert not runtime.is_active_invocation(current)

        await asyncio.create_task(child())
        copied.append(copy_context())

    await runtime._tracked_handler("demo", handler)(None, ops, None)
    assert not runtime.is_active_invocation(ops)
    assert not copied[0].run(runtime.is_active_invocation, ops)
    assert not runtime._active_ops and not runtime._inflight


@pytest.mark.asyncio
async def test_nested_handler_scope_restores_parent_only_after_inner_returns():
    runtime = Runtime()
    outer_ops, inner_ops = _ops(), _ops()

    async def inner(event, current, next_handler):
        assert runtime.is_active_invocation(inner_ops)
        assert not runtime.is_active_invocation(outer_ops)

    async def outer(event, current, next_handler):
        assert runtime.is_active_invocation(outer_ops)
        await runtime._tracked_handler("demo", inner)(None, inner_ops, None)
        assert runtime.is_active_invocation(outer_ops)
        assert not runtime.is_active_invocation(inner_ops)

    await runtime._tracked_handler("demo", outer)(None, outer_ops, None)
    assert not runtime.is_active_invocation(outer_ops)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
async def test_failure_closes_scope_and_releases_existing_package_accounting(failure):
    runtime = Runtime()
    ops = _ops()
    copied = []

    async def handler(event, current, next_handler):
        copied.append(copy_context())
        raise failure()

    with pytest.raises(failure):
        await runtime._tracked_handler("demo", handler)(None, ops, None)
    assert not copied[0].run(runtime.is_active_invocation, ops)
    assert not runtime._active_ops and not runtime._inflight


@pytest.mark.asyncio
async def test_concurrent_handlers_cannot_use_each_others_ops():
    runtime = Runtime()
    first_ops, second_ops = _ops(), _ops()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first(event, current, next_handler):
        entered.set()
        await release.wait()
        assert runtime.is_active_invocation(first_ops)
        assert not runtime.is_active_invocation(second_ops)

    async def second(event, current, next_handler):
        await entered.wait()
        assert runtime.is_active_invocation(second_ops)
        assert not runtime.is_active_invocation(first_ops)
        release.set()

    await asyncio.gather(
        runtime._tracked_handler("a", first)(None, first_ops, None),
        runtime._tracked_handler("b", second)(None, second_ops, None),
    )
    assert not runtime._active_ops and not runtime._inflight


def test_scope_query_outside_event_loop_is_false():
    assert not Runtime().is_active_invocation(_ops())
