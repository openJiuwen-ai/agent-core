# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DbSessions watchdog: a wedged driver surfaces as TimeoutError, not a hang.

The pool guards connection checkout with ``pool_timeout``, but a statement
submitted to a wedged driver thread awaits its result with no timeout at all.
``DbSessions.read()`` / ``write()`` now run their session block under a
watchdog so such a hang raises loudly. The write lock wait is deliberately
NOT counted — writers legitimately queue behind each other.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.tools.database.engine import DbSessions


class _FakeSession:
    """Minimal async-context session standing in for AsyncSession."""

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _fake_session_factory() -> _FakeSession:
    return _FakeSession()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_read_block_exceeding_watchdog_raises() -> None:
    """A read block hanging past the watchdog raises TimeoutError instead of stalling."""
    sessions = DbSessions(_fake_session_factory)
    sessions._watchdog_seconds = 0

    with pytest.raises(TimeoutError):
        async with sessions.read():
            # With a zero watchdog the deadline is already past, so the first
            # suspend point inside the block times out at once. An Event that
            # is never set hangs without any real sleep.
            await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_write_block_exceeding_watchdog_raises_and_releases_lock() -> None:
    """A hung write block raises TimeoutError and leaves the write lock free."""
    sessions = DbSessions(_fake_session_factory)
    sessions._watchdog_seconds = 0

    with pytest.raises(TimeoutError):
        async with sessions.write():
            await asyncio.Event().wait()

    assert not sessions._write_lock.locked()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_write_lock_wait_is_not_counted_by_watchdog() -> None:
    """Queueing behind the write lock is not counted by the watchdog; only the block is.

    With a zero watchdog, *any* wait longer than zero exceeds the budget —
    so a writer that merely queues for the lock would already time out if
    the lock were inside the ``asyncio.timeout`` context. It must survive.
    """
    sessions = DbSessions(_fake_session_factory)
    sessions._watchdog_seconds = 0

    entered = asyncio.Event()

    async def hold_lock() -> None:
        # The holder deliberately hangs past its own (zero) budget: it is
        # killed by the watchdog and releases the lock on the way out.
        with pytest.raises(TimeoutError):
            async with sessions.write():
                entered.set()
                await asyncio.Event().wait()

    holder = asyncio.create_task(hold_lock())
    await entered.wait()

    # Queue behind the holder; the wait happens outside the timeout context,
    # and the block itself has no suspend point, so it must not raise.
    async with sessions.write():
        pass

    await holder


@pytest.mark.asyncio
@pytest.mark.level0
async def test_fast_read_and_write_paths_unaffected() -> None:
    """Normal fast blocks complete untouched by the watchdog."""
    sessions = DbSessions(_fake_session_factory)
    async with sessions.read() as read_session:
        assert isinstance(read_session, _FakeSession)
    async with sessions.write() as write_session:
        assert isinstance(write_session, _FakeSession)
