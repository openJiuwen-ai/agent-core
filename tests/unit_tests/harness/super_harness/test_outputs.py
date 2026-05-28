# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify the queue-backed AsyncIterator contract of SuperHarness.outputs()."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.harness.super_harness.outputs import _END, _OutputIterator


@pytest.mark.asyncio
async def test_iterator_yields_items_until_sentinel() -> None:
    """Items pushed before _END are yielded; _END terminates iteration."""
    q: asyncio.Queue = asyncio.Queue()
    await q.put("a")
    await q.put("b")
    await q.put("c")
    await q.put(_END)

    it = _OutputIterator(q)
    collected = [x async for x in it]
    assert collected == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_iterator_blocks_until_item_arrives() -> None:
    """get() blocks until an item is pushed; then sentinel ends iteration."""
    q: asyncio.Queue = asyncio.Queue()

    async def push_after_delay() -> None:
        await asyncio.sleep(0.02)
        await q.put("late")
        await q.put(_END)

    it = _OutputIterator(q)
    asyncio.create_task(push_after_delay())
    collected = [x async for x in it]
    assert collected == ["late"]


@pytest.mark.asyncio
async def test_empty_after_sentinel() -> None:
    """An iterator with only _END returns no items."""
    q: asyncio.Queue = asyncio.Queue()
    await q.put(_END)

    it = _OutputIterator(q)
    collected = [x async for x in it]
    assert collected == []
