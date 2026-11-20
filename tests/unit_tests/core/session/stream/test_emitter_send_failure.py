# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A full stream queue must not silently drop a chunk."""

import pytest

from openjiuwen.core.session.stream.emitter import AsyncStreamQueue


@pytest.mark.asyncio
async def test_send_raises_when_queue_stays_full() -> None:
    queue = AsyncStreamQueue(maxsize=1)
    await queue.send("occupant")

    with pytest.raises(TimeoutError, match="failed to send stream data"):
        await queue.send("lost", attempt_timeout=0.01, max_retries=1)
