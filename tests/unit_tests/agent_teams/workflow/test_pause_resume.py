# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-level pause/resume: the ``abort_event`` checkpoints + journal replay.

Offline (no agent_teams coupling, no LLM): a gated backend blocks a chosen call
so a pause can be timed precisely. Covers the entry gate (a paused run starts no
new agent), the pre-journal guard (an in-flight call interrupted mid-pause does
not persist to the WAL), and resume (the completed prefix is a cache hit; only
the interrupted call reruns live).
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from openjiuwen.agent_teams.workflow.engine import journal as journal_mod
from openjiuwen.agent_teams.workflow.engine import run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted
from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal

_ABC_SCRIPT = '''
from swarmflow import agent

META = {"name": "abc", "description": "three sequential agents", "phases": []}

async def run(args):
    a = await agent("task A", label="A")
    b = await agent("task B", label="B")
    c = await agent("task C", label="C")
    return [a, b, c]
'''


class _GatedBackend(AgentBackend):
    """Records executed labels; optionally blocks a label until its gate is set."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.call_keys: list[tuple[str, str | None]] = []
        self._gates: dict[str, asyncio.Event] = {}
        self.started: dict[str, asyncio.Event] = {}

    def gate(self, label: str) -> asyncio.Event:
        """Make ``label`` block inside ``run`` until the returned event is set."""
        ev = asyncio.Event()
        self._gates[label] = ev
        self.started[label] = asyncio.Event()
        return ev

    async def run(
        self, prompt: str, opts: dict, schema_json: dict | None, *, call_key: str | None = None
    ) -> AgentResult:
        label = opts.get("label") or "?"
        self.executed.append(label)
        self.call_keys.append((label, call_key))
        started = self.started.get(label)
        if started is not None:
            started.set()
        gate = self._gates.get(label)
        if gate is not None:
            await gate.wait()
        return AgentResult(text=f"{label}-done", tokens=1)


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def _wal_labels(journal_path: str) -> list[str]:
    """Labels persisted in the WAL sidecar, in append order (call records only)."""
    wal = Path(f"{journal_path}.wal")
    if not wal.exists():
        return []
    labels: list[str] = []
    for line in wal.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("type") in ("pause", "seal"):
            continue  # run-level record, not an agent call
        labels.append(rec.get("label"))
    return labels


def test_abort_event_blocks_new_agents(tmp_path):
    """A pre-set abort_event makes the first agent() raise; nothing runs or journals."""
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")
    backend = _GatedBackend()
    ev = AbortSignal()
    ev.set()

    async def _scenario() -> str:
        try:
            await run_workflow(script, backend=backend, journal_path=journal_path, abort_event=ev)
        except WorkflowAborted:
            return "aborted"
        return "completed"

    assert asyncio.run(_scenario()) == "aborted"
    assert backend.executed == []
    assert _wal_labels(journal_path) == []


def test_in_flight_agent_not_journaled(tmp_path):
    """Pause while B runs: A is journaled, B's result is dropped (pre-journal guard)."""
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")
    backend = _GatedBackend()
    b_gate = backend.gate("B")
    ev = AbortSignal()

    async def _scenario() -> None:
        task = asyncio.create_task(
            run_workflow(script, backend=backend, journal_path=journal_path, abort_event=ev)
        )
        await backend.started["B"].wait()  # A done + journaled, B in flight
        ev.set()  # pause
        b_gate.set()  # let B's backend return → pre-journal guard fires
        with pytest.raises(WorkflowAborted):
            await task

    asyncio.run(_scenario())
    assert backend.executed == ["A", "B"]  # both ran; B interrupted before journaling
    assert _wal_labels(journal_path) == ["A"]  # only A persisted; C never started


