# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""BackgroundTask.wait must not forward waiter cancellation into the work."""

import asyncio

import pytest

from openjiuwen.core.common.background_tasks import BackgroundTask


@pytest.mark.asyncio
async def test_wait_survives_waiter_cancellation():
    release = asyncio.Event()

    async def work():
        await release.wait()
        return "ok"

    task = asyncio.create_task(work())
    handle = BackgroundTask.from_asyncio_task(task, group="evolution")
    waiter = asyncio.create_task(handle.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0)
    assert task.cancelled() is False
    release.set()
    assert await task == "ok"


@pytest.mark.asyncio
async def test_explicit_cancel_still_stops_work():
    async def work():
        await asyncio.Event().wait()
        return "ok"

    task = asyncio.create_task(work())
    handle = BackgroundTask.from_asyncio_task(task, group="evolution")
    await handle.cancel()
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_wait_timeout_does_not_cancel_until_explicit_cancel():
    async def work():
        await asyncio.Event().wait()
        return "ok"

    task = asyncio.create_task(work())
    handle = BackgroundTask.from_asyncio_task(task, group="evolution")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(handle.wait(), timeout=0.05)
    assert task.cancelled() is False
    await handle.cancel()
    assert task.cancelled() is True
