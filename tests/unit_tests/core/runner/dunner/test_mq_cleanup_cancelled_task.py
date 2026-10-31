# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The MQ adapter done-callback must tolerate cancelled tasks."""

import asyncio

import pytest

from openjiuwen.core.runner.drunner.server_adapter.mq_server_adapter import MqServerAdapter


@pytest.mark.asyncio
async def test_cleanup_task_does_not_raise_for_cancelled_task() -> None:
    async def _never() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_never())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    adapter = object.__new__(MqServerAdapter)
    adapter.adapter_id = "adapter-1"
    adapter._running_tasks = {}  # type: ignore[attr-defined]

    # Baseline raises CancelledError here (Task.exception on a cancelled task).
    adapter._cleanup_task("msg-1", task)
