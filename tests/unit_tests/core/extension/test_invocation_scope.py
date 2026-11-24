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

    await runtime._tracked_handler("demo", 1, handler)(None, ops, None)
    assert not runtime.is_active_invocation(ops)
    assert not copied[0].run(runtime.is_active_invocation, ops)
    assert not runtime._active_ops and not runtime._inflight and not runtime._ops_generations


@pytest.mark.asyncio
async def test_nested_handler_scope_restores_parent_only_after_inner_returns():
    runtime = Runtime()
    outer_ops, inner_ops = _ops(), _ops()

    async def inner(event, current, next_handler):
        assert runtime.is_active_invocation(inner_ops)
        assert not runtime.is_active_invocation(outer_ops)

    async def outer(event, current, next_handler):
        assert runtime.is_active_invocation(outer_ops)
        await runtime._tracked_handler("demo", 1, inner)(None, inner_ops, None)
        assert runtime.is_active_invocation(outer_ops)
        assert not runtime.is_active_invocation(inner_ops)

    await runtime._tracked_handler("demo", 1, outer)(None, outer_ops, None)
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
        await runtime._tracked_handler("demo", 1, handler)(None, ops, None)
    assert not copied[0].run(runtime.is_active_invocation, ops)
    assert not runtime._active_ops and not runtime._inflight and not runtime._ops_generations


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
        runtime._tracked_handler("a", 1, first)(None, first_ops, None),
        runtime._tracked_handler("b", 1, second)(None, second_ops, None),
    )
    assert not runtime._active_ops and not runtime._inflight and not runtime._ops_generations


def test_scope_query_outside_event_loop_is_false():
    assert not Runtime().is_active_invocation(_ops())


@pytest.mark.asyncio
async def test_generation_waiters_and_task_scopes_keep_independent_lifetimes():
    runtime = Runtime()
    outer_ops, inner_ops = _ops(), _ops()
    waiters = []

    async def inner(event, current, next_handler):
        assert runtime.is_active_invocation(inner_ops)
        assert not runtime.is_active_invocation(outer_ops)
        assert runtime.invocation_generation(inner_ops) == 8
        assert runtime.invocation_generation(outer_ops) == 7
        waiter = asyncio.create_task(runtime.wait_generation_idle("demo", 8))
        await asyncio.sleep(0)
        assert not waiter.done()
        waiters.append(waiter)

    async def outer(event, current, next_handler):
        assert runtime.is_active_invocation(outer_ops)
        assert runtime.invocation_generation(outer_ops) == 7
        old_generation = asyncio.create_task(runtime.wait_generation_idle("demo", 7))
        await asyncio.sleep(0)
        await runtime._tracked_handler("demo", 8, inner)(None, inner_ops, None)
        await asyncio.wait_for(waiters[0], timeout=1)
        assert runtime.invocation_generation(inner_ops) is None
        assert runtime.is_active_invocation(outer_ops)
        assert not old_generation.done()
        waiters.append(old_generation)

    await runtime._tracked_handler("demo", 7, outer)(None, outer_ops, None)
    await asyncio.wait_for(waiters[1], timeout=1)
    assert runtime.invocation_generation(outer_ops) is None
    assert not runtime.is_active_invocation(outer_ops)
