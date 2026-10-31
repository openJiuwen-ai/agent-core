# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancelling a producer blocked in send() must actually cancel it."""

import asyncio

import pytest

from openjiuwen.core.session.stream.emitter import AsyncStreamQueue

TRIALS = 20
CHUNKS = 50


@pytest.mark.asyncio
async def test_cancel_after_receive_cancels_producer() -> None:
    # The consumer cancels right after get() wakes, i.e. after put() finished
    # but before send() resumed. On 3.11, asyncio.wait_for swallowed that
    # cancel (gh-86296) and the producer ran to completion.
    for _ in range(TRIALS):
        queue = AsyncStreamQueue()

        async def produce() -> None:
            for i in range(CHUNKS):
                await queue.send(i)
                await asyncio.sleep(0)

        task = asyncio.create_task(produce())
        await queue.receive()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()
