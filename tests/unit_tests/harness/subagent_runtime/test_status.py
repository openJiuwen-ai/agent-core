# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime StatusChannel."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.harness.subagent_runtime.models import SubagentStatus, SubagentStatusKind
from openjiuwen.harness.subagent_runtime.status import StatusChannel


@pytest.mark.asyncio
async def test_channel_receiver_end_to_end_lifecycle() -> None:
    """Worker writes via channel.set(); main-agent wait reads via receiver.wait_for_final()."""
    channel = StatusChannel()
    receiver = channel.subscribe()

    assert channel.current() == SubagentStatus.pending_init()
    assert channel.version() == 0

    wait_task = asyncio.create_task(receiver.wait_for_final())
    await asyncio.sleep(0)
    assert not wait_task.done()

    await channel.set(SubagentStatus.running())
    await asyncio.sleep(0)
    assert not wait_task.done()
    assert receiver.current() == SubagentStatus.running()

    await channel.set(SubagentStatus.completed("result"))
    status = await asyncio.wait_for(wait_task, timeout=1.0)

    assert status == SubagentStatus.completed("result")
    assert status.is_final()
    assert channel.version() == 2
    assert receiver.current() == channel.current()


@pytest.mark.asyncio
async def test_channel_receiver_end_to_end_with_interrupted_round() -> None:
    """Full round: running → interrupted → running → completed; wait stays blocked until final."""
    channel = StatusChannel(SubagentStatus.pending_init())
    receiver = channel.subscribe()

    wait_task = asyncio.create_task(receiver.wait_for_final())
    await asyncio.sleep(0)

    await channel.set(SubagentStatus.running())
    await asyncio.sleep(0)
    assert not wait_task.done()

    await channel.set(SubagentStatus.interrupted())
    await asyncio.sleep(0)
    assert not wait_task.done()

    await channel.set(SubagentStatus.running())
    await asyncio.sleep(0)
    assert not wait_task.done()

    await channel.set(SubagentStatus.completed("after interrupt"))
    status = await asyncio.wait_for(wait_task, timeout=1.0)

    assert status == SubagentStatus.completed("after interrupt")
    assert channel.version() == 4


@pytest.mark.asyncio
async def test_channel_defaults_to_pending_init() -> None:
    channel = StatusChannel()
    assert channel.current() == SubagentStatus.pending_init()
    assert channel.version() == 0


@pytest.mark.asyncio
async def test_status_channel_reports_repeated_values_as_changes() -> None:
    channel = StatusChannel(SubagentStatus.running())
    receiver = channel.subscribe()

    task = asyncio.create_task(receiver.changed())
    await asyncio.sleep(0)
    await channel.set(SubagentStatus.interrupted())
    await channel.set(SubagentStatus.running())

    changed = await asyncio.wait_for(task, timeout=1.0)
    assert changed is True


@pytest.mark.asyncio
async def test_status_channel_close_releases_waiter() -> None:
    channel = StatusChannel(SubagentStatus.running())
    receiver = channel.subscribe()
    task = asyncio.create_task(receiver.changed())
    await asyncio.sleep(0)

    await channel.close()

    assert await asyncio.wait_for(task, timeout=1.0) is False


@pytest.mark.asyncio
async def test_close_does_not_increment_version() -> None:
    channel = StatusChannel(SubagentStatus.running())
    assert channel.version() == 0

    await channel.close()

    assert channel.version() == 0
    assert channel.current() == SubagentStatus.running()


@pytest.mark.asyncio
async def test_receiver_current_matches_channel_after_set() -> None:
    channel = StatusChannel()
    receiver = channel.subscribe()

    await channel.set(SubagentStatus.running())

    assert receiver.current() == channel.current() == SubagentStatus.running()


@pytest.mark.asyncio
async def test_subscribe_after_set_waits_for_next_transition() -> None:
    channel = StatusChannel()
    await channel.set(SubagentStatus.running())
    assert channel.version() == 1

    receiver = channel.subscribe()
    task = asyncio.create_task(receiver.changed())
    await asyncio.sleep(0)
    assert not task.done()

    await channel.set(SubagentStatus.completed("done"))
    assert await asyncio.wait_for(task, timeout=1.0) is True
    assert receiver.current() == SubagentStatus.completed("done")


@pytest.mark.asyncio
async def test_wait_for_final_returns_immediately_when_already_final() -> None:
    channel = StatusChannel(SubagentStatus.completed("done"))
    receiver = channel.subscribe()

    status = await receiver.wait_for_final()

    assert status == SubagentStatus.completed("done")


@pytest.mark.asyncio
async def test_wait_for_final_waits_through_interrupted() -> None:
    channel = StatusChannel(SubagentStatus.running())
    receiver = channel.subscribe()
    task = asyncio.create_task(receiver.wait_for_final())
    await asyncio.sleep(0)

    await channel.set(SubagentStatus.interrupted())
    await asyncio.sleep(0)
    assert not task.done()

    await channel.set(SubagentStatus.completed("done"))
    status = await asyncio.wait_for(task, timeout=1.0)
    assert status == SubagentStatus.completed("done")


@pytest.mark.asyncio
async def test_wait_for_final_reaches_completed_from_running() -> None:
    channel = StatusChannel(SubagentStatus.running())
    receiver = channel.subscribe()

    async def complete_later() -> None:
        await asyncio.sleep(0.01)
        await channel.set(SubagentStatus.completed("result"))

    producer = asyncio.create_task(complete_later())
    status = await receiver.wait_for_final()
    await producer

    assert status == SubagentStatus.completed("result")


@pytest.mark.asyncio
async def test_not_found_on_channel_is_treated_as_final() -> None:
    """NOT_FOUND must not be written by control; if present, wait must not hang."""
    channel = StatusChannel(SubagentStatus.not_found())
    receiver = channel.subscribe()

    status = await receiver.wait_for_final()

    assert status.kind is SubagentStatusKind.NOT_FOUND
    assert status.is_final()


@pytest.mark.asyncio
async def test_receivers_track_versions_independently() -> None:
    channel = StatusChannel(SubagentStatus.running())
    first = channel.subscribe()
    second = channel.subscribe()

    await channel.set(SubagentStatus.interrupted())
    assert await first.changed() is True
    assert await second.changed() is True

    await channel.set(SubagentStatus.running())
    assert await first.changed() is True

    late = channel.subscribe()
    assert late.current() == SubagentStatus.running()
    pending = asyncio.create_task(late.changed())
    await asyncio.sleep(0)
    assert not pending.done()

    await channel.set(SubagentStatus.completed("done"))
    assert await asyncio.wait_for(pending, timeout=1.0) is True


@pytest.mark.asyncio
async def test_wait_for_final_may_return_non_final_after_close() -> None:
    channel = StatusChannel(SubagentStatus.running())
    receiver = channel.subscribe()
    task = asyncio.create_task(receiver.wait_for_final())
    await asyncio.sleep(0)

    await channel.close()

    status = await asyncio.wait_for(task, timeout=1.0)
    assert status.kind.value == "running"
    assert not status.is_final()


@pytest.mark.asyncio
async def test_set_increments_version() -> None:
    channel = StatusChannel()
    assert channel.version() == 0

    await channel.set(SubagentStatus.running())
    assert channel.version() == 1
    assert channel.current() == SubagentStatus.running()

    await channel.set(SubagentStatus.completed("ok"))
    assert channel.version() == 2
    assert channel.current().message == "ok"
