# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework transform_io decorator.

Covers both direct callable (input_transform/output_transform) and
event-based (input_event/output_event) modes, for async/sync functions
and generators.
"""

import pytest

from openjiuwen.core.runner.callback import AsyncCallbackFramework
from openjiuwen.core.runner.callback.decorator import (
    create_transform_io_by_events_decorator,
    create_transform_io_decorator,
)


# ---------- Direct callable: create_transform_io_decorator ----------


@pytest.mark.asyncio
async def test_transform_io_decorator_async_input_output():
    """Transform async function with sync input and output transforms."""
    def add_one(*args, **kwargs):
        a, k = list(args), dict(kwargs)
        k["n"] = k.get("n", 0) + 1
        return (tuple(a), k)

    def double(x):
        return x * 2

    decorator = create_transform_io_decorator(
        input_transform=add_one,
        output_transform=double,
    )

    @decorator
    async def compute(n: int):
        return n

    result = await compute(0)
    assert result == 2  # input 0 -> n=1, output 1*2=2


@pytest.mark.asyncio
async def test_transform_io_decorator_input_only():
    """Only input transform; output unchanged."""
    def set_limit(*args, **kwargs):
        return args, {**kwargs, "limit": 10}

    decorator = create_transform_io_decorator(input_transform=set_limit)

    @decorator
    async def fetch(limit: int):
        return limit

    result = await fetch(5)  # kwargs get limit=10 from transform
    assert result == 10


@pytest.mark.asyncio
async def test_transform_io_decorator_output_only():
    """Only output transform; input unchanged."""
    def serialize(x):
        return str(x)

    decorator = create_transform_io_decorator(output_transform=serialize)

    @decorator
    async def get_value():
        return 42

    result = await get_value()
    assert result == "42"


@pytest.mark.asyncio
async def test_transform_io_decorator_async_generator():
    """Output transform applied to each yielded item."""
    def double(x):
        return x * 2

    decorator = create_transform_io_decorator(output_transform=double)

    @decorator
    async def stream():
        yield 1
        yield 2
        yield 3

    items = []
    async for v in stream():
        items.append(v)
    assert items == [2, 4, 6]


@pytest.mark.asyncio
async def test_transform_io_decorator_sync_function():
    """Sync function with sync transforms."""
    def set_n(*args, **kwargs):
        k = dict(kwargs)
        k["n"] = 3
        return (args, k)

    def triple(x):
        return x * 3

    decorator = create_transform_io_decorator(
        input_transform=set_n,
        output_transform=triple,
    )

    @decorator
    def sync_compute(**kwargs):
        return kwargs.get("n", 0)

    result = sync_compute()
    assert result == 9  # input gets n=3, output 3*3=9


@pytest.mark.asyncio
async def test_transform_io_decorator_no_transform():
    """No input/output transform; pass-through."""
    decorator = create_transform_io_decorator()

    @decorator
    async def identity(x: int):
        return x

    result = await identity(7)
    assert result == 7


# ---------- Event-based: create_transform_io_by_events_decorator ----------


@pytest.mark.asyncio
async def test_transform_io_by_events_input_output(framework):
    """Event-based input and output transform."""
    @framework.on("input_transform", callback_type="transform")
    async def normalize_input(*args, **kwargs):
        args_list, kw = list(args), dict(kwargs)
        kw.setdefault("limit", 10)
        return (tuple(args_list), kw)

    @framework.on("output_transform", callback_type="transform")
    async def serialize_output(result):
        return f"result:{result}"

    decorator = create_transform_io_by_events_decorator(
        framework,
        input_event="input_transform",
        output_event="output_transform",
    )

    @decorator
    async def fetch_data(limit: int):
        return {"count": limit}

    result = await fetch_data(5)
    assert result == "result:{'count': 10}"


@pytest.mark.asyncio
async def test_transform_io_by_events_input_only(framework):
    """Event-based input transform only."""
    @framework.on("input_event", callback_type="transform")
    async def add_one(*args, **kwargs):
        a, k = list(args), dict(kwargs)
        k["n"] = k.get("n", 0) + 1
        return (tuple(a), k)

    decorator = create_transform_io_by_events_decorator(
        framework,
        input_event="input_event",
    )

    @decorator
    async def compute(n: int):
        return n

    result = await compute(0)
    assert result == 1


@pytest.mark.asyncio
async def test_transform_io_by_events_output_only(framework):
    """Event-based output transform only."""
    @framework.on("output_event", callback_type="transform")
    async def double(result):
        return result * 2

    decorator = create_transform_io_by_events_decorator(
        framework,
        output_event="output_event",
    )

    @decorator
    async def get_value():
        return 21

    result = await get_value()
    assert result == 42


@pytest.mark.asyncio
async def test_transform_io_by_events_no_callbacks_passthrough(framework):
    """When no callbacks registered for event, pass through."""
    decorator = create_transform_io_by_events_decorator(
        framework,
        input_event="nonexistent_input",
        output_event="nonexistent_output",
    )

    @decorator
    async def identity(x: int):
        return x

    result = await identity(100)
    assert result == 100


@pytest.mark.asyncio
async def test_transform_io_by_events_last_callback_wins(framework):
    """Last executed callback return value is used (lowest priority runs last)."""
    @framework.on("out", priority=0, callback_type="transform")
    async def first(result):
        return result + 1

    @framework.on("out", priority=10, callback_type="transform")
    async def second(result):
        return result * 2

    decorator = create_transform_io_by_events_decorator(
        framework,
        output_event="out",
    )

    @decorator
    async def get():
        return 5

    # trigger_transform() runs in priority order (high first): second(5*2=10),
    # then first(5+1=6). Last executed = first → result = 6.
    result = await get()
    assert result == 6  # 5+1 from first (last executed, lowest priority)


@pytest.mark.asyncio
async def test_transform_io_by_events_async_generator(framework):
    """Event-based output transform on async generator items."""
    @framework.on("item", callback_type="transform")
    async def double(result):
        return result * 2

    decorator = create_transform_io_by_events_decorator(
        framework,
        output_event="item",
    )

    @decorator
    async def stream():
        yield 1
        yield 2

    items = []
    async for v in stream():
        items.append(v)
    assert items == [2, 4]


@pytest.mark.asyncio
async def test_transform_io_by_events_custom_result_key(framework):
    """Custom result_key for output event payload."""
    @framework.on("custom_out", callback_type="transform")
    async def transform(**kwargs):
        value = kwargs.get("value")
        return value * 3

    decorator = create_transform_io_by_events_decorator(
        framework,
        output_event="custom_out",
        result_key="value",
    )

    @decorator
    async def get():
        return 7

    result = await get()
    assert result == 21


# ---------- Framework.transform_io (direct callable) ----------


@pytest.mark.asyncio
async def test_framework_transform_io_callable(framework):
    """Framework.transform_io with input_transform/output_transform."""

    def normalize(*args, **kwargs):
        return args, {**kwargs, "limit": 10}

    def to_str(result):
        return str(result)

    @framework.transform_io(
        input_transform=normalize,
        output_transform=to_str,
    )
    async def fetch(limit: int):
        return limit

    result = await fetch(5)
    assert result == "10"


@pytest.mark.asyncio
async def test_framework_transform_io_events(framework):
    """Framework.transform_io with input_event/output_event."""

    @framework.on("in_ev", callback_type="transform")
    async def in_cb(*args, **kwargs):
        base = (args[0] if args else kwargs.get("x", 0))
        return args, {**kwargs, "x": base + 1}

    @framework.on("out_ev", callback_type="transform")
    async def out_cb(result):
        return result + 100

    @framework.transform_io(
        input_event="in_ev",
        output_event="out_ev",
    )
    async def compute(x: int):
        return x

    result = await compute(1)
    assert result == 102  # x->2, 2+100=102


@pytest.mark.asyncio
async def test_framework_transform_io_events_prefer_over_callable(framework):
    """When both event and callable given, event is used."""
    @framework.on("ev", callback_type="transform")
    async def event_cb(result):
        return "from_event"

    def callable_cb(result):
        return "from_callable"

    @framework.transform_io(
        output_event="ev",
        output_transform=callable_cb,
    )
    async def get():
        return "raw"

    result = await get()
    assert result == "from_event"


@pytest.mark.asyncio
async def test_framework_transform_io_sync_generator_with_events(framework):
    """Event-based transform on sync generator (wrapper becomes async)."""
    @framework.on("item", callback_type="transform")
    async def double(result):
        return result * 2

    @framework.transform_io(output_event="item")
    def sync_stream():
        yield 10
        yield 20

    items = []
    async for v in sync_stream():
        items.append(v)
    assert items == [20, 40]


# ---------- Edge cases ----------


@pytest.mark.asyncio
async def test_transform_io_decorator_sync_generator_direct():
    """Direct callable: sync generator with output transform."""
    def negate(x):
        return -x

    decorator = create_transform_io_decorator(output_transform=negate)

    @decorator
    def sync_gen():
        yield 1
        yield 2

    items = list(sync_gen())
    assert items == [-1, -2]


@pytest.mark.asyncio
async def test_transform_io_decorator_async_input_transform():
    """Async input transform with direct decorator."""
    async def add_one_async(*args, **kwargs):
        a, k = list(args), dict(kwargs)
        k["n"] = k.get("n", 0) + 1
        return (tuple(a), k)

    decorator = create_transform_io_decorator(input_transform=add_one_async)

    @decorator
    async def compute(n: int):
        return n

    result = await compute(0)
    assert result == 1


@pytest.mark.asyncio
async def test_transform_io_by_events_input_tuple_return(framework):
    """Input event callback returns (args, kwargs) tuple."""
    @framework.on("in", callback_type="transform")
    async def return_tuple(*args, **kwargs):
        return ((1, 2), {"a_key": 3})

    decorator = create_transform_io_by_events_decorator(
        framework,
        input_event="in",
    )

    @decorator
    async def consume(a: int, b: int, a_key: int = 0):
        return a + b + a_key

    result = await consume(0, 0)
    assert result == 6  # a=1, b=2, a_key=3


# === Generator output mode ===


@pytest.mark.asyncio
async def test_transform_io_generator_mode_expand():
    """Direct callable: async gen + output_mode='generator' expands each item to two."""

    async def expand(source):
        async for item in source:
            yield item
            yield item * 10

    @create_transform_io_decorator(output_transform=expand, output_mode="generator")
    async def gen():
        for i in range(1, 4):
            yield i

    items = [item async for item in gen()]
    assert items == [1, 10, 2, 20, 3, 30]


@pytest.mark.asyncio
async def test_transform_io_generator_mode_filter():
    """Direct callable: async gen + output_mode='generator' filters out even items."""

    async def keep_odd(source):
        async for item in source:
            if item % 2 != 0:
                yield item

    @create_transform_io_decorator(output_transform=keep_odd, output_mode="generator")
    async def gen():
        for i in range(1, 6):
            yield i

    items = [item async for item in gen()]
    assert items == [1, 3, 5]


@pytest.mark.asyncio
async def test_transform_io_generator_mode_stateful():
    """Direct callable: async gen + output_mode='generator' accumulates running total."""

    async def running_total(source):
        total = 0
        async for item in source:
            total += item
            yield total

    @create_transform_io_decorator(
        output_transform=running_total, output_mode="generator"
    )
    async def gen():
        for i in [1, 2, 3, 4]:
            yield i

    items = [item async for item in gen()]
    assert items == [1, 3, 6, 10]


@pytest.mark.asyncio
async def test_transform_io_generator_mode_no_output_transform():
    """Direct callable: output_mode='generator', output_transform=None passes source through."""

    @create_transform_io_decorator(output_transform=None, output_mode="generator")
    async def gen():
        for i in range(3):
            yield i

    items = [item async for item in gen()]
    assert items == [0, 1, 2]


@pytest.mark.asyncio
async def test_transform_io_generator_mode_sync_gen():
    """Direct callable: sync gen + output_mode='generator' is promoted to async gen."""

    async def double(source):
        async for item in source:
            yield item * 2

    @create_transform_io_decorator(output_transform=double, output_mode="generator")
    def sync_gen():
        yield from range(3)

    items = [item async for item in sync_gen()]
    assert items == [0, 2, 4]


def test_transform_io_generator_mode_invalid_for_non_gen():
    """Direct callable: output_mode='generator' on a non-generator raises ValueError."""

    with pytest.raises(ValueError, match="output_mode='generator'"):

        @create_transform_io_decorator(output_mode="generator")
        async def not_a_gen():
            return 42


@pytest.mark.asyncio
async def test_transform_io_frame_mode_unchanged():
    """Direct callable: output_mode='frame' (default) keeps existing frame-by-frame behavior."""

    @create_transform_io_decorator(
        output_transform=lambda x: x * 2, output_mode="frame"
    )
    async def gen():
        for i in range(1, 4):
            yield i

    items = [item async for item in gen()]
    assert items == [2, 4, 6]


@pytest.mark.asyncio
async def test_transform_io_events_generator_mode_expand(framework):
    """Events path: async gen + output_mode='generator' expands each item to two."""

    @framework.on_transform("out_ev")
    async def expand(result):
        async def _gen(src):
            async for item in src:
                yield item
                yield item * 10

        return _gen(result)

    @framework.transform_io(output_event="out_ev", output_mode="generator")
    async def gen():
        for i in range(1, 3):
            yield i

    items = [item async for item in gen()]
    assert items == [1, 10, 2, 20]


@pytest.mark.asyncio
async def test_transform_io_events_generator_mode_filter(framework):
    """Events path: async gen + output_mode='generator' filters items."""

    @framework.on_transform("out_ev")
    async def keep_odd(result):
        async def _gen(src):
            async for item in src:
                if item % 2 != 0:
                    yield item

        return _gen(result)

    @framework.transform_io(output_event="out_ev", output_mode="generator")
    async def gen():
        for i in range(1, 6):
            yield i

    items = [item async for item in gen()]
    assert items == [1, 3, 5]


@pytest.mark.asyncio
async def test_transform_io_events_generator_mode_no_callbacks(framework):
    """Events path: no callbacks registered → source passes through unchanged."""

    @framework.transform_io(output_event="out_ev", output_mode="generator")
    async def gen():
        for i in range(3):
            yield i

    items = [item async for item in gen()]
    assert items == [0, 1, 2]


@pytest.mark.asyncio
async def test_transform_io_events_generator_mode_sync_gen(framework):
    """Events path: sync gen + output_mode='generator' is promoted to async gen."""

    @framework.on_transform("out_ev")
    async def double(result):
        async def _gen(src):
            async for item in src:
                yield item * 2

        return _gen(result)

    @framework.transform_io(output_event="out_ev", output_mode="generator")
    def sync_gen():
        yield from range(3)

    items = [item async for item in sync_gen()]
    assert items == [0, 2, 4]
