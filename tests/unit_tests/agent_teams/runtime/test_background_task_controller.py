# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from openjiuwen.agent_teams.runtime.background_task_controller import (
    BackgroundTaskController, SwarmflowRunHandle)
from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal
from openjiuwen.harness_protocol.state import HarnessState


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


def _make_handle(run_id="wf_1", task_id="t_1", launcher=None, owner_member=None):
    return SwarmflowRunHandle(
        task_id=task_id, run_id=run_id, abort_event=AbortSignal(),
        backend=_FakeBackend(), native=_FakeNative(), inputs={}, session_id="s",
        launcher=launcher, owner_member=owner_member)


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


# ---------------------------------------------------------------------------
# Per-run launcher routing (C3): each run's ticket carries its launching tool;
# resume routes home instead of always using the single global slot.
# ---------------------------------------------------------------------------


class _StatefulHarness:
    """Fake harness whose state can be flipped to TERMINATED."""

    def __init__(self):
        self.state = HarnessState.IDLE


class _ToolWithHarness:
    def __init__(self, harness):
        self._parent_agent = harness
        self.relaunched = []

    def relaunch(self, inputs, session_id):
        self.relaunched.append((inputs, session_id))


@pytest.mark.asyncio
async def test_resume_routes_each_run_to_its_own_launcher():
    """Two tools each own a run; resume must relaunch on the launching tool."""
    ctl = BackgroundTaskController()
    tool_a, tool_b = _ToolWithHarness(_StatefulHarness()), _ToolWithHarness(_StatefulHarness())
    ctl.register(_make_handle("wf_a", launcher=tool_a))
    ctl.register(_make_handle("wf_b", launcher=tool_b))
    await ctl.pause(None)

    ok = await ctl.resume(None)

    assert ok is True
    assert tool_a.relaunched == [({}, "s")]
    assert tool_b.relaunched == [({}, "s")]
    assert not ctl._paused


@pytest.mark.asyncio
async def test_resume_falls_back_to_global_slot_for_dead_launcher():
    """A dead launching tool (harness TERMINATED) falls back to the global slot.

    Leader cross-cycle rebuild: the pause tore the harness down and a new
    cycle's tool registered itself — the ticket must relaunch there, not on
    the stale launching tool.
    """
    ctl = BackgroundTaskController()
    dead_tool = _ToolWithHarness(_StatefulHarness())
    dead_tool._parent_agent.state = HarnessState.TERMINATED
    current = _ToolWithHarness(_StatefulHarness())
    ctl.register(_make_handle("wf_1", launcher=dead_tool))
    await ctl.pause("wf_1")
    ctl.set_launcher(current)

    ok = await ctl.resume("wf_1")

    assert ok is True
    assert dead_tool.relaunched == []
    assert current.relaunched == [({}, "s")]


@pytest.mark.asyncio
async def test_resume_legacy_handle_without_launcher_uses_global_slot():
    """Handles predating the launcher field (test stubs) keep the old routing."""
    ctl = BackgroundTaskController()
    ctl.register(_make_handle("wf_1", launcher=None))
    await ctl.pause("wf_1")
    launcher = _FakeLauncher()
    ctl.set_launcher(launcher)

    ok = await ctl.resume("wf_1")

    assert ok is True
    assert launcher.relaunched == [({}, "s")]


@pytest.mark.asyncio
async def test_resume_prefers_invoking_tool_over_global_slot():
    """A dead ticket launcher routes to the INVOKING tool (``via``), not the slot.

    The invoking member's tool is alive by construction (its ``action="resume"``
    call is on the stack), so pinning the relaunch host to it keeps narration
    and completion injection on the member that decided to resume — instead of
    on whichever member's tool happened to register as the global slot last
    (possible whenever another member finishes a round between this member's
    cycle start and its resume call).
    """
    ctl = BackgroundTaskController()
    dead_tool = _ToolWithHarness(_StatefulHarness())
    dead_tool._parent_agent.state = HarnessState.TERMINATED
    invoker = _ToolWithHarness(_StatefulHarness())
    other_member = _ToolWithHarness(_StatefulHarness())
    ctl.register(_make_handle("wf_1", launcher=dead_tool))
    await ctl.pause("wf_1")
    ctl.set_launcher(other_member)  # registered after the invoker's cycle

    ok = await ctl.resume("wf_1", via=invoker)

    assert ok is True
    assert dead_tool.relaunched == []
    assert other_member.relaunched == []
    assert invoker.relaunched == [({}, "s")]


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


# ---------------------------------------------------------------------------
# Owner-scoped stop (C4): member teardown stops only that member's runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_by_owner_leaves_other_members_runs_running():
    """Teardown of member X stops X's two runs; the leader's stays active."""
    ctl = BackgroundTaskController()
    x1 = _make_handle("wf_x1", owner_member="X")
    x2 = _make_handle("wf_x2", owner_member="X")
    leader = _make_handle("wf_leader", owner_member=None)
    ctl.register(x1)
    ctl.register(x2)
    ctl.register(leader)

    ok = await ctl.stop_by_owner("X")

    assert ok is True
    assert "wf_x1" not in ctl._active and "wf_x2" not in ctl._active
    assert "wf_x1" not in ctl._paused and "wf_x2" not in ctl._paused
    # The leader's run is untouched: still registered, never aborted.
    assert "wf_leader" in ctl._active
    assert leader.abort_event.is_set() is False
    assert x1.abort_event.reason == "stop" and x1.abort_event.is_set()
    assert x2.abort_event.reason == "stop" and x2.abort_event.is_set()


