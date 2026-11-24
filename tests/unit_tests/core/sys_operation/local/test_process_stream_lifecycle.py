# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler, StreamEventType


@pytest.mark.asyncio
async def test_stream_deadline_includes_wait_after_both_output_pipes_close():
    stopped = asyncio.Event()
    process = Mock(spec=asyncio.subprocess.Process)
    process.pid = 123
    process.returncode = None
    process.stdout = Mock(read=AsyncMock(return_value=b""))
    process.stderr = Mock(read=AsyncMock(return_value=b""))

    async def wait():
        await stopped.wait()
        process.returncode = -1
        return -1

    process.wait = wait
    handler = AsyncProcessHandler(process, timeout=0.05)
    handler._kill_process_tree = Mock(side_effect=stopped.set)
    chunks = [chunk async for chunk in handler.stream()]
    assert any(chunk.type == StreamEventType.ERROR for chunk in chunks)
    handler._kill_process_tree.assert_called_once()
    assert process.returncode == -1


@pytest.mark.asyncio
async def test_closing_stream_after_exit_does_not_stop_background_descendants():
    process = Mock(spec=asyncio.subprocess.Process)
    process.returncode = 0
    process.stdout = Mock(read=AsyncMock(return_value=b""))
    process.stderr = Mock(read=AsyncMock(return_value=b""))
    handler = AsyncProcessHandler(process)
    handler._kill_process_tree = Mock()
    events = handler.stream()
    assert (await anext(events)).type == StreamEventType.EXIT
    await events.aclose()
    handler._kill_process_tree.assert_not_called()
