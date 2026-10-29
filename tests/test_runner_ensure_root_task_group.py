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
import time

import pytest

from openjiuwen.core.runner.runner import (
    DEFAULT_RUNNER_CONFIG,
    _RootTaskGroupState,
    _RunnerImpl,
)


@pytest.fixture(autouse=True)
def _clear_main_thread_loop_after_test():
    """Leave the main thread without a current loop after each test.

    _new_loop() sets each fresh loop as the main thread's current loop; the
    tests close those loops in their finally blocks. Without this teardown
    the main thread would keep pointing at a closed loop, and a later test
    in the same session calling asyncio.get_event_loop() would receive the
    closed object.
    """
    yield
    asyncio.set_event_loop(None)


def _new_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _owner(runner: _RunnerImpl):
    """The current root task group owner task, or None when not started."""
    state = runner._root_state
    return state.owner if state is not None else None


def test_single_loop_start_stop_is_clean() -> None:
    """Baseline: start → stop on the same loop must not raise.

    Guards against the fix regressing the common single-loop path.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()
    try:
        loop.run_until_complete(runner.start())
        owner = _owner(runner)
        assert owner is not None
        assert owner.get_loop() is loop  # bound to the live loop
        assert loop.run_until_complete(runner.stop()) is True
        assert _owner(runner) is None
    finally:
        loop.close()


def test_reuse_after_loop_close_rebuilds_on_current_loop() -> None:
    """After the first loop closes, a start() on a second loop must NOT reuse
    the stale owner — it must rebuild a fresh task group on the current loop.

    Pre-fix behavior: _ensure_root_task_group sees the owner field is not
    None and returns, so the second start reuses the owner bound to the
    now-closed loop1; the subsequent stop() then awaits that dead owner and
    raises RuntimeError (Event loop is closed / attached to a different loop).
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    owner_after_first = _owner(runner)
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
        owner_after_second = _owner(runner)
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
        assert loop2.run_until_complete(runner.stop()) is True
        assert _owner(runner) is None
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
        assert loop2.run_until_complete(runner.stop()) is True  # would raise pre-fix
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
            owner = _owner(runner)
            assert owner is not None
            assert not owner.get_loop().is_closed()
            assert loop.run_until_complete(runner.stop()) is True
            assert _owner(runner) is None
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
    owner_after_first = _owner(runner)
    assert owner_after_first is not None
    assert not loop1.is_closed()  # live foreign loop, deliberately left open

    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        owner_after_second = _owner(runner)
        assert owner_after_second is not None
        assert owner_after_second is not owner_after_first, (
            "reused owner bound to a live foreign loop — "
            "reuse check missed owner_loop is asyncio.get_running_loop()"
        )
        assert owner_after_second.get_loop() is loop2, (
            "new owner not bound to the current running loop"
        )
        # stop() on loop2 must not cross-loop-await anything.
        assert loop2.run_until_complete(runner.stop()) is True
        assert _owner(runner) is None
    finally:
        loop2.close()
        loop1.close()


