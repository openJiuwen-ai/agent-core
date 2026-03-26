# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for emit_before / emit_after / emit_around with sync functions
and sync generators (promoted to async).
"""

import pytest


# === emit_before — sync ===


@pytest.mark.asyncio
async def test_emit_before_sync_function(framework):
    """Sync plain function decorated with emit_before is promoted to async."""
    triggered = []

    @framework.on("before_sync")
    async def handler(x):
        triggered.append(x)

    @framework.emit_before("before_sync")
    def compute(x):
        return x * 2

    result = await compute(5)

    assert result == 10
    assert triggered == [5]


@pytest.mark.asyncio
async def test_emit_before_sync_generator(framework):
    """Sync generator decorated with emit_before is promoted to async generator."""
    triggered = []

    @framework.on("before_gen")
    async def handler(**kwargs):
        triggered.append("fired")

    @framework.emit_before("before_gen", pass_args=False)
    def generate():
        yield 1
        yield 2
        yield 3

    items = []
    async for item in generate():
        items.append(item)

    assert items == [1, 2, 3]
    assert triggered == ["fired"]


# === emit_after — sync ===


@pytest.mark.asyncio
async def test_emit_after_sync_function(framework):
    """Sync plain function decorated with emit_after is promoted to async."""
    received_results = []

    @framework.on("after_sync")
    async def handler(result):
        received_results.append(result)

    @framework.emit_after("after_sync")
    def compute(x):
        return x + 1

    result = await compute(10)

    assert result == 11
    assert received_results == [11]


@pytest.mark.asyncio
async def test_emit_after_sync_generator_per_item(framework):
    """Sync generator with emit_after per_item triggers event for each item."""
    received_items = []

    @framework.on("after_item")
    async def handler(item):
        received_items.append(item)

    @framework.emit_after("after_item")
    def generate():
        yield "a"
        yield "b"

    items = []
    async for item in generate():
        items.append(item)

    assert items == ["a", "b"]
    assert received_items == ["a", "b"]


@pytest.mark.asyncio
async def test_emit_after_sync_generator_once(framework):
    """Sync generator with emit_after once triggers event after all items."""
    received = []

    @framework.on("after_all")
    async def handler(result):
        received.append(result)

    @framework.emit_after("after_all", stream_mode="once")
    def generate():
        yield 10
        yield 20

    items = []
    async for item in generate():
        items.append(item)

    assert items == [10, 20]
    assert received == [[10, 20]]


@pytest.mark.asyncio
async def test_emit_after_sync_generator_per_item_pass_args(framework):
    """Sync generator with emit_after per_item and pass_args=True
    passes each item along with original arguments to the handler.
    """
    received = []

    @framework.on("after_item_args")
    async def handler(*args, **kwargs):
        received.append({"args": args, "kwargs": kwargs})

    @framework.emit_after(
        "after_item_args",
        pass_args=True,
        stream_mode="per_item",
    )
    async def generate(prefix, sep="-"):
        yield f"{prefix}{sep}1"
        yield f"{prefix}{sep}2"

    items = []
    async for item in generate("val", sep=":"):
        items.append(item)

    assert items == ["val:1", "val:2"]
    assert len(received) == 2
    for entry in received:
        assert entry["args"] == ("val",)
        assert entry["kwargs"]["sep"] == ":"
    assert received[0]["kwargs"]["item"] == "val:1"
    assert received[1]["kwargs"]["item"] == "val:2"


@pytest.mark.asyncio
async def test_emit_after_gen_per_item_pass_args(framework):
    received_args = []
    received_result = []

    @framework.on("end", priority=50)
    async def on_end(num, item):
        received_args.append(num)
        received_result.append(item)

    @framework.on("complete", priority=50)
    async def on_complete(num, result):
        received_args.append(num)
        received_result.append(result)

    @framework.emit_after("end", pass_args=True, stream_mode="per_item")
    async def generate(num):
        for i in range(num):
            yield i * 2

    async for _n in generate(3):
        pass

    assert received_args == [3, 3, 3]
    assert received_result == [0, 2, 4]

# === emit_around — sync ===


@pytest.mark.asyncio
async def test_emit_around_sync_function(framework):
    """Sync plain function decorated with emit_around is promoted to async."""
    events = []

    @framework.on("start")
    async def on_start(x):
        events.append(("start", x))

    @framework.on("end")
    async def on_end(x, result):
        events.append(("end", result))

    @framework.emit_around("start", "end")
    def compute(x):
        return x * 3

    result = await compute(4)

    assert result == 12
    assert events[0] == ("start", 4)
    assert events[1] == ("end", 12)


@pytest.mark.asyncio
async def test_emit_around_sync_function_error(framework):
    """Sync function with emit_around triggers on_error_event on exception."""
    errors = []

    @framework.on("err")
    async def on_error(error):
        errors.append(str(error))

    @framework.emit_around("s", "e", on_error_event="err")
    def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await failing()

    assert len(errors) == 1
    assert "boom" in errors[0]


@pytest.mark.asyncio
async def test_emit_around_sync_generator(framework):
    """Sync generator decorated with emit_around is promoted to async generator."""
    events = []

    @framework.on("gen_start")
    async def on_start(**kwargs):
        events.append("start")

    @framework.on("gen_end")
    async def on_end(result, **kwargs):
        events.append(("end", result))

    @framework.emit_around("gen_start", "gen_end", pass_args=False)
    def generate():
        yield "x"
        yield "y"

    items = []
    async for item in generate():
        items.append(item)

    assert items == ["x", "y"]
    assert events[0] == "start"
    assert events[1] == ("end", ["x", "y"])