def test_resume_cache_hit_prefix(tmp_path):
    """Resume reruns only the interrupted call; the completed prefix is a cache hit."""
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")

    # First run: A completes, B interrupted by the pause.
    backend1 = _GatedBackend()
    b_gate = backend1.gate("B")
    ev = AbortSignal()

    async def _first() -> None:
        task = asyncio.create_task(
            run_workflow(script, backend=backend1, journal_path=journal_path, abort_event=ev)
        )
        await backend1.started["B"].wait()
        ev.set()
        b_gate.set()
        with pytest.raises(WorkflowAborted):
            await task

    asyncio.run(_first())
    assert backend1.executed == ["A", "B"]

    # Resume: A cache-hits (not re-run); B (interrupted) + C run live.
    backend2 = _GatedBackend()

    async def _resume():
        return await run_workflow(
            script, backend=backend2, journal_path=journal_path, resume=journal_path
        )

    result = asyncio.run(_resume())
    assert backend2.executed == ["B", "C"]  # A not re-run
    assert result == ["A-done", "B-done", "C-done"]


def test_backend_receives_stable_call_key_across_resume(tmp_path):
    """The call-path key a backend sees is deterministic across a resume replay.

    The worker member name (hence the worktree slug) derives from this key, so
    A (replayed as a cache hit in run 2) and B (rerun live) must map to the
    same keys they had in run 1 — a per-backend counter cannot do that, since
    hit calls never reach the backend and the counter restarts at zero.
    """
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")

    backend1 = _GatedBackend()
    b_gate = backend1.gate("B")
    ev = AbortSignal()

    async def _first() -> None:
        task = asyncio.create_task(
            run_workflow(script, backend=backend1, journal_path=journal_path, abort_event=ev)
        )
        await backend1.started["B"].wait()
        ev.set()
        b_gate.set()
        with pytest.raises(WorkflowAborted):
            await task

    asyncio.run(_first())

    backend2 = _GatedBackend()

    async def _resume():
        return await run_workflow(
            script, backend=backend2, journal_path=journal_path, resume=journal_path
        )

    asyncio.run(_resume())
    # Backend 1 ran A (key 0) then B (key 1). Backend 2 replays A as a hit
    # (never reaches the backend) and reruns B live — B must still arrive with
    # the SAME call key it had in run 1, not a re-counted one.
    run1_keys = dict(backend1.call_keys)
    run2_keys = dict(backend2.call_keys)
    assert run1_keys["B"] is not None
    assert run2_keys["B"] == run1_keys["B"]


_ISO_SCRIPT = '''
from swarmflow import agent

META = {"name": "iso", "description": "two agents, the first switchable", "phases": []}

async def run(args):
    a = await agent("task A", label="A"@OPTIONS_A@)
    b = await agent("task B", label="B")
    return [a, b]
'''


def test_resume_hits_untouched_call_and_reruns_edited_isolation(tmp_path):
    """Editing a call's isolation re-keys it; untouched siblings still hit.

    Run 1 pauses during B, so A's record is durable. The edited script adds
    ``isolation='worktree'`` to A only (same prompt/label). On relaunch A must
    MISS (its signature changed) and rerun live — the worktree the caller now
    asked for actually gets created — while B is unchanged and behaves as
    before. Without isolation in the signature the edited call would silently
    resume-hit the old record and skip the worktree (SDD-0005 §4.3 gap).
    """
    journal_path = str(tmp_path / "j.jsonl")

    plain = _write(tmp_path, "iso.py", _ISO_SCRIPT.replace("@OPTIONS_A@", ""))
    backend1 = _GatedBackend()
    b_gate = backend1.gate("B")
    ev = AbortSignal()

    async def _first() -> None:
        task = asyncio.create_task(
            run_workflow(plain, backend=backend1, journal_path=journal_path, abort_event=ev)
        )
        await backend1.started["B"].wait()
        ev.set()
        b_gate.set()
        with pytest.raises(WorkflowAborted):
            await task

    asyncio.run(_first())
    assert backend1.executed == ["A", "B"]

    edited = _write(
        tmp_path,
        "iso_edited.py",
        _ISO_SCRIPT.replace("@OPTIONS_A@", ', options={"isolation": "worktree"}'),
    )
    backend2 = _GatedBackend()

    async def _relaunch():
        return await run_workflow(
            edited, backend=backend2, journal_path=journal_path, resume=journal_path
        )

    result = asyncio.run(_relaunch())
    # A's signature changed (isolation folded in) → miss → rerun live.
    # Without the fix A would be a silent cache hit and "A" would not appear.
    assert "A" in backend2.executed
    assert result == ["A-done", "B-done"]


