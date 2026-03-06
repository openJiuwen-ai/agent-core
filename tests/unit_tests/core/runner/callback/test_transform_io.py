# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for trigger_transform / on_transform / transform_io event-based pipeline.

Verifies:
- trigger_transform only fires callback_type="transform" callbacks
- Regular on() callbacks are not affected by trigger_transform
- No transform callbacks → invoke/stream behaves identically to before
- Transform callbacks can modify input arguments
- Transform callbacks can modify output results
- stream output event fires once per yielded item
"""

import pytest
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.runner.callback.decorator import _TRANSFORM_NOOP


# === trigger_transform basics ===

@pytest.mark.asyncio
async def test_trigger_transform_returns_noop_when_no_callbacks():
    """When no transform callbacks are registered, trigger_transform returns _TRANSFORM_NOOP."""
    fw = AsyncCallbackFramework(enable_logging=False)
    result = await fw.trigger_transform("some_event", "arg1")
    assert result is _TRANSFORM_NOOP


@pytest.mark.asyncio
async def test_trigger_transform_ignores_regular_callbacks():
    """Regular on() callbacks (callback_type='') are NOT invoked by trigger_transform."""
    fw = AsyncCallbackFramework(enable_logging=False)
    called = []

    @fw.on("my_event")
    async def regular_handler(x):
        called.append(x)
        return "regular"

    result = await fw.trigger_transform("my_event", 42)
    assert result is _TRANSFORM_NOOP
    assert called == []


@pytest.mark.asyncio
async def test_trigger_transform_runs_transform_callbacks():
    """trigger_transform runs callbacks registered with callback_type='transform'."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on("my_event", callback_type="transform")
    async def transform_handler(x):
        return x * 2

    result = await fw.trigger_transform("my_event", 5)
    assert result == 10


@pytest.mark.asyncio
async def test_trigger_transform_returns_last_result():
    """When multiple transform callbacks exist, trigger_transform returns the last result."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on("ev", callback_type="transform", priority=10)
    async def h1(x):
        return x + 1

    @fw.on("ev", callback_type="transform", priority=0)
    async def h2(x):
        return x * 100

    # priority=10 runs first (h1), priority=0 runs last (h2); last result is from h2
    result = await fw.trigger_transform("ev", 3)
    assert result == 300


@pytest.mark.asyncio
async def test_trigger_transform_coexists_with_regular_trigger():
    """trigger() skips transform callbacks; trigger_transform() runs only transform-type ones."""
    fw = AsyncCallbackFramework(enable_logging=False)
    regular_called = []
    transform_called = []

    @fw.on("ev")
    async def regular(x):
        regular_called.append(x)

    @fw.on("ev", callback_type="transform")
    async def transform(x):
        transform_called.append(x)
        return x

    # trigger() fires only non-transform callbacks; skips callback_type="transform"
    await fw.trigger("ev", 7)
    assert regular_called == [7]
    assert transform_called == []  # skipped by trigger()

    # trigger_transform() fires only transform-type; regular callback not called
    result = await fw.trigger_transform("ev", 9)
    assert result == 9
    assert regular_called == [7]   # still unchanged — trigger_transform skips it
    assert transform_called == [9]


# === on_transform convenience decorator ===

@pytest.mark.asyncio
async def test_on_transform_registers_transform_type():
    """on_transform registers a callback with callback_type='transform'."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("ev")
    async def handler(x):
        return x + 10

    # trigger_transform() picks it up
    result = await fw.trigger_transform("ev", 5)
    assert result == 15

    # trigger() skips transform-type callbacks
    results = await fw.trigger("ev", 5)
    assert results == []


# === transform_io via events — no registered callbacks ===

@pytest.mark.asyncio
async def test_transform_io_identity_when_no_transform_callbacks():
    """With no transform callbacks, transform_io is transparent (inputs and output unchanged)."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def add(a, b):
        return a + b

    assert await add(2, 3) == 5


@pytest.mark.asyncio
async def test_transform_io_stream_identity_when_no_transform_callbacks():
    """With no transform callbacks, transform_io stream is transparent."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def gen(n):
        for i in range(n):
            yield i

    items = [item async for item in gen(3)]
    assert items == [0, 1, 2]


# === transform_io — input modification ===

@pytest.mark.asyncio
async def test_transform_io_modifies_input():
    """Transform callback on input_event can modify positional arguments."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("in_ev")
    async def double_first(a, b):
        return ((a * 2, b), {})

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def add(a, b):
        return a + b

    # a is doubled from 3→6; b stays 4; result = 10
    assert await add(3, 4) == 10


@pytest.mark.asyncio
async def test_transform_io_modifies_output():
    """Transform callback on output_event can modify the function result."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("out_ev")
    async def negate(result):
        return -result

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def add(a, b):
        return a + b

    assert await add(2, 3) == -5


@pytest.mark.asyncio
async def test_transform_io_modifies_both_input_and_output():
    """Input and output transforms compose correctly."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("in_ev")
    async def increment_a(a, b):
        return ((a + 1, b), {})

    @fw.on_transform("out_ev")
    async def double_result(result):
        return result * 2

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def add(a, b):
        return a + b  # (a+1) + b, then *2

    assert await add(1, 2) == (2 + 2) * 2  # a=2, b=2 → 4*2=8


# === transform_io — stream output per-item ===

@pytest.mark.asyncio
async def test_transform_io_stream_output_fires_per_item():
    """Output transform fires independently for each yielded item."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("out_ev")
    async def square(result):
        return result ** 2

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def gen(n):
        for i in range(1, n + 1):
            yield i

    items = [item async for item in gen(4)]
    assert items == [1, 4, 9, 16]


@pytest.mark.asyncio
async def test_transform_io_stream_input_modifies_arg():
    """Input transform fires once before iteration and modifies the generator argument."""
    fw = AsyncCallbackFramework(enable_logging=False)

    @fw.on_transform("in_ev")
    async def double_n(n):
        return ((n * 2,), {})

    @fw.transform_io(input_event="in_ev", output_event="out_ev")
    async def gen(n):
        for i in range(n):
            yield i

    items = [item async for item in gen(3)]  # n becomes 6
    assert items == list(range(6))


# === Disabled transform callback is skipped ===

@pytest.mark.asyncio
async def test_disabled_transform_callback_is_skipped():
    """Disabled transform callbacks are not invoked."""
    fw = AsyncCallbackFramework(enable_logging=False)

    info_holder = []

    @fw.on("ev", callback_type="transform")
    async def handler(x):
        return x * 99

    # Grab the registered CallbackInfo and disable it
    for cb_info in fw.callbacks.get("ev", []):
        cb_info.enabled = False
        info_holder.append(cb_info)

    result = await fw.trigger_transform("ev", 5)
    assert result is _TRANSFORM_NOOP
