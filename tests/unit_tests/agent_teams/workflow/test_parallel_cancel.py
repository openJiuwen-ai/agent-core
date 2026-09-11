# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""parallel() cancel/drain and _attempt_calls abort gate — zero-sleep, event-gated.

Covers the two cancel-path fixes in primitives.py without spawning run_workflow:

- explicit branch cancel on engine cancel (6ae870127): cancelling parallel()'s
  driver cancels every branch task so CancelledError lands on each branch.
- bounded drain + abort gate (3ef328293): the cancel path drains branches before
  unwinding (aclose() never pops a session row a live branch still uses), and
  _attempt_calls re-checks the abort gate before each backend attempt so a
  straggler that outlives the drain dies with a WorkflowAborted, not a retry.

All gates are asyncio.Event — no sleeps, no polling loops.
"""
from __future__ import annotations

import asyncio

import pytest

import openjiuwen.agent_teams.workflow.engine.primitives as primitives
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted
from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal, Runtime


# ---------------------------------------------------------------- parallel()


async def _settle_loose_tasks() -> None:
    """Cancel leftover branch tasks and retrieve their exceptions (no GC warnings)."""
    cur = asyncio.current_task()
    loose = [t for t in asyncio.all_tasks() if t is not cur]
    for t in loose:
        t.cancel()
    if loose:
        await asyncio.gather(*loose, return_exceptions=True)


@pytest.mark.asyncio
async def test_parallel_cancels_every_branch_on_driver_cancel():
    """A cancel of parallel()'s driver must cancel every branch task (6ae870127).

    Each branch parks on its own Event. Cancelling the driver surfaces
    CancelledError in the driver and cancels each branch — the branch's
    CancelledError flag fires (cancel landed on its await), proving the branch
    was not orphaned to burn tokens past the cancel.
    """
    started = [asyncio.Event() for _ in range(3)]
    cancelled = [asyncio.Event() for _ in range(3)]
    released = asyncio.Event()

    async def branch(i: int):
        started[i].set()
        try:
            await released.wait()  # park until released or cancelled
        except asyncio.CancelledError:
            cancelled[i].set()
            raise

    async def driver():
        return await primitives.parallel([lambda i=i: branch(i) for i in range(3)])

    drv = asyncio.create_task(driver())
    for ev in started:
        await ev.wait()  # all three branches parked
    drv.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drv

    assert all(ev.is_set() for ev in cancelled)  # every branch saw the cancel

    await _settle_loose_tasks()


@pytest.mark.asyncio
async def test_parallel_drains_branches_before_unwinding():
    """The cancel path drains branches before re-raising (3ef328293 drain half).

    A branch that does real cleanup on cancel must finish that cleanup before
    the driver's CancelledError surfaces. The branch records its death in a
    Future; the driver must not re-raise until that Future is set — proving the
    bounded wait ran. Event-gated (no sleep): the branch dies the moment cancel
    lands, the Future resolves, the wait returns.
    """
    started = asyncio.Event()
    dead = asyncio.Event()

    async def branch():
        started.set()
        try:
            await asyncio.Event().wait()  # park forever (until cancelled)
        except asyncio.CancelledError:
            dead.set()  # cleanup runs here
            raise

    async def driver():
        return await primitives.parallel([branch])

    drv = asyncio.create_task(driver())
    await started.wait()
    drv.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drv

    assert dead.is_set()  # branch died BEFORE the driver re-raised

    await _settle_loose_tasks()


# ----------------------------------------------------- _attempt_calls abort gate


class _Res:
    skipped = False
    text = "ok"
    structured = None
    tokens = 1


@pytest.mark.asyncio
async def test_attempt_calls_abort_gate_stops_before_backend_when_paused():
    """_attempt_calls re-checks the abort gate before each attempt (3ef328293 gate).

    With the abort signal set, the first attempt must raise WorkflowAborted
    *before* make_call ever runs — no backend call, no retry. Event-gated: the
    make_call would block forever (parking on an Event), so if the gate ever
    failed to fire, the test hangs — failing fast on the bug, not on a timeout.
    """
    ev = AbortSignal()
    ev.set(reason="pause")
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger(), abort_event=ev)

    called = asyncio.Event()

    async def make_call():
        called.set()
        return _Res()

    with pytest.raises(WorkflowAborted) as ei:
        await primitives._attempt_calls(rt, {"label": "t"}, None, None, make_call)
    assert ei.value.reason == "pause"
    assert not called.is_set()  # the gate stopped it before the backend


@pytest.mark.asyncio
async def test_attempt_calls_abort_gate_stops_a_straggler_between_retries():
    """A pause landing between attempts aborts the next one (straggler path).

    Attempt 1 runs (abort signal clear) and fails; the pause lands in that gap;
    attempt 2 must hit the gate and raise WorkflowAborted instead of retrying
    into the (now torn-down) backend. The gate is what bounds a straggler that
    outlives parallel()'s drain: it dies cleanly, not with 3x retry noise.
    """
    ev = AbortSignal()
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger(total=10_000), abort_event=ev)
    rt.retries = 3

    attempt = 0

    async def make_call():
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            ev.set(reason="pause")  # pause lands before attempt 2
            raise RuntimeError("backend boom")
        return _Res()  # would-be retry success — must never run

    with pytest.raises(WorkflowAborted) as ei:
        await primitives._attempt_calls(rt, {"label": "t"}, None, None, make_call)
    assert ei.value.reason == "pause"
    assert attempt == 1  # attempt 2 never ran