def test_resume_still_hits_when_isolation_untouched(tmp_path):
    """The isolation-aware signature is byte-stable when isolation is absent.

    Journals written before this change must keep hitting: omitting isolation
    yields the exact legacy identity dict, so this guards against accidentally
    re-keying every existing cache.
    """
    journal_path = str(tmp_path / "j.jsonl")

    plain = _write(tmp_path, "iso.py", _ISO_SCRIPT.replace("@OPTIONS_A@", ""))
    backend1 = _GatedBackend()
    b_gate = backend1.gate("B")
    ev = AbortSignal()

    async def _first() -> None:
        task = asyncio.create_task(
            run_workflow(plain, backend=backend1, journal_path=journal_path, abort_event=ev)
        )
        await backend1.started["B"].wait()
        ev.set()
        b_gate.set()
        with pytest.raises(WorkflowAborted):
            await task

    asyncio.run(_first())

    backend2 = _GatedBackend()

    async def _relaunch():
        return await run_workflow(
            plain, backend=backend2, journal_path=journal_path, resume=journal_path
        )

    asyncio.run(_relaunch())
    # Same script, same keys/sigs → A is a hit and never reaches the backend.
    assert backend2.executed == ["B"]


def _wal_run_records(journal_path: str) -> list[dict]:
    """Run-level records (``pause`` / ``seal`` / ``args``) in the WAL, append order."""
    wal = Path(f"{journal_path}.wal")
    if not wal.exists():
        return []
    records: list[dict] = []
    for line in wal.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("type") not in ("pause", "seal"):
            continue  # call records carry a structural key, not a run record
        records.append(rec)
    return records


