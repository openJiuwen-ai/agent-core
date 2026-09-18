# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancellation races between submission and execution."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.modules.task_scheduler import TaskScheduler
from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


async def _make_scheduler():
    config = ControllerConfig()
    manager = TaskManager(config)
    await manager.add_task(
        Task(
            task_id="pending",
            session_id="session",
            task_type="test",
            status=TaskStatus.SUBMITTED,
        )
    )
    scheduler = TaskScheduler(config, manager, None, None, None, AgentCard(name="test"))
    scheduler.sessions["session"] = Session(session_id="session")
    return scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_cancel_submitted_task_waits_for_scheduled_wrapper(started):
    scheduler = await _make_scheduler()
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def wrapper():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    execution = asyncio.create_task(wrapper())
    scheduler._running_tasks["pending"] = (None, execution)
    try:
        if started:
            await asyncio.wait_for(entered.wait(), 1)
        assert await scheduler.cancel_task("pending")
        assert execution.cancelled()
        assert exited.is_set() == started
        assert scheduler._running_tasks == {}
        assert scheduler.task_manager.tasks["pending"].status == TaskStatus.CANCELED
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_task_in_stale_scan_is_not_scheduled():
    scheduler = await _make_scheduler()
    scanned = asyncio.Event()
    release_scan = asyncio.Event()
    checked = asyncio.Event()
    get_task = scheduler.task_manager.get_task

    async def delayed_scan(task_filter=None):
        tasks = await get_task(task_filter=task_filter)
        if task_filter.status == TaskStatus.SUBMITTED:
            scanned.set()
            await release_scan.wait()
        elif release_scan.is_set():
            checked.set()
        return tasks

    scheduler.task_manager.get_task = delayed_scan
    scheduler._execute_task_wrapper = AsyncMock()
    await scheduler.start()
    try:
        await asyncio.wait_for(scanned.wait(), 1)
        assert await scheduler.cancel_task("pending")
        release_scan.set()
        await asyncio.wait_for(checked.wait(), 1)
        scheduler._execute_task_wrapper.assert_not_called()
        assert scheduler._running_tasks == {}
    finally:
        release_scan.set()
        await scheduler.stop()
