#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
"""Unit tests for LocalFunction sync-tool event-loop safety.

Verifies that sync functions and sync generators wrapped by @tool run in the
default worker pool (anyio.to_thread) instead of the event loop thread, so a
long-running tool no longer stalls the whole process, while keeping result
values, exception propagation, and contextvars propagation unchanged.
"""
import asyncio
import contextlib
import contextvars
import time
from unittest import IsolatedAsyncioTestCase

from openjiuwen.core.foundation.tool.tool import tool

# Heartbeat interval kept small so a responsive loop records many ticks while
# the blocking work runs; a stalled loop records none.
_HEARTBEAT_INTERVAL = 0.05
_TOOL_SLEEP = 0.4


@tool
def slow_sync_tool(seconds: float) -> str:
    """Sync tool doing blocking IO-like work (modeled by time.sleep)."""
    time.sleep(seconds)
    return "done"


@tool
def failing_sync_tool() -> str:
    """Sync tool raising an exception."""
    raise ValueError("sync tool failure")


@tool
def slow_sync_gen_tool(count: int, step_seconds: float):
    """Sync generator yielding values with blocking work between items."""
    for i in range(count):
        time.sleep(step_seconds)
        yield i


@tool
def failing_sync_gen_tool():
    """Sync generator raising mid-iteration."""
    yield 1
    raise RuntimeError("sync generator failure")


async def _run_with_heartbeat(coro, min_ticks: int):
    """Await ``coro`` while counting event-loop ticks; fail if the loop stalls.

    The heartbeat task sleeps in small increments on the loop; if the loop is
    blocked by the awaited work, no ticks accumulate for its whole duration.
    """
    ticks = []

    async def heartbeat():
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            ticks.append(1)

    hb = asyncio.create_task(heartbeat())
    try:
        result = await coro
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    assert len(ticks) >= min_ticks, (
        f"event loop stalled: {len(ticks)} heartbeat ticks "
        f"(expected >= {min_ticks})"
    )
    return result


class TestLocalFunctionSyncEventLoopSafety(IsolatedAsyncioTestCase):
    async def test_sync_invoke_result_and_kwargs(self):
        """Sync tool result and keyword-argument binding are unchanged."""
        result = await slow_sync_tool.invoke(inputs={"seconds": 0})
        self.assertEqual(result, "done")

    async def test_sync_invoke_does_not_block_event_loop(self):
        """A sleeping sync tool must not stall the loop: heartbeat keeps ticking.

        0.4s of blocking work at a 0.05s heartbeat interval yields >= 7 ticks
        on a responsive loop; a blocked loop would record 0.
        """
        result = await _run_with_heartbeat(
            slow_sync_tool.invoke(inputs={"seconds": _TOOL_SLEEP}),
            min_ticks=3,
        )
        self.assertEqual(result, "done")

    async def test_sync_invoke_exception_propagates(self):
        """Exceptions raised in the worker thread re-raise on the awaiting side."""
        with self.assertRaises(ValueError):
            await failing_sync_tool.invoke(inputs={})

    async def test_sync_invoke_propagates_contextvars(self):
        """Context set on the event loop side is visible inside the sync tool."""
        var = contextvars.ContextVar("ut_ctx_var", default="unset")

        @tool
        def read_context() -> str:
            return var.get()

        var.set("ctx-propagated")
        result = await read_context.invoke(inputs={})
        self.assertEqual(result, "ctx-propagated")

    async def test_sync_generator_stream_order(self):
        """Sync generator stream yields items in order via the worker pool."""
        got = [item async for item in slow_sync_gen_tool.stream(
            inputs={"count": 3, "step_seconds": 0})]
        self.assertEqual(got, [0, 1, 2])

    async def test_sync_generator_stream_does_not_block_event_loop(self):
        """Slow-producing sync generator keeps the loop responsive per item.

        3 items x 0.4s blocking work each at a 0.05s heartbeat: a responsive
        loop records well over 6 ticks; a stalled loop records 0-1.
        """
        async def consume():
            return [item async for item in slow_sync_gen_tool.stream(
                inputs={"count": 3, "step_seconds": _TOOL_SLEEP})]

        got = await _run_with_heartbeat(consume(), min_ticks=6)
        self.assertEqual(got, [0, 1, 2])

    async def test_sync_generator_exception_propagates(self):
        """Exceptions raised mid-stream re-raise on the consuming side."""
        got = []
        with self.assertRaises(RuntimeError):
            async for item in failing_sync_gen_tool.stream(inputs={}):
                got.append(item)
        self.assertEqual(got, [1])