def _hold_run_records(record_type: str) -> tuple[asyncio.Event, asyncio.Event, "journal_mod.Journal._append_wal"]:
    """Patch ``Journal._append_wal`` to gate ``record_type`` behind an event.

    Returns ``(started, release, patched)``: install ``patched`` with
    monkeypatch, wait ``started`` — the run task is then deterministically
    suspended inside the record write, exactly where a controller stop's
    pending cancellation lands — then issue the cancellations and set
    ``release`` to let the write finish. Event-driven end to end: no sleeps.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    orig = journal_mod.Journal._append_wal

    async def _hold(self, record):
        if record.get("type") == record_type:
            started.set()
            await release.wait()
        return await orig(self, record)

    return started, release, _hold


@pytest.mark.parametrize("cancels", [1, 2])
def test_stop_seal_survives_cancel_during_seal_write(tmp_path, monkeypatch, cancels):
    """A stop whose seal write is cancelled mid-flight still seals the WAL.

    The controller's three-step abort cancels the engine task after setting the
    abort signal; when the pre-journal guard has already raised
    ``WorkflowAborted(stop)`` the pending cancellation lands on the seal-write
    await inside the exception handler (a member teardown may cancel a second
    time mid-write). Two invariants, both asserted here:

    * the seal still reaches the WAL — otherwise a same-run_id relaunch
      replays the terminated run's cache instead of forcing a fresh id;
    * the cancellation is re-raised once the write completes — a stop request
      must not be silently swallowed (CancelledError is a BaseException).
    """
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")
    backend = _GatedBackend()
    b_gate = backend.gate("B")
    ev = AbortSignal()

    async def _scenario() -> None:
        started, release, patched = _hold_run_records("seal")
        monkeypatch.setattr(journal_mod.Journal, "_append_wal", patched)
        task = asyncio.create_task(
            run_workflow(script, backend=backend, journal_path=journal_path, abort_event=ev)
        )
        await backend.started["B"].wait()  # A journaled, B in flight
        ev.set("stop")
        b_gate.set()  # B returns → pre-journal guard raises WorkflowAborted(stop)
        await started.wait()  # engine is suspended inside the seal write
        for _ in range(cancels):
            task.cancel()
        release.set()  # write proceeds; the shield defers the pending cancels
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())
    seals = [rec for rec in _wal_run_records(journal_path) if rec.get("type") == "seal"]
    assert [rec.get("terminal_status") for rec in seals] == ["stopped"]


def test_pause_record_survives_cancel_during_write(tmp_path, monkeypatch):
    """A pause interrupted by a cancellation still writes its pause record.

    Same contract as the seal race above: the record lands first, then the
    deferred CancelledError is re-raised so the controller's pause (a silent
    cancel the relauncher consumes) still terminates the engine task.
    """
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")
    backend = _GatedBackend()
    b_gate = backend.gate("B")
    ev = AbortSignal()

    async def _scenario() -> None:
        started, release, patched = _hold_run_records("pause")
        monkeypatch.setattr(journal_mod.Journal, "_append_wal", patched)
        task = asyncio.create_task(
            run_workflow(script, backend=backend, journal_path=journal_path, abort_event=ev)
        )
        await backend.started["B"].wait()
        ev.set("pause")
        b_gate.set()
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())
    pauses = [rec for rec in _wal_run_records(journal_path) if rec.get("type") == "pause"]
    assert [rec.get("pause_reason") for rec in pauses] == ["pause"]


def test_stop_cancel_while_call_in_flight_still_seals(tmp_path):
    """Regression: cancel-first (no checkpoint reached) seals via the CancelledError path.

    The task is suspended at the backend call when ``cancel()`` bites, so the
    engine unwinds through ``except asyncio.CancelledError`` — the branch that
    pre-dated the shielded writes. Its seal must keep landing.
    """
    script = _write(tmp_path, "abc.py", _ABC_SCRIPT)
    journal_path = str(tmp_path / "j.jsonl")
    backend = _GatedBackend()
    b_gate = backend.gate("B")
    ev = AbortSignal()

    async def _scenario() -> None:
        task = asyncio.create_task(
            run_workflow(script, backend=backend, journal_path=journal_path, abort_event=ev)
        )
        await backend.started["B"].wait()
        ev.set("stop")
        task.cancel()  # cancel while B is suspended at the gate
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())
    seals = [rec for rec in _wal_run_records(journal_path) if rec.get("type") == "seal"]
    assert [rec.get("terminal_status") for rec in seals] == ["stopped"]


def test_terminal_write_inner_cancel_propagates_without_spinning():
    """A cancellation that kills the WRITE task itself propagates promptly.

    The deferral loop discriminates by cancel origin: a CancelledError that
    came through the shield from the write task (no pending cancel on this
    coroutine) is re-raised immediately — the record can never land. The
    pre-fix loop treated it as a deferred outer cancel and re-shielded the
    terminated task forever; awaiting a done-cancelled task never yields to
    the loop, so the spin starves it (not even ``asyncio.timeout`` can fire).
    The daemon-thread watchdog is what turns that regression into a failure
    instead of a hung run.
    """
    from openjiuwen.agent_teams.workflow.engine.runner import _await_terminal_write

    outcome: dict[str, str] = {}

    async def cancelled_write():
        raise asyncio.CancelledError()

    async def scenario() -> None:
        try:
            await _await_terminal_write(cancelled_write)
        except asyncio.CancelledError:
            outcome["result"] = "propagated"

    def run_scenario() -> None:
        asyncio.run(scenario())

    worker = threading.Thread(target=run_scenario, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "terminal write never finished: livelock"
    assert outcome.get("result") == "propagated"