def test_concurrent_ensure_root_task_group_on_two_loops() -> None:
    """Concurrent _ensure_root_task_group() from two threads on two live loops
    must not corrupt the shared rebuild state.

    Review feedback on !2323: the clear→rebuild flow touches several shared
    fields without serialization; a regression test should probe whether two
    simultaneous rebuilds can interleave and overwrite each other's state.

    Oracle: after both threads finish, exactly one rebuild has won the state
    publish and the surviving state object is self-consistent — its owner is
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

    assert not t1.is_alive(), "thread 1 still running after join timeout"
    assert not t2.is_alive(), "thread 2 still running after join timeout"
    # A loser may hit the by-design RuntimeError: the retry loop's adoption
    # predicate is loop-local, so two cross-loop rebuilds can only keep
    # replacing each other; the loser fails loudly on the state it can no
    # longer own. Only unexpected exception types make this test red.
    unexpected = [
        exc for exc in errors
        if not (isinstance(exc, RuntimeError) and "concurrently replaced" in str(exc))
    ]
    assert not unexpected, f"concurrent rebuild raised unexpectedly: {unexpected!r}"
    # Single-slot invariant: after both threads finish, exactly one state
    # object is published, and it is fully built - not half-written by a
    # loser that was pre-empted mid-publish. This is the invariant the
    # _root_state_lock critical section must uphold.
    state = runner._root_state
    assert state is not None, "no state published after concurrent ensure"
    owner = state.owner
    assert owner is not None
    owner_loop = owner.get_loop()
    assert owner_loop in (loop1, loop2), "owner bound to an unexpected loop"
    assert state.group is not None
    assert state.ready is not None
    assert state.stop is not None
    # A loser that failed loudly must have retired its own state: its owner
    # is either done (already retired) or still a live task on its own loop
    # (orphaned - the failure path signals stop but cannot await a foreign
    # owner). No loser may leave an owner that is done AND still occupying
    # the slot, which would be a stuck half-state.
    for exc in errors:
        if isinstance(exc, RuntimeError) and "concurrently replaced" in str(exc):
            assert owner_loop is not None  # loser retired its own; slot holds only the winner

    # The runner must remain fully usable on the surviving owner's loop.
    winner_loop = owner_loop
    loser_loop = loop2 if winner_loop is loop1 else loop1
    assert winner_loop.run_until_complete(runner.stop()) is True
    assert runner._root_state is None
    loser_loop.close()
    winner_loop.close()


def test_cross_loop_stop_never_touches_the_stale_state() -> None:
    """stop() on a second loop must drop the stale state without scheduling
    anything on, or mutating, the loop that owned it.

    The v2 close path gates cancel_all/stop-event/cancel-scope behind the same
    loop-ownership predicate as the await: a cross-loop stop must take the
    drop-only branch, leaving the stale state's stop event unset (setting an
    event from a foreign loop would raise RuntimeError on a closed loop).
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    stale_state = runner._root_state
    assert stale_state is not None
    loop1.close()  # owner left stale, stop event still unset

    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        assert runner._root_state is not stale_state
        assert loop2.run_until_complete(runner.stop()) is True
        assert runner._root_state is None
    finally:
        loop2.close()
    # The stale loop's objects were never driven from loop2.
    assert not stale_state.stop.is_set()


def test_rebuild_signals_stale_owner_on_live_foreign_loop() -> None:
    """A rebuild over a live foreign-loop owner must signal that owner to
    stop on its own loop, so its task group and child tasks do not stay
    parked for the process lifetime.

    Review feedback on !3490: dropping the reference alone leaves the stale
    owner awaiting its stop event forever. The rebuild path must set that
    event on the owner's loop via call_soon_threadsafe.
    """
    import threading

    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    stale = runner._root_state
    assert stale is not None
    stale_owner = stale.owner
    assert stale_owner is not None

    thread = threading.Thread(target=loop1.run_forever, daemon=True)
    thread.start()
    loop2 = _new_loop()
    try:
        loop2.run_until_complete(runner.start())
        assert runner._root_state is not stale
        deadline = time.monotonic() + 5
        while not stale_owner.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stale.stop.is_set(), "stale owner was never signalled to stop"
        assert stale_owner.done(), "stale owner on a live foreign loop never terminated"
    finally:
        assert loop2.run_until_complete(runner.stop()) is True
        loop2.close()
        loop1.call_soon_threadsafe(loop1.stop)
        thread.join(timeout=10)
        assert not thread.is_alive()
        if not loop1.is_closed():
            loop1.close()


