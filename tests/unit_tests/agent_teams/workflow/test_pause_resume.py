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
from pathlib import Path

import pytest

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
