# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-layer tests for the stateful session primitives.

These exercise the business-agnostic core added for ``agent_session`` /
``human_session`` / ``human`` — multi-turn history accumulation, the options-bag
whitelist, journal cache-hit short-circuit (resume), and the ``open/send/close/
aclose`` backend lifecycle — entirely offline with a recording backend and the
deterministic ``MockBackend``. No agent_teams coupling, no LLM, no network.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.workflow.engine import (
    MockBackend,
    ProgressKind,
    WorkflowError,
    WorkflowProgressEvent,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult


class _RecordingBackend(AgentBackend):
    """Backend that records the session lifecycle so tests can assert on it.

    ``send_turn`` echoes the prompt and the *prior* history length, which lets a
    test prove that context accumulates across turns and that cache hits never
    reach the backend.
    """

    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str | None]] = []  # (sid, kind, instructions)
        self.turns: list[tuple[str, str, int]] = []  # (sid, prompt, prior_history_len)
        self.correlations: list[str | None] = []  # correlation_id per send_turn
        self.closed: list[str] = []
        self.aclosed = 0
        self._sid_n = 0

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        if schema_json is not None:
            return AgentResult(structured={"v": prompt})
        return AgentResult(text=f"ran:{prompt}")

    async def open_session(self, *, kind: str, instructions: str | None, opts: dict) -> str:
        sid = f"s{self._sid_n}"
        self._sid_n += 1
        self.opened.append((sid, kind, instructions))
        return sid

    async def send_turn(self, session_id, prompt, opts, schema_json, *, history=(), correlation_id=None) -> AgentResult:
        self.turns.append((session_id, prompt, len(history)))
        self.correlations.append(correlation_id)
        if schema_json is not None:
            return AgentResult(structured={"echo": prompt, "n": len(history)})
        return AgentResult(text=f"turn:{prompt}:{len(history)}")

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def aclose(self) -> None:
        self.aclosed += 1


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


_MULTI_TURN_SCRIPT = '''
from swarmflow import agent_session

META = {"name": "sess", "description": "multi-turn", "phases": []}

async def run(args):
    s = agent_session(label="chat", instructions="be brief")
    a = await s.send("first")
    b = await s.send("second")
    c = await s.send("third")
    return [a, b, c]
'''


