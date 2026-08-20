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

from openjiuwen.agent_teams.tools.database import engine as engine_module
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
async def test_read_block_exceeding_watchdog_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read block hanging past the watchdog raises TimeoutError instead of stalling."""
    monkeypatch.setattr(engine_module, "_DB_SESSION_WATCHDOG_SECONDS", 0.05)
    sessions = DbSessions(_fake_session_factory)

    with pytest.raises(TimeoutError):
        async with sessions.read():
            await asyncio.sleep(1.0)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_write_block_exceeding_watchdog_raises_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung write block raises TimeoutError and leaves the write lock free."""
    monkeypatch.setattr(engine_module, "_DB_SESSION_WATCHDOG_SECONDS", 0.05)
    sessions = DbSessions(_fake_session_factory)

    with pytest.raises(TimeoutError):
        async with sessions.write():
            await asyncio.sleep(1.0)

    assert not sessions._write_lock.locked()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_write_lock_wait_is_not_counted_by_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Queueing behind the write lock longer than the watchdog is fine; only the block counts."""
    monkeypatch.setattr(engine_module, "_DB_SESSION_WATCHDOG_SECONDS", 0.05)
    sessions = DbSessions(_fake_session_factory)

    entered = asyncio.Event()

    async def hold_lock_briefly() -> None:
        # Each block stays inside the watchdog budget; three of them queued
        # back-to-back make the last writer's lock wait alone exceed it.
        async with sessions.write():
            entered.set()
            await asyncio.sleep(0.03)

    holders = [asyncio.create_task(hold_lock_briefly()) for _ in range(3)]
    await entered.wait()
    # Let the remaining holders enqueue on the lock first so this writer's
    # wait spans all of them (well past the watchdog interval).
    await asyncio.sleep(0.01)

    async with sessions.write():
        pass  # must not raise: its own block is fast

    for holder in holders:
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
