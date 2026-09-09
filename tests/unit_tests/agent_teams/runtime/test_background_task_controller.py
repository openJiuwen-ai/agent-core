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
        backend=_FakeBackend(), native=_FakeNative(), inputs={}, session_id="s")


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


class _FakeLauncher:
    """Stand-in for the current cycle's SwarmflowTool."""

    def __init__(self):
        self.relaunched: list[tuple[dict, str]] = []

    def relaunch(self, inputs, session_id):
        self.relaunched.append((inputs, session_id))


@pytest.mark.asyncio
async def test_resume_relaunches_via_registered_launcher():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")
    launcher = _FakeLauncher()
    ctl.set_launcher(launcher)
    ok = await ctl.resume("wf_1")
    assert ok and launcher.relaunched == [({}, "s")] and "wf_1" not in ctl._paused


@pytest.mark.asyncio
async def test_resume_uses_the_newest_launcher_not_the_launching_one():
    """A pause/resume cycle rebuilds the leader harness and its SwarmflowTool.

    The ticket carries only data; the controller must relaunch on whichever
    tool registered last (the live cycle), never on the one that launched.
    """
    ctl = BackgroundTaskController()
    old, new = _FakeLauncher(), _FakeLauncher()
    ctl.set_launcher(old)
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")
    ctl.set_launcher(new)  # harness rebuilt → new tool registers itself
    await ctl.resume("wf_1")
    assert old.relaunched == [] and new.relaunched == [({}, "s")]


@pytest.mark.asyncio
async def test_resume_without_launcher_keeps_ticket():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")
    assert await ctl.resume("wf_1") is False
    assert "wf_1" in ctl._paused  # parked until a launcher exists


@pytest.mark.asyncio
async def test_stop_unknown_returns_false():
    ctl = BackgroundTaskController()
    assert await ctl.stop("nope") is False


@pytest.mark.asyncio
async def test_stop_terminates_an_already_paused_run():
    ctl = BackgroundTaskController()
    launcher = _FakeLauncher()
    ctl.set_launcher(launcher)
    ctl.register(_make_handle("wf_1"))
    await ctl.pause("wf_1")

    ok = await ctl.stop("wf_1")

    assert ok is True
    assert "wf_1" not in ctl._paused
    # dropping the ticket means a later resume must not relaunch.
    resumed = await ctl.resume("wf_1")
    assert resumed is False and launcher.relaunched == []


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


@pytest.mark.asyncio
async def test_pause_waits_for_task_unwind():
    """pause() must not return until the cancelled task has actually unwound.

    The engine writes the pause record and emits WORKFLOW_PAUSED while unwinding
    (in the task's finally), which happens asynchronously after task.cancel().
    An embedder that tears the leader harness down right after pause() would
    otherwise lose that event.
    """
    unwound = []

    async def _slow_unwind():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Simulate the engine's multi-step teardown before the record lands.
            for _ in range(5):
                await asyncio.sleep(0)
            unwound.append("record-written")
            raise

    task = asyncio.create_task(_slow_unwind())
    await asyncio.sleep(0)

    class _TaskRT:
        def __init__(self, t):
            self._tasks = {"t_1": t}
        async def cancel(self, task_id):
            self._tasks[task_id].cancel()
            return True

    class _TaskNative:
        def __init__(self, t):
            self.async_tool_runtime = _TaskRT(t)

    h = SwarmflowRunHandle(
        task_id="t_1", run_id="wf_1", abort_event=AbortSignal(),
        backend=_FakeBackend(), native=_TaskNative(task), inputs={}, session_id="s")
    ctl = BackgroundTaskController()
    ctl.register(h)

    await ctl.pause("wf_1")

    assert task.done()
    assert unwound == ["record-written"]


@pytest.mark.asyncio
async def test_stop_none_stops_all_active_and_paused():
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1"))
    ctl.register(_make_handle("wf_2"))
    await ctl.pause("wf_2")  # park wf_2 in _paused

    ok = await ctl.stop(None)

    assert ok is True
    assert "wf_1" not in ctl._active and "wf_1" not in ctl._paused  # active → aborted + dropped
    assert "wf_2" not in ctl._paused  # paused → relaunch closure dropped
    assert not ctl._active and not ctl._paused


@pytest.mark.asyncio
async def test_stop_none_drops_paused_without_reaborting():
    ctl = BackgroundTaskController()
    h = _make_handle("wf_1")
    ctl.register(h)
    await ctl.pause("wf_1")
    assert h.abort_event.reason == "pause"

    ok = await ctl.stop(None)

    assert ok is True
    assert "wf_1" not in ctl._paused
    # A paused run already wrote its pause record at pause time; stop must only
    # drop the relaunch closure, not re-abort it to reason="stop".
    assert h.abort_event.reason == "pause"


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
        inputs={}, session_id="s",
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
    """resume(): relaunch every parked run through the registered launcher."""
    seq = []
    launcher = _FakeLauncher()
    ctl = BackgroundTaskController()
    ctl.set_launcher(launcher)
    handle, _ev, _native = _rec_handle("w1", seq)
    ctl.register(handle)

    async def scenario() -> bool:
        await ctl.pause()
        return await ctl.resume()

    resumed = asyncio.run(scenario())

    assert resumed is True
    assert launcher.relaunched == [({}, "s")]
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
