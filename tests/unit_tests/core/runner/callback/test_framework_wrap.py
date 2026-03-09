# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for wrap handlers: create_wrap_decorator, framework.on_wrap, framework.wrap.

Coverage:
- Static chain (create_wrap_decorator): single/multiple handlers, execution order,
  arg/result mutation, short-circuit, no-op, sync/async functions, async/sync generators,
  error propagation.
- Event-based chain (on_wrap / wrap): priority ordering, dynamic lookup after decoration,
  no-handler pass-through, generator support, storage in _callbacks, unregister.
"""

import pytest

from openjiuwen.core.runner.callback import AsyncCallbackFramework
from openjiuwen.core.runner.callback.decorator import (
    _WRAP_EVENT_PREFIX,
    create_on_wrap_decorator,
    create_wrap_by_event_decorator,
    create_wrap_decorator,
)


# ===========================================================================
# create_wrap_decorator — static chain, async functions
# ===========================================================================


@pytest.mark.asyncio
async def test_single_handler_executes_around_function():
    """Single handler runs before and after the wrapped function."""
    log = []

    async def handler(call_next, *args, **kwargs):
        log.append("before")
        result = await call_next(*args, **kwargs)
        log.append("after")
        return result

    @create_wrap_decorator(handler)
    async def func():
        log.append("func")
        return 42

    result = await func()
    assert result == 42
    assert log == ["before", "func", "after"]


@pytest.mark.asyncio
async def test_multiple_handlers_outermost_first():
    """handlers[0] is outermost: called first, completes last."""
    log = []

    async def h1(call_next, *args, **kwargs):
        log.append("h1_in")
        result = await call_next(*args, **kwargs)
        log.append("h1_out")
        return result

    async def h2(call_next, *args, **kwargs):
        log.append("h2_in")
        result = await call_next(*args, **kwargs)
        log.append("h2_out")
        return result

    @create_wrap_decorator(h1, h2)
    async def func():
        log.append("func")
        return 0

    await func()
    assert log == ["h1_in", "h2_in", "func", "h2_out", "h1_out"]


@pytest.mark.asyncio
async def test_handler_modifies_kwargs():
    """Handler can modify kwargs before passing to call_next."""

    async def add_one(call_next, *args, **kwargs):
        kwargs["n"] = kwargs.get("n", 0) + 1
        return await call_next(*args, **kwargs)

    @create_wrap_decorator(add_one)
    async def compute(n: int):
        return n

    assert await compute(n=5) == 6


@pytest.mark.asyncio
async def test_handler_modifies_result():
    """Handler can transform the result returned by call_next."""

    async def double(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result * 2

    @create_wrap_decorator(double)
    async def get():
        return 7

    assert await get() == 14


@pytest.mark.asyncio
async def test_handler_short_circuit():
    """Handler that never calls call_next; wrapped function is not reached."""
    reached = []

    async def blocker(call_next, *args, **kwargs):
        return "blocked"

    @create_wrap_decorator(blocker)
    async def func():
        reached.append(True)
        return "original"

    result = await func()
    assert result == "blocked"
    assert reached == []


@pytest.mark.asyncio
async def test_no_handlers_is_identity():
    """Zero handlers: decorator returns the original function unchanged."""

    @create_wrap_decorator()
    async def func(x):
        return x * 3

    assert await func(4) == 12


@pytest.mark.asyncio
async def test_error_in_wrapped_function_propagates():
    """Exception from the wrapped function bubbles through the chain."""

    async def passthrough(call_next, *args, **kwargs):
        return await call_next(*args, **kwargs)

    @create_wrap_decorator(passthrough)
    async def boom():
        raise ValueError("oops")

    with pytest.raises(ValueError, match="oops"):
        await boom()


@pytest.mark.asyncio
async def test_error_in_handler_propagates():
    """Exception raised inside a handler bubbles up to the caller."""

    async def bad_handler(call_next, *args, **kwargs):
        raise RuntimeError("handler failed")

    @create_wrap_decorator(bad_handler)
    async def func():
        return 1

    with pytest.raises(RuntimeError, match="handler failed"):
        await func()


@pytest.mark.asyncio
async def test_stacked_result_mutation():
    """Two handlers each mutate the result; composition order is respected."""

    async def add_10(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result + 10

    async def add_100(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result + 100

    # add_10 is outermost: func → add_100 → add_10
    # func returns 1 → add_100: 101 → add_10: 111
    @create_wrap_decorator(add_10, add_100)
    async def func():
        return 1

    assert await func() == 111


# ===========================================================================
# create_wrap_decorator — sync function
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_function_promoted_to_async():
    """Sync function is awaitable after decoration."""

    async def handler(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result + 1

    @create_wrap_decorator(handler)
    def sync_func(n):
        return n * 2

    assert await sync_func(3) == 7  # 3*2=6 + 1=7


# ===========================================================================
# create_wrap_decorator — async generator
# ===========================================================================


@pytest.mark.asyncio
async def test_async_generator_single_handler():
    """Handler iterates call_next and can transform yielded items."""

    async def double_items(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield item * 2

    @create_wrap_decorator(double_items)
    async def stream():
        yield 1
        yield 2
        yield 3

    items = []
    async for v in stream():
        items.append(v)
    assert items == [2, 4, 6]


@pytest.mark.asyncio
async def test_async_generator_chain_order():
    """Outermost handler's item transformation composes with inner handlers."""

    async def add_10(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield item + 10

    async def mul_2(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield item * 2

    # add_10 is outermost: stream → mul_2 → add_10
    # item=1 → *2=2 → +10=12
    @create_wrap_decorator(add_10, mul_2)
    async def stream():
        yield 1
        yield 2

    items = []
    async for v in stream():
        items.append(v)
    assert items == [12, 14]


@pytest.mark.asyncio
async def test_async_generator_handler_filter_items():
    """Handler can drop items by not yielding them."""

    async def only_even(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            if item % 2 == 0:
                yield item

    @create_wrap_decorator(only_even)
    async def stream():
        for i in range(5):
            yield i

    items = []
    async for v in stream():
        items.append(v)
    assert items == [0, 2, 4]


# ===========================================================================
# create_wrap_decorator — sync generator
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_generator_promoted_to_async_generator():
    """Sync generator becomes an async generator after decoration."""

    async def negate(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield -item

    @create_wrap_decorator(negate)
    def sync_gen():
        yield 1
        yield 2

    items = []
    async for v in sync_gen():
        items.append(v)
    assert items == [-1, -2]


# ===========================================================================
# framework.on_wrap / framework.wrap — event-based chain
# ===========================================================================


@pytest.mark.asyncio
async def test_single_handler(framework):
    """Single on_wrap handler executes around the wrapped function."""
    log = []

    @framework.on_wrap("greet")
    async def handler(call_next, *args, **kwargs):
        log.append("before")
        result = await call_next(*args, **kwargs)
        log.append("after")
        return result

    @framework.wrap("greet")
    async def greet(name: str):
        log.append("greet")
        return f"hello {name}"

    result = await greet("world")
    assert result == "hello world"
    assert log == ["before", "greet", "after"]


@pytest.mark.asyncio
async def test_priority_determines_outermost(framework):
    """Higher-priority handler is outermost (called first, exits last)."""
    log = []

    @framework.on_wrap("ev", priority=5)
    async def low(call_next, *args, **kwargs):
        log.append("low_in")
        result = await call_next(*args, **kwargs)
        log.append("low_out")
        return result

    @framework.on_wrap("ev", priority=20)
    async def high(call_next, *args, **kwargs):
        log.append("high_in")
        result = await call_next(*args, **kwargs)
        log.append("high_out")
        return result

    @framework.wrap("ev")
    async def func():
        log.append("func")
        return 0

    await func()
    assert log == ["high_in", "low_in", "func", "low_out", "high_out"]


@pytest.mark.asyncio
async def test_no_handler_passthrough(framework):
    """Wrapped function with no registered handlers runs normally."""

    @framework.wrap("empty_event")
    async def func(x):
        return x * 2

    assert await func(5) == 10


@pytest.mark.asyncio
async def test_dynamic_lookup_after_decoration(framework):
    """Handler registered AFTER @wrap is still included in the chain."""

    @framework.wrap("late")
    async def func():
        return 1

    # Register handler after decoration; must still be picked up
    @framework.on_wrap("late")
    async def handler(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result + 100

    assert await func() == 101


@pytest.mark.asyncio
async def test_handler_stored_in_callbacks_registry(framework):
    """WrapHandler is stored under '__wrap__:{event}' in framework.callbacks."""

    @framework.on_wrap("my_func")
    async def handler(call_next, *args, **kwargs):
        return await call_next(*args, **kwargs)

    key = f"{_WRAP_EVENT_PREFIX}my_func"
    assert key in framework.callbacks
    assert len(framework.callbacks[key]) == 1
    assert framework.callbacks[key][0].callback is handler


@pytest.mark.asyncio
async def test_handler_does_not_pollute_regular_event(framework):
    """WrapHandlers are isolated from regular event callbacks."""

    @framework.on_wrap("isolated")
    async def handler(call_next, *args, **kwargs):
        return await call_next(*args, **kwargs)

    # The logical event "isolated" must have no regular callbacks
    assert len(framework.callbacks.get("isolated", [])) == 0


@pytest.mark.asyncio
async def test_unregister_handler(framework):
    """Handler unregistered via framework.unregister is excluded from chain."""
    log = []

    @framework.on_wrap("unregister_ev")
    async def handler(call_next, *args, **kwargs):
        log.append("handler")
        return await call_next(*args, **kwargs)

    @framework.wrap("unregister_ev")
    async def func():
        return 1

    # Handler active
    await func()
    assert log == ["handler"]

    # Remove handler
    await framework.unregister(f"{_WRAP_EVENT_PREFIX}unregister_ev", handler)
    log.clear()

    await func()
    assert log == []


@pytest.mark.asyncio
async def test_handler_modifies_args_framework(framework):
    """Handler can pass different kwargs to call_next."""

    @framework.on_wrap("compute")
    async def double_n(call_next, *args, **kwargs):
        kwargs["n"] = kwargs.get("n", 0) * 2
        return await call_next(*args, **kwargs)

    @framework.wrap("compute")
    async def compute(n: int):
        return n

    assert await compute(n=3) == 6


@pytest.mark.asyncio
async def test_handler_modifies_result_framework(framework):
    """Handler can transform the result before returning."""

    @framework.on_wrap("process")
    async def stringify(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return f"value={result}"

    @framework.wrap("process")
    async def process(x):
        return x + 1

    assert await process(4) == "value=5"


@pytest.mark.asyncio
async def test_async_generator_handler(framework):
    """on_wrap handler for async generator uses async-generator signature."""

    @framework.on_wrap("stream")
    async def negate_items(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield -item

    @framework.wrap("stream")
    async def stream(n):
        for i in range(n):
            yield i

    items = []
    async for v in stream(3):
        items.append(v)
    assert items == [0, -1, -2]


@pytest.mark.asyncio
async def test_sync_generator_handler(framework):
    """on_wrap handler for sync generator; wrapper becomes async generator."""

    @framework.on_wrap("sync_stream")
    async def add_one(call_next, *args, **kwargs):
        async for item in call_next(*args, **kwargs):
            yield item + 1

    @framework.wrap("sync_stream")
    def sync_gen(n):
        for i in range(n):
            yield i * 10

    items = []
    async for v in sync_gen(3):
        items.append(v)
    assert items == [1, 11, 21]


@pytest.mark.asyncio
async def test_three_handlers_stacked(framework):
    """Three handlers compose in priority order."""

    @framework.on_wrap("stack", priority=1)
    async def h1(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result + 1   # innermost of the three

    @framework.on_wrap("stack", priority=10)
    async def h2(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result * 3   # middle

    @framework.on_wrap("stack", priority=20)
    async def h3(call_next, *args, **kwargs):
        result = await call_next(*args, **kwargs)
        return result - 5   # outermost

    @framework.wrap("stack")
    async def func():
        return 10   # 10 → +1=11 → *3=33 → -5=28

    assert await func() == 28


@pytest.mark.asyncio
async def test_error_propagates_through_handler(framework):
    """Exception from wrapped function propagates through on_wrap handler."""

    @framework.on_wrap("err_ev")
    async def passthrough(call_next, *args, **kwargs):
        return await call_next(*args, **kwargs)

    @framework.wrap("err_ev")
    async def failing():
        raise ValueError("fail")

    with pytest.raises(ValueError, match="fail"):
        await failing()


# ===========================================================================
# create_on_wrap_decorator / create_wrap_by_event_decorator — low-level API
# ===========================================================================


@pytest.mark.asyncio
async def test_register_and_wrap_via_factories(framework):
    """create_on_wrap_decorator and create_wrap_by_event_decorator work together."""
    calls = []

    @create_on_wrap_decorator(framework, "factory_ev")
    async def handler(call_next, *args, **kwargs):
        calls.append("handler")
        return await call_next(*args, **kwargs)

    @create_wrap_by_event_decorator(framework, "factory_ev")
    async def func():
        calls.append("func")
        return 99

    result = await func()
    assert result == 99
    assert calls == ["handler", "func"]


@pytest.mark.asyncio
async def test_priority_via_factory(framework):
    """priority kwarg in create_on_wrap_decorator controls chain position."""
    order = []

    @create_on_wrap_decorator(framework, "prio_ev", priority=1)
    async def h_low(call_next, *args, **kwargs):
        order.append("low")
        return await call_next(*args, **kwargs)

    @create_on_wrap_decorator(framework, "prio_ev", priority=10)
    async def h_high(call_next, *args, **kwargs):
        order.append("high")
        return await call_next(*args, **kwargs)

    @create_wrap_by_event_decorator(framework, "prio_ev")
    async def func():
        return 0

    await func()
    assert order == ["high", "low"]


@pytest.mark.asyncio
async def test_disabled_handler_skipped(framework):
    """Disabling a handler's CallbackInfo entry excludes it from the chain."""

    @framework.on_wrap("dis_ev")
    async def handler(call_next, *args, **kwargs):
        return "from_handler"

    @framework.wrap("dis_ev")
    async def func():
        return "from_func"

    # Disable the handler's CallbackInfo directly
    key = f"{_WRAP_EVENT_PREFIX}dis_ev"
    framework.callbacks[key][0].enabled = False

    result = await func()
    assert result == "from_func"
