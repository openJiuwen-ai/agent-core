# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for tool-task cancellation handling in AbilityManager.

Covers ``_await_tool_task``'s contract around ``asyncio.CancelledError``:

- an outer-scope cancellation (``task.cancel()`` / ``asyncio.timeout``) must
  propagate so the run aborts instead of continuing to call the model;
- a self-cancelled tool task (outer task not cancelling) must map back to a
  ``CancelledError`` value so it can be surfaced as an "Interrupted" tool
  message;
- ordinary exceptions must keep mapping to values (gather parity).
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager


@pytest.mark.asyncio
async def test_await_tool_task_propagates_outer_cancellation():
    """Outer task.cancel() must re-raise CancelledError, not swallow it."""
    started: list = []

    async def tool_coro() -> str:
        await asyncio.sleep(10)
        return "unreachable"

    outer = asyncio.current_task()

    async def canceller() -> None:
        await asyncio.sleep(0.05)
        outer.cancel()

    canceller_task = asyncio.create_task(canceller())
    try:
        with pytest.raises(asyncio.CancelledError):
            await AbilityManager._await_tool_task(
                tool_coro(), on_task_started=started.append
            )
    finally:
        for task in started:
            task.cancel()
        canceller_task.cancel()
        await asyncio.gather(*started, canceller_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_await_tool_task_returns_self_cancelled_tool_as_value():
    """A tool task cancelled on its own (outer task not cancelling) maps to a
    CancelledError value rather than aborting the whole run."""
    started: list = []

    async def tool_coro() -> str:
        await asyncio.sleep(10)
        return "unreachable"

    async def canceller() -> None:
        # Cancel the tool task itself once it has been scheduled.
        for _ in range(1000):
            if started:
                started[0].cancel()
                return
            await asyncio.sleep(0.005)

    canceller_task = asyncio.create_task(canceller())
    try:
        result = await AbilityManager._await_tool_task(
            tool_coro(), on_task_started=started.append
        )
        assert isinstance(result, asyncio.CancelledError)
    finally:
        canceller_task.cancel()
        await asyncio.gather(canceller_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_await_tool_task_maps_ordinary_exception_to_value():
    """Ordinary exceptions must still map to values (gather parity)."""

    async def tool_coro() -> str:
        raise ValueError("boom")

    result = await AbilityManager._await_tool_task(tool_coro())
    assert isinstance(result, ValueError)