@pytest.mark.asyncio
async def test_stop_by_owner_no_match_is_noop():
    """No run owned by the member (or a None name) → False, nothing touched."""
    ctl = BackgroundTaskController()
    h = _make_handle("wf_1", owner_member="X")
    ctl.register(h)

    assert await ctl.stop_by_owner("Y") is False
    assert await ctl.stop_by_owner(None) is False
    assert "wf_1" in ctl._active
    assert h.abort_event.is_set() is False


@pytest.mark.asyncio
async def test_stop_by_owner_keeps_paused_runs_parked_by_default():
    """Round-end teardown must not drop paused tickets: pause→resume depends on them.

    A team pause parks the runs first, then tears the run cycle down — the
    teardown hook sees the tickets already in _paused. Dropping them would
    break resume (not_found), so only active runs are stopped here.
    """
    ctl = BackgroundTaskController()
    launcher = _FakeLauncher()
    ctl.set_launcher(launcher)
    ctl.register(_make_handle("wf_x1", owner_member="X"))
    await ctl.pause("wf_x1")

    ok = await ctl.stop_by_owner("X")

    assert ok is False  # nothing active to stop
    assert "wf_x1" in ctl._paused  # ticket intact for a later resume


@pytest.mark.asyncio
async def test_stop_by_owner_include_paused_drops_parked_tickets():
    """Permanent teardown (member dispose) also drops the member's paused tickets."""
    ctl = BackgroundTaskController()
    launcher = _FakeLauncher()
    ctl.set_launcher(launcher)
    ctl.register(_make_handle("wf_x1", owner_member="X"))
    await ctl.pause("wf_x1")

    ok = await ctl.stop_by_owner("X", include_paused=True)

    assert ok is True
    assert "wf_x1" not in ctl._paused
    assert await ctl.resume("wf_x1") is False and launcher.relaunched == []


# ---------------------------------------------------------------------------
# Harness teardown ordering (C4): the controller stop precedes the native
# teardown on dispose only. The round-end stop must not touch owned runs —
# they are hosted by the member runtime / team controller and live past the
# round (results inject into a later cycle).
# ---------------------------------------------------------------------------


def _teardown_host(owner="teammate-a", member_name="teammate-a"):
    """A TeamHarness-shaped stub exercising the real _stop_owned_swarmflow_runs.

    ``owner`` is what controller.stop_by_owner receives; ``member_name`` is the
    TeamHarness's own name (set to None to simulate the no-name no-op path).
    """
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness

    calls = []

    class _Controller:
        async def stop_by_owner(self, member, *, include_paused=False):
            calls.append(("stop_by_owner", member, include_paused))

    class _Native:
        state = HarnessState.IDLE

        async def stop(self):
            calls.append(("native_stop",))

        async def dispose(self):
            calls.append(("native_dispose",))

    harness = TeamHarness.__new__(TeamHarness)
    harness._native = _Native()
    harness._bg_controller = _Controller() if owner is not None else None
    harness._member_name = member_name
    harness._active_agent_session = None
    return harness, calls


@pytest.mark.asyncio
async def test_round_end_stop_keeps_owned_runs_running():
    """TeamHarness.stop: round recycle must not stop owned runs.

    Runs belong to the member runtime / team controller, not to this cycle's
    native; stopping them here would kill live runs at every round end
    (the persistent-team round recycle calls stop per round).
    """
    harness, calls = _teardown_host()

    await harness.stop()

    assert calls == [("native_stop",)]


@pytest.mark.asyncio
async def test_permanent_dispose_stops_owned_runs_before_native_dispose():
    """TeamHarness.dispose: controller stop precedes the permanent teardown.

    ``include_paused=True``: nobody can relaunch after a permanent teardown,
    so parked tickets of this member are dropped too.
    """
    harness, calls = _teardown_host()

    await harness.dispose()

    assert calls == [("stop_by_owner", "teammate-a", True), ("native_dispose",)]


@pytest.mark.asyncio
async def test_teardown_without_controller_or_name_is_noop():
    """No controller (teammates today) or no member name: straight teardown."""
    harness, calls = _teardown_host(owner=None)
    await harness.stop()
    assert calls == [("native_stop",)]

    harness, calls = _teardown_host(member_name=None)
    await harness.dispose()
    assert calls == [("native_dispose",)]


@pytest.mark.asyncio
async def test_teardown_survives_controller_failure():
    """A controller failure is logged, never blocks the teardown."""
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness

    class _BrokenController:
        async def stop_by_owner(self, member, *, include_paused=False):
            raise RuntimeError("controller down")

    class _Native:
        state = HarnessState.IDLE

        async def stop(self):
            teardown.append("native_stop")

        async def dispose(self):
            teardown.append("native_dispose")

    teardown = []
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = _Native()
    harness._bg_controller = _BrokenController()
    harness._member_name = "teammate-a"
    harness._active_agent_session = None

    await harness.dispose()

    assert teardown == ["native_dispose"]