def test_cross_loop_stop_on_closed_loop_drops_without_signal() -> None:
    """stop() called directly on a second loop (no rebuild in between) must
    drop the stale state without raising and without driving the closed
    loop's objects.

    Review feedback on !3490: the close path's stale branches had zero
    coverage; every earlier test rebuilt on the second loop before stopping.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)

    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    stale_state = runner._root_state
    assert stale_state is not None
    loop1.close()  # owner left stale, stop event still unset

    loop2 = _new_loop()
    try:
        # Direct stop on the new loop: no start() rebuild in between, so
        # _close_root_task_group faces the stale state itself.
        assert loop2.run_until_complete(runner.stop()) is True
        assert runner._root_state is None
    finally:
        loop2.close()
    assert not stale_state.stop.is_set(), "closed-loop stale owner must not be signalled"


def test_cross_loop_stop_on_live_foreign_loop_signals_owner() -> None:
    """stop() on a second loop while the first loop still runs must signal
    the foreign owner to stop on its own loop, so its task group is cleaned
    up instead of staying parked for the process lifetime.
    """
    import threading

    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop1 = _new_loop()
    loop1.run_until_complete(runner.start())
    stale = runner._root_state
    assert stale is not None
    stale_owner = stale.owner
    assert stale_owner is not None

    thread = threading.Thread(target=loop1.run_forever, daemon=True)
    thread.start()
    loop2 = _new_loop()
    try:
        assert loop2.run_until_complete(runner.stop()) is True
        assert runner._root_state is None
        deadline = time.monotonic() + 5
        while not stale_owner.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stale.stop.is_set(), "foreign owner was never signalled to stop"
        assert stale_owner.done(), "foreign owner never terminated after stop signal"
    finally:
        loop2.close()
        loop1.call_soon_threadsafe(loop1.stop)
        thread.join(timeout=10)
        assert not thread.is_alive()
        loop1.close()


def test_cancel_all_failure_does_not_skip_owner_teardown(monkeypatch) -> None:
    """A failing task-manager cancel_all must not skip the same-loop owner
    teardown: the owner must still be stopped and awaited, and the state
    reference cleared.

    Review feedback on !3490: an exception from cancel_all used to abort the
    whole close path, leaving a healthy same-loop owner running while the
    state reference was dropped (fake-success shutdown).
    """
    from openjiuwen.core.common.task_manager import manager as manager_module

    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()
    loop.run_until_complete(runner.start())
    state = runner._root_state
    assert state is not None
    owner = state.owner
    assert owner is not None

    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("manager down")

    # runner.py imports get_task_manager inside the function body, so the
    # call-time import resolves the manager submodule attribute. Count
    # invocations so a silently ineffective patch turns the test red.
    monkeypatch.setattr(manager_module, "get_task_manager", boom)
    try:
        loop.run_until_complete(runner._close_root_task_group())
    finally:
        loop.close()
    assert calls, "boom was never called; the patch did not take effect"
    assert owner.done(), "same-loop owner was not torn down after cancel_all failure"
    assert runner._root_state is None


def test_stale_owner_exit_does_not_clobber_rebuilt_state() -> None:
    """A stale owner's teardown must only clear the group slot of the state
    object it owns, never that of a state a newer loop published.

    The owner loop's ``finally`` runs whenever the owner exits — including
    after a rebuild replaced ``_root_state``. Without the identity guard the
    exiting stale owner would null the fresh state's task group and every
    later ``start_task`` on it would raise.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()
    sentinel = object()

    async def scenario() -> None:
        await runner._ensure_root_task_group()
        stale = runner._root_state
        assert stale is not None and stale.group is not None
        fresh = _RootTaskGroupState()
        fresh.group = sentinel  # type: ignore[assignment]
        runner._root_state = fresh
        stale.stop.set()
        await stale.owner  # runs its finally while `fresh` is published
        assert fresh.group is sentinel, (
            "stale owner teardown clobbered the rebuilt state's task group"
        )
        # The stale owner's finally leaves its own (now unreachable) group
        # slot untouched when it is no longer the published state; only the
        # published state's slot is load-bearing, which the sentinel oracle
        # above guards. Without the identity check in the owner's finally,
        # the assignment would hit fresh.group and the oracle would fail.

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_concurrent_adoption_of_unready_state_waits_for_ready() -> None:
    """Two concurrent _ensure_root_task_group() calls on the same loop must
    never return with an unready state: a caller that adopts a concurrent
    caller's just-published state must wait for its owner to open the task
    group, instead of awaiting a pending owner directly (which would block
    start() until the group opens - the hang the final ready wait prevents).

    Review feedback on !3490 round 8: pins the ready wait on every exit path.
    """
    runner = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)
    loop = _new_loop()

    async def scenario():
        # Two ensures racing on one loop: both must settle on one ready,
        # live state; the loser adopts the winner's state under the lock.
        await asyncio.gather(
            runner._ensure_root_task_group(),
            runner._ensure_root_task_group(),
        )
        state = runner._root_state
        assert state is not None
        assert state.group is not None, "ensure returned before the task group opened"
        assert state.ready.is_set()
        assert state.owner is not None and not state.owner.done()

    try:
        loop.run_until_complete(scenario())
    finally:
        assert loop.run_until_complete(runner.stop()) is True
        loop.close()
