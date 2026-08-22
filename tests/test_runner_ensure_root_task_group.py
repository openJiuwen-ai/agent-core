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
