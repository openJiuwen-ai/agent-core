# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from openjiuwen.agent_teams.runtime.background_task_controller import (
    BackgroundTaskController, SwarmflowRunHandle)
from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal


class _FakeBackend:
    def __init__(self):
        self.aborted = False
    async def abort_sessions(self):
        self.aborted = True


class _FakeNative:
    def __init__(self):
        self.cancelled = None
    @property
    def async_tool_runtime(self):
        class _RT:
            async def cancel(self, task_id):
                self.outer.cancelled = task_id
        rt = _RT()
        rt.outer = self
        return rt


def _make_handle(run_id="wf_1", task_id="t_1"):
    return SwarmflowRunHandle(
        task_id=task_id, run_id=run_id, abort_event=AbortSignal(),
        backend=_FakeBackend(), native=_FakeNative(), relaunch=lambda: None)


@pytest.mark.asyncio
async def test_pause_one_run_by_id():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    ok = await ctl.pause("wf_1")
    assert ok
    assert "wf_1" not in ctl._active
    assert "wf_1" in ctl._paused


@pytest.mark.asyncio
async def test_pause_none_is_full_collection():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    ctl.register(_make_handle("wf_2"))
    ok = await ctl.pause(None)
    assert ok and len(ctl._paused) == 2


@pytest.mark.asyncio
async def test_stop_is_terminal_not_in_paused():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    ok = await ctl.stop("wf_1")
    assert ok
    assert "wf_1" not in ctl._active
    assert "wf_1" not in ctl._paused


@pytest.mark.asyncio
async def test_resume_relaunches():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")
    relaunched = []
    h = ctl._paused["wf_1"]
    h.relaunch = lambda: relaunched.append(1)
    ok = await ctl.resume("wf_1")
    assert ok and relaunched == [1] and "wf_1" not in ctl._paused


@pytest.mark.asyncio
async def test_stop_unknown_returns_false():
    ctl = BackgroundTaskController()
    assert await ctl.stop("nope") is False


@pytest.mark.asyncio
async def test_stop_terminates_an_already_paused_run():
    ctl = BackgroundTaskController()
    relaunched = []
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")
    ctl._paused["wf_1"].relaunch = lambda: relaunched.append(1)

    ok = await ctl.stop("wf_1")

    assert ok is True
    assert "wf_1" not in ctl._paused
    # dropping the relaunch closure means a later resume must not relaunch.
    resumed = await ctl.resume("wf_1")
    assert resumed is False and relaunched == []


@pytest.mark.asyncio
async def test_pause_sets_abort_reason_pause():
    ctl = BackgroundTaskController()
    h = _make_handle("wf_1")
    ctl.register(h)
    ok = await ctl.pause("wf_1")
    assert ok is True
    assert h.abort_event.reason == "pause"
    assert h.abort_event.is_set() is True


@pytest.mark.asyncio
async def test_stop_sets_abort_reason_stop():
    ctl = BackgroundTaskController()
    h = _make_handle("wf_1")
    ctl.register(h)
    ok = await ctl.stop("wf_1")
    assert ok is True
    assert h.abort_event.reason == "stop"
    assert h.abort_event.is_set() is True


# ---------------------------------------------------------------------------
# Preserved legacy coverage (prior task), adapted to run_id-addressed keys.
# ---------------------------------------------------------------------------


class _RecBackend:
    def __init__(self, seq):
        self.seq = seq
    async def abort_sessions(self):
        self.seq.append("abort_sessions")


class _RecNative:
    def __init__(self, seq):
        self.seq = seq
        self.async_tool_runtime = _RecRuntime(seq)


class _RecRuntime:
    def __init__(self, seq):
        self.seq = seq
        self.cancelled: list[str] = []
    async def cancel(self, task_id):
        self.seq.append(f"cancel:{task_id}")
        self.cancelled.append(task_id)
        return True


def _rec_handle(task_id, seq):
    ev = AbortSignal()
    native = _RecNative(seq)
    handle = SwarmflowRunHandle(
        task_id=task_id,
        run_id=task_id,
        abort_event=ev,
        backend=_RecBackend(seq),
        native=native,
        relaunch=lambda: None,
    )
    return handle, ev, native


def test_pause_runs_three_steps_in_order_and_parks_for_resume():
    """pause(): set abort_event → abort_sessions → cancel task; then parked."""
    seq = []
    ctl = BackgroundTaskController()
    handle, ev, native = _rec_handle("w1", seq)
    ctl.register(handle)

    ok = asyncio.run(ctl.pause())

    assert ok is True
    assert ev.is_set()  # step 1: engine abort signal raised
    # steps 2 and 3 ran in order — sessions aborted BEFORE the top-level cancel.
    assert seq == ["abort_sessions", "cancel:w1"]
    assert native.async_tool_runtime.cancelled == ["w1"]
    assert ctl.is_paused() is True
    assert ctl.is_paused("w1") is True


def test_resume_relaunches_and_clears_paused():
    """resume(): relaunch every parked run with its remembered closure."""
    seq = []
    relaunched = []
    ctl = BackgroundTaskController()
    handle, _ev, _native = _rec_handle("w1", seq)
    handle.relaunch = lambda: relaunched.append("w1")
    ctl.register(handle)

    async def scenario() -> bool:
        await ctl.pause()
        return await ctl.resume()

    resumed = asyncio.run(scenario())

    assert resumed is True
    assert relaunched == ["w1"]
    assert ctl.is_paused() is False


def test_pause_and_resume_are_noops_when_nothing_registered():
    """No active / no parked run → both return False without error."""
    ctl = BackgroundTaskController()
    assert asyncio.run(ctl.pause()) is False
    assert asyncio.run(ctl.resume()) is False


def test_deregister_by_run_id_drops_active_but_keeps_paused():
    """deregister drops the active handle but leaves a paused run in _paused.

    A paused run must survive run_background's finally-time deregister, or
    resume(run_id) would report not_found and the leader would start a fresh
    run instead of resuming the paused prefix.
    """
    ctl = BackgroundTaskController()
    ctl.register(_rec_handle("w1", [])[0])
    assert "w1" in ctl._active
    ctl.deregister("w1")
    assert "w1" not in ctl._active

    ctl.register(_rec_handle("w2", [])[0])
    asyncio.run(ctl.pause("w2"))
    assert "w2" in ctl._paused
    ctl.deregister("w2")
    assert "w2" in ctl._paused
