# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host authorization runs before worker I/O without blocking its event loop."""

import asyncio
from unittest.mock import Mock

import pytest

from openjiuwen.core.sys_operation.local.operation_observer import bind_operation_observer, before_operation
from openjiuwen.harness.security.outbound import sync_client


@pytest.mark.asyncio
async def test_worker_http_denial_prevents_request_and_keeps_host_loop_available():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def observer(name, arguments):
        calls.append((name, arguments))
        entered.set()
        await release.wait()
        return False

    session = Mock()
    with bind_operation_observer(observer):
        worker = asyncio.create_task(asyncio.to_thread(
            sync_client.post, "https://example.com/send", session=session, data="TEST_BODY"))
        await asyncio.wait_for(entered.wait(), 2)
        session.request.assert_not_called()
        release.set()
        with pytest.raises(PermissionError):
            await worker
    assert calls == [("network_send", {"url": "https://example.com/send", "data": "TEST_BODY"})]
    session.request.assert_not_called()


@pytest.mark.asyncio
async def test_parallel_tools_do_not_share_observers_or_reenter_for_reporting():
    events = []

    async def tool(identity):
        async def observer(name, arguments):
            await before_operation("network_send", {"url": "http://mock-desktop", "data": ""})
            await asyncio.sleep(0)
            events.append((identity, arguments))
            return True
        with bind_operation_observer(observer):
            await before_operation("file_read", identity)

    await asyncio.gather(tool("A"), tool("B"))
    assert sorted(events) == [("A", "A"), ("B", "B")]
    await before_operation("file_read", "unobserved")
    assert len(events) == 2
