# -*- coding: utf-8 -*-
"""Tests for _ensure_root_task_group loop-liveness check.

Covers the cross-loop task-group reuse defect: GLOBAL_RUNNER is a process-wide
singleton whose root task group owner is bound to the asyncio loop that created
it. When the same Runner is driven from a second loop after the first is closed
(e.g. multiple FastAPI apps / TestClients with independent lifespans),
_ensure_root_task_group must NOT reuse the stale owner — otherwise stop()
awaits an owner attached to a dead loop and raises RuntimeError.

These tests instantiate a private _RunnerImpl (not the module-level
GLOBAL_RUNNER) so they do not perturb other tests.

Run:
    pytest tests/test_runner_ensure_root_task_group.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner.runner import (
    DEFAULT_RUNNER_CONFIG,
    _RunnerImpl,
)


def _new_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def test_single_loop_start_stop_is_clean() -> None:
    """Baseline: start → stop on the same loop must not raise.

    Guards against the fix regressing the common single-loop path.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()
    try:
        loop.run_until_complete(runner.start())
        owner = runner._root_task_group_owner
        assert owner is not None
        assert owner.get_loop() is loop  # bound to the live loop
        loop.run_until_complete(runner.stop())
        assert runner._root_task_group_owner is None
    finally:
        loop.close()


def test_reuse_after_loop_close_rebuilds_on_current_loop() -> None:
    """After the first loop closes, a start() on a second loop must NOT reuse
    the stale owner — it must rebuild a fresh task group on the current loop.

    Pre-fix behavior: _ensure_root_task_group sees _root_task_group_owner is
    not None and returns, so the second start reuses the owner bound to the
    now-closed loop1; the subsequent stop() then awaits that dead owner and
    raises RuntimeError (Event loop is closed / attached to a different loop).
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    owner_after_first = runner._root_task_group_owner
    assert owner_after_first is not None
    # Simulate app A going down WITHOUT a clean stop: close loop1, leaving the
    # owner task bound to the dead loop. (A clean stop would have nulled it.)
    loop1.close()
    assert loop1.is_closed()
    assert owner_after_first.get_loop() is loop1
    assert owner_after_first.get_loop().is_closed()

    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        owner_after_second = runner._root_task_group_owner
        # The fix: must NOT reuse the stale owner; must rebuild on loop2.
        assert owner_after_second is not None
        assert owner_after_second is not owner_after_first, (
            "reused stale owner task bound to a closed loop — "
            "_ensure_root_task_group did not detect loop liveness"
        )
        assert owner_after_second.get_loop() is loop2, (
            "new owner not bound to the current (live) loop"
        )
        # stop() on the second loop must succeed (no cross-loop await).
        loop2.run_until_complete(runner.stop())
        assert runner._root_task_group_owner is None
    finally:
        loop2.close()


def test_stop_after_loop_close_does_not_raise() -> None:
    """stop() on a fresh loop after the original loop closed must not raise
    RuntimeError("...different loop" / "Event loop is closed").

    This is the user-visible symptom: the lifespan teardown (Runner.stop) of
    a second app/TestClient surfaces the cross-loop failure as an uncaught
    RuntimeError. Pre-fix, this raised; post-fix, it must be clean.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    loop1.close()  # app A down, owner left stale

    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        # The user-facing assertion: stop on the second loop must not raise.
        loop2.run_until_complete(runner.stop())  # would raise pre-fix
    finally:
        loop2.close()


def test_repeated_start_stop_on_same_loop_is_idempotent() -> None:
    """Multiple start/stop cycles on the SAME loop must remain clean — the
    loop-liveness check must not incorrectly discard a still-alive owner.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()
    try:
        for _ in range(3):
            loop.run_until_complete(runner.start())
            owner = runner._root_task_group_owner
            assert owner is not None
            assert not owner.get_loop().is_closed()
            loop.run_until_complete(runner.stop())
            assert runner._root_task_group_owner is None
    finally:
        loop.close()


def test_reuse_on_live_foreign_loop_rebuilds_on_current_loop() -> None:
    """A start() on loop2 while loop1 is STILL RUNNING (not closed) must NOT
    reuse the loop1-bound owner.

    Review feedback on !2323: liveness alone is insufficient. If the old loop
    is still alive but is not the current running loop, reusing the owner lets
    a subsequent stop() await a foreign-loop task and raise
    RuntimeError("... attached to a different loop"). The reuse check must
    also require owner_loop is the current running loop.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    owner_after_first = runner._root_task_group_owner
    assert owner_after_first is not None
    assert not loop1.is_closed()  # live foreign loop, deliberately left open

    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        owner_after_second = runner._root_task_group_owner
        assert owner_after_second is not None
        assert owner_after_second is not owner_after_first, (
            "reused owner bound to a live foreign loop — "
            "reuse check missed owner_loop is asyncio.get_running_loop()"
        )
        assert owner_after_second.get_loop() is loop2, (
            "new owner not bound to the current running loop"
        )
        # stop() on loop2 must not cross-loop-await anything.
        loop2.run_until_complete(runner.stop())
        assert runner._root_task_group_owner is None
    finally:
        loop2.close()
        loop1.close()


def test_concurrent_ensure_root_task_group_on_two_loops() -> None:
    """Concurrent _ensure_root_task_group() from two threads on two live loops
    must not corrupt the shared rebuild state.

    Review feedback on !2323: the clear→rebuild flow touches several shared
    fields without serialization; a regression test should probe whether two
    simultaneous rebuilds can interleave and overwrite each other's state.

    Oracle: after both threads finish, exactly one rebuild has won the field
    write and every field triplet is consistent — the surviving owner is
    bound to one of the two loops, and the runner can still start()/stop()
    cleanly afterwards.
    """
    import threading

    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop1 = _new_loop()
    loop2 = _new_loop()
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def drive(loop: asyncio.AbstractEventLoop) -> None:
        try:
            barrier.wait(timeout=10)
            loop.run_until_complete(runner._ensure_root_task_group())
        except BaseException as exc:  # noqa: BLE001 - collected in main thread
            errors.append(exc)

    t1 = threading.Thread(target=drive, args=(loop1,))
    t2 = threading.Thread(target=drive, args=(loop2,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"concurrent rebuild raised: {errors!r}"
    owner = runner._root_task_group_owner
    assert owner is not None
    owner_loop = owner.get_loop()
    assert owner_loop in (loop1, loop2), "owner bound to an unexpected loop"
    assert not owner.done()
    # The field triplet must be self-consistent, not half-written by a loser.
    assert runner._root_task_group is not None
    assert runner._root_task_group_ready is not None
    assert runner._root_task_group_stop is not None

    # The runner must remain fully usable on the surviving owner's loop.
    winner_loop = owner_loop
    loser_loop = loop2 if winner_loop is loop1 else loop1
    winner_loop.run_until_complete(runner.stop())
    assert runner._root_task_group_owner is None
    loser_loop.close()
    winner_loop.close()
