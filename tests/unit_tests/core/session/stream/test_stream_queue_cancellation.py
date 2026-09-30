# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.session.stream.emitter import AsyncStreamQueue


@pytest.mark.asyncio
async def test_send_propagates_caller_cancellation():
    """Cancelling a producer that sends chunk after chunk must stop it.

    ``send`` runs once per stream chunk; if it dropped the caller's
    cancellation (3.11 ``wait_for``), a hard-cancelled round would keep going.
    """
    # Bounded and drained concurrently, so every ``put`` actually suspends and
    # is completed by the consumer: the tick where the swallow could happen.
    queue = AsyncStreamQueue(maxsize=1)

    async def drain() -> None:
        while True:
            await queue._stream_queue.get()

    drainer = asyncio.create_task(drain())

    async def produce(started: asyncio.Event) -> int:
        for i in range(5000):
            await queue.send(i)
            if i == 5:
                started.set()
        return i

    for delay_ticks in range(60):
        started = asyncio.Event()
        task = asyncio.create_task(produce(started))
        await started.wait()
        for _ in range(delay_ticks % 5):
            await asyncio.sleep(0)
        task.cancel()
        try:
            sent = await task
        except asyncio.CancelledError:
            continue
        drainer.cancel()
        raise AssertionError(f"cancellation swallowed; producer sent {sent} items")
    drainer.cancel()


@pytest.mark.asyncio
async def test_send_still_retries_on_full_queue_timeout():
    queue = AsyncStreamQueue(maxsize=1)
    await queue.send("first")

    # Queue stays full: every attempt times out, send gives up without raising.
    await queue.send("second", attempt_timeout=0.01, max_retries=2)

    assert queue._sent_count == 1
