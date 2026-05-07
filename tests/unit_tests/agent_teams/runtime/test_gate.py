# coding: utf-8
"""InteractGate timing tests covering admit / consume_done / close_and_drain."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.runtime.gate import InteractGate


@pytest.mark.asyncio
async def test_admit_returns_ticket_when_open():
    gate = InteractGate()
    ticket = await gate.admit()
    assert ticket is not None
    assert gate.inflight == 1
    assert gate.closed is False


@pytest.mark.asyncio
async def test_admit_returns_none_when_closed():
    gate = InteractGate()
    await gate.close_and_drain()
    assert gate.closed is True
    ticket = await gate.admit()
    assert ticket is None


@pytest.mark.asyncio
async def test_consume_done_decrements_inflight():
    gate = InteractGate()
    ticket = await gate.admit()
    assert gate.inflight == 1
    await gate.consume_done(ticket)
    assert gate.inflight == 0


@pytest.mark.asyncio
async def test_consume_done_idempotent_after_drain():
    gate = InteractGate()
    ticket = await gate.admit()
    await gate.consume_done(ticket)
    # Second call should be a no-op rather than going negative.
    await gate.consume_done(ticket)
    assert gate.inflight == 0


@pytest.mark.asyncio
async def test_consume_done_ignores_foreign_ticket():
    a = InteractGate()
    b = InteractGate()
    ticket_b = await b.admit()
    await a.admit()
    # Passing b's ticket to a must not affect a's counter.
    await a.consume_done(ticket_b)
    assert a.inflight == 1


@pytest.mark.asyncio
async def test_close_and_drain_returns_immediately_when_idle():
    gate = InteractGate()
    await asyncio.wait_for(gate.close_and_drain(), timeout=0.5)
    assert gate.closed is True


@pytest.mark.asyncio
async def test_close_and_drain_blocks_until_inflight_consumed():
    gate = InteractGate()
    ticket = await gate.admit()

    async def _drain_consume() -> None:
        await asyncio.sleep(0.05)
        await gate.consume_done(ticket)

    consume_task = asyncio.create_task(_drain_consume())
    # close_and_drain must return only after consume_done runs.
    await asyncio.wait_for(gate.close_and_drain(), timeout=0.5)
    await consume_task
    assert gate.inflight == 0
    assert gate.closed is True


@pytest.mark.asyncio
async def test_admit_after_drain_still_rejected():
    gate = InteractGate()
    ticket = await gate.admit()
    drain_task = asyncio.create_task(gate.close_and_drain())
    await asyncio.sleep(0.01)
    # Mid-drain admits must already be rejected because closed flag is set.
    rejected = await gate.admit()
    await gate.consume_done(ticket)
    await drain_task
    assert rejected is None


@pytest.mark.asyncio
async def test_multiple_inflight_drained_together():
    gate = InteractGate()
    tickets = [await gate.admit() for _ in range(3)]
    assert gate.inflight == 3

    async def _consume_after_delay(ticket, delay):
        await asyncio.sleep(delay)
        await gate.consume_done(ticket)

    consume_tasks = [
        asyncio.create_task(_consume_after_delay(t, 0.02 * (i + 1)))
        for i, t in enumerate(tickets)
    ]
    await asyncio.wait_for(gate.close_and_drain(), timeout=0.5)
    for task in consume_tasks:
        await task
    assert gate.inflight == 0