def test_agent_session_accumulates_context_across_turns(tmp_path):
    """Each turn sees the prior turns' (user, assistant) history grow by two."""
    script = _write(tmp_path, "sess.py", _MULTI_TURN_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    # One avatar opened lazily on the first send; reused for all three turns.
    assert len(backend.opened) == 1
    sid, kind, instructions = backend.opened[0]
    assert kind == "agent" and instructions == "be brief"
    # Prior-history length grows 0 -> 2 -> 4 (one (user, assistant) pair per turn).
    assert [t[2] for t in backend.turns] == [0, 2, 4]
    assert all(t[0] == sid for t in backend.turns)
    assert result == ["turn:first:0", "turn:second:2", "turn:third:4"]
    # Run-end teardown closed the backend exactly once.
    assert backend.aclosed == 1


_SCHEMA_SESSION_SCRIPT = '''
from swarmflow import agent_session

META = {"name": "schema-sess", "description": "structured turns", "phases": []}

SCHEMA = {
    "type": "object",
    "properties": {"echo": {"type": "string"}, "n": {"type": "integer"}},
    "required": ["echo", "n"],
}

async def run(args):
    s = agent_session(label="q")
    return await s.send("ask", schema=SCHEMA)
'''


def test_agent_session_structured_turn_returns_dict(tmp_path):
    """A session turn with a JSON-Schema returns a conforming dict."""
    script = _write(tmp_path, "schema_sess.py", _SCHEMA_SESSION_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert isinstance(result, dict) and result == {"echo": "ask", "n": 0}


_HUMAN_SESSION_SCRIPT = '''
from swarmflow import human_session

META = {"name": "human-sess", "description": "human multi-turn", "phases": []}

async def run(args):
    h = human_session(label="lead", instructions="confirm")
    a = await h.send("approve?")
    b = await h.send("and the budget?")
    return [a, b]
'''


def test_human_session_routes_kind_human_and_keeps_context(tmp_path):
    """A human session opens with kind='human' and accumulates context too."""
    script = _write(tmp_path, "human_sess.py", _HUMAN_SESSION_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert len(backend.opened) == 1 and backend.opened[0][1] == "human"
    assert [t[2] for t in backend.turns] == [0, 2]
    assert result == ["turn:approve?:0", "turn:and the budget?:2"]


_HUMAN_CORR_SCRIPT = '''
from swarmflow import human_session, phase

META = {"name": "hc", "description": "human correlation ids", "phases": []}

async def run(args):
    phase("review")
    h = human_session(label="lead")
    a = await h.send("q1")
    b = await h.send("q2")
    return [a, b]
'''


def test_human_correlation_id_is_deterministic_phase_label_turn(tmp_path):
    """A human turn's correlation id is deterministic (phase:label:turn), not a uuid."""
    script = _write(tmp_path, "hc.py", _HUMAN_CORR_SCRIPT)
    backend = _RecordingBackend()

    asyncio.run(run_workflow(script, backend=backend))

    # Deterministic, human-readable, stable across a replay — never random.
    assert backend.correlations == ["review:lead:0", "review:lead:1"]


def test_agent_turn_correlation_id_is_none(tmp_path):
    """Agent turns carry no correlation id (only human turns do)."""
    script = _write(tmp_path, "sess.py", _MULTI_TURN_SCRIPT)
    backend = _RecordingBackend()

    asyncio.run(run_workflow(script, backend=backend))

    assert backend.correlations == [None, None, None]


_HUMAN_ONESHOT_SCRIPT = '''
from swarmflow import human

META = {"name": "human-1", "description": "one-shot human", "phases": []}

async def run(args):
    return await human("pick one")
'''


def test_human_one_shot_opens_and_closes_its_ephemeral_session(tmp_path):
    """``human()`` opens an ephemeral session and closes it after the single turn."""
    script = _write(tmp_path, "human1.py", _HUMAN_ONESHOT_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert len(backend.opened) == 1 and backend.opened[0][1] == "human"
    # The ephemeral session is explicitly closed by human()'s finally block.
    assert backend.closed == [backend.opened[0][0]]
    assert result == "turn:pick one:0"


_HUMAN_ONESHOT_LABELLED_SCRIPT = '''
from swarmflow import human, phase

META = {"name": "human-lbl", "description": "one-shot human with label", "phases": []}

async def run(args):
    phase("signoff")
    return await human("approve?", label="host")
'''


def test_human_one_shot_accepts_label_and_phase(tmp_path):
    """``human(label=..., phase=...)`` mirrors agent()/sessions and labels the turn.

    A one-shot ``human`` must accept ``label`` (and ``phase``) like ``agent`` and
    ``human_session`` do; the label/phase flow into the deterministic human
    correlation id (``phase:label:turn``).
    """
    script = _write(tmp_path, "humanlbl.py", _HUMAN_ONESHOT_LABELLED_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert backend.correlations == ["signoff:host:0"]
    assert result == "turn:approve?:0"


_BAD_OPTION_SCRIPT = '''
from swarmflow import agent_session

META = {"name": "bad-opt", "description": "unknown option", "phases": []}

async def run(args):
    s = agent_session()
    return await s.send("hi", options={"bogus": 1})
'''


def test_options_bag_rejects_unknown_key(tmp_path):
    """An unknown ``options`` key fails fast rather than silently no-opping."""
    script = _write(tmp_path, "bad_opt.py", _BAD_OPTION_SCRIPT)
    backend = _RecordingBackend()

    with pytest.raises(WorkflowError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "bogus" in str(exc.value)
    # The bad turn never reached the backend.
    assert backend.turns == []


_NOTIFY_SCRIPT = '''
from swarmflow import agent_session

META = {"name": "notify", "description": "one-way notify", "phases": []}

async def run(args):
    s = agent_session(label="ann")
    reply = await s.send("question")
    pushed = await s.send("fyi: decided", notify=True)
    after = await s.send("next")
    return {"reply": reply, "pushed": pushed, "after": after}
'''


def test_notify_returns_none_but_still_advances_context(tmp_path):
    """``notify=True`` returns None yet records the turn so context continues."""
    script = _write(tmp_path, "notify.py", _NOTIFY_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert result["pushed"] is None  # one-way push has no return value
    # The notify turn still hit the backend and grew history (0 -> 2 -> 4).
    assert [t[2] for t in backend.turns] == [0, 2, 4]
    assert result["after"] == "turn:next:4"


def test_notify_with_schema_is_rejected(tmp_path):
    """``notify=True`` is text-only; combining it with a schema raises."""
    src = '''
from swarmflow import agent_session

META = {"name": "bad-notify", "description": "notify+schema", "phases": []}

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

async def run(args):
    s = agent_session()
    return await s.send("hi", schema=SCHEMA, notify=True)
'''
    script = _write(tmp_path, "bad_notify.py", src)
    with pytest.raises(WorkflowError):
        asyncio.run(run_workflow(script, backend=_RecordingBackend()))


def test_resume_replays_session_turns_without_opening_a_session(tmp_path):
    """A --resume run is a pure cache replay: no open_session, no send_turn."""
    script = _write(tmp_path, "sess.py", _MULTI_TURN_SCRIPT)
    journal = str(tmp_path / "run.jsonl")

    first = _RecordingBackend()
    asyncio.run(run_workflow(script, backend=first, journal_path=journal))
    assert len(first.turns) == 3 and len(first.opened) == 1

    second = _RecordingBackend()
    replay_events: list[WorkflowProgressEvent] = []
    result = asyncio.run(
        run_workflow(script, backend=second, resume=journal, progress_sink=replay_events.append)
    )
    # Pure replay: the avatar is never built and no turn reaches the backend.
    assert second.opened == [] and second.turns == []
    # Yet the script still produced the same answers (rehydrated from journal).
    assert result == ["turn:first:0", "turn:second:2", "turn:third:4"]
    completed = [e for e in replay_events if e.kind == ProgressKind.AGENT_COMPLETED]
    assert len(completed) == 3


def test_resume_after_upstream_change_reopens_and_reruns(tmp_path):
    """Changing an early turn's prompt invalidates it and every later turn.

    The journal is keyed by *structural call path* (call ordinals), which is
    independent of the file name — so editing the script and resuming works the
    same whether the edit lands in the same file or a renamed copy. Distinct file
    names are used here to keep the test free of the source loader's
    (mtime + size) bytecode-cache, which a same-path same-size rewrite could hit.
    """
    v1 = _MULTI_TURN_SCRIPT
    v2 = _MULTI_TURN_SCRIPT.replace('await s.send("second")', 'await s.send("SECOND")')
    journal = str(tmp_path / "run.jsonl")
    asyncio.run(
        run_workflow(_write(tmp_path, "v1.py", v1), backend=_RecordingBackend(), journal_path=journal)
    )

    backend = _RecordingBackend()
    result = asyncio.run(
        run_workflow(_write(tmp_path, "v2.py", v2), backend=backend, resume=journal)
    )

    # Turn 1 is a hit (not re-run); turns 2 and 3 re-run (2 changed, 3 depends on it).
    prompts = [t[1] for t in backend.turns]
    assert prompts == ["SECOND", "third"]
    assert result[1] == "turn:SECOND:2" and result[2] == "turn:third:4"


def test_agent_call_signature_unchanged_by_history_param(tmp_path):
    """A stateless ``agent()`` resume is unaffected by the new history parameter.

    ``agent()`` always passes empty history, so its signature is byte-identical to
    before — a stateless workflow still replays as a pure cache hit.
    """
    src = '''
from swarmflow import agent

META = {"name": "stateless", "description": "single-shot", "phases": []}

async def run(args):
    return await agent("do it", label="once")
'''
    script = _write(tmp_path, "stateless.py", src)
    journal = str(tmp_path / "run.jsonl")
    asyncio.run(run_workflow(script, backend=_RecordingBackend(), journal_path=journal))

    second = _RecordingBackend()
    asyncio.run(run_workflow(script, backend=second, resume=journal))
    # Pure replay through the single-shot path: run() never called.
    assert second.turns == []
