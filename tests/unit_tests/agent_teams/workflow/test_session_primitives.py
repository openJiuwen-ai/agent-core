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
    ProgressKind,
    EngineError,
    WorkflowProgressEvent,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult


class _RecordingBackend(AgentBackend):
    """Backend that records the session lifecycle so tests can assert on it.

    ``send_turn`` echoes the prompt and the *prior* history length, which lets a
    test prove that context accumulates across turns and that cache hits never
    reach the backend. ``capture_fork`` records the call and returns a canned
    ``fork_data`` so the engine's fork plumbing (eager capture, history-mirror
    inheritance, child seeding) is exercised deterministically.
    """

    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str | None]] = []  # (sid, kind, instructions)
        self.turns: list[tuple[str, str, int]] = []  # (sid, prompt, prior_history_len)
        self.correlations: list[str | None] = []  # correlation_id per send_turn
        self.closed: list[str] = []
        self.aclosed = 0
        self._sid_n = 0
        self.forks: list[tuple[str, int | None, str]] = []  # (sid, keep_rounds, fork_mode)
        self.seeded: list[tuple[str, dict | None]] = []  # (sid, fork_data) via open_session
        self.fork_data: dict | None = {"messages": [{"role": "user", "content": "forked"}]}
        self.named: list[str] = []  # member names reserved via ensure_member_name

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        if schema_json is not None:
            return AgentResult(structured={"v": prompt})
        return AgentResult(text=f"ran:{prompt}")

    async def capture_fork(self, session_id: str, *, keep_rounds: int | None, fork_mode: str) -> dict | None:
        self.forks.append((session_id, keep_rounds, fork_mode))
        return self.fork_data

    async def ensure_member_name(self, *, kind: str, opts: dict) -> str:
        name = f"m{self._sid_n}"
        self._sid_n += 1
        self.named.append(name)
        return name

    async def open_session(
        self,
        *,
        kind: str,
        instructions: str | None,
        opts: dict,
        fork_data: dict | None = None,
        member_name: str | None = None,
    ) -> str:
        sid = member_name or f"s{self._sid_n}"
        if member_name is None:
            self._sid_n += 1
        self.opened.append((sid, kind, instructions))
        self.seeded.append((sid, fork_data))
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


_MULTI_TURN_SCRIPT = """
from swarmflow import agent_session

META = {"name": "sess", "description": "multi-turn", "phases": []}

async def run(args):
    s = agent_session(label="chat", instructions="be brief")
    a = await s.send("first")
    b = await s.send("second")
    c = await s.send("third")
    return [a, b, c]
"""


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


_SCHEMA_SESSION_SCRIPT = """
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
"""


def test_agent_session_structured_turn_returns_dict(tmp_path):
    """A session turn with a JSON-Schema returns a conforming dict."""
    script = _write(tmp_path, "schema_sess.py", _SCHEMA_SESSION_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert isinstance(result, dict) and result == {"echo": "ask", "n": 0}


_HUMAN_SESSION_SCRIPT = """
from swarmflow import human_session

META = {"name": "human-sess", "description": "human multi-turn", "phases": []}

async def run(args):
    h = human_session(label="lead", instructions="confirm")
    a = await h.send("approve?")
    b = await h.send("and the budget?")
    return [a, b]
"""


def test_human_session_routes_kind_human_and_keeps_context(tmp_path):
    """A human session opens with kind='human' and accumulates context too."""
    script = _write(tmp_path, "human_sess.py", _HUMAN_SESSION_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert len(backend.opened) == 1 and backend.opened[0][1] == "human"
    assert [t[2] for t in backend.turns] == [0, 2]
    assert result == ["turn:approve?:0", "turn:and the budget?:2"]


_HUMAN_CORR_SCRIPT = """
from swarmflow import human_session, phase

META = {"name": "hc", "description": "human correlation ids", "phases": []}

async def run(args):
    phase("review")
    h = human_session(label="lead")
    a = await h.send("q1")
    b = await h.send("q2")
    return [a, b]
"""


def test_human_correlation_id_is_deterministic_phase_label_turn(tmp_path):
    """A human turn's correlation id is deterministic (phase:label:turn), not a uuid."""
    script = _write(tmp_path, "hc.py", _HUMAN_CORR_SCRIPT)
    backend = _RecordingBackend()

    asyncio.run(run_workflow(script, backend=backend))

    # Deterministic, human-readable, stable across a replay — never random.
    assert backend.correlations == ["review:lead:0", "review:lead:1"]


def test_agent_turn_correlation_id_is_deterministic(tmp_path):
    """Agent session turns carry the same deterministic id shape as human turns."""
    script = _write(tmp_path, "sess.py", _MULTI_TURN_SCRIPT)
    backend = _RecordingBackend()

    asyncio.run(run_workflow(script, backend=backend))

    assert backend.correlations == ["_:chat:0", "_:chat:1", "_:chat:2"]


_HUMAN_ONESHOT_SCRIPT = """
from swarmflow import human

META = {"name": "human-1", "description": "one-shot human", "phases": []}

async def run(args):
    return await human("pick one")
"""


def test_human_one_shot_opens_and_closes_its_ephemeral_session(tmp_path):
    """``human()`` opens an ephemeral session and closes it after the single turn."""
    script = _write(tmp_path, "human1.py", _HUMAN_ONESHOT_SCRIPT)
    backend = _RecordingBackend()

    result = asyncio.run(run_workflow(script, backend=backend))

    assert len(backend.opened) == 1 and backend.opened[0][1] == "human"
    # The ephemeral session is explicitly closed by human()'s finally block.
    assert backend.closed == [backend.opened[0][0]]
    assert result == "turn:pick one:0"


_HUMAN_ONESHOT_LABELLED_SCRIPT = """
from swarmflow import human, phase

META = {"name": "human-lbl", "description": "one-shot human with label", "phases": []}

async def run(args):
    phase("signoff")
    return await human("approve?", label="host")
"""


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


_BAD_OPTION_SCRIPT = """
from swarmflow import agent_session

META = {"name": "bad-opt", "description": "unknown option", "phases": []}

async def run(args):
    s = agent_session()
    return await s.send("hi", options={"bogus": 1})
"""


def test_options_bag_rejects_unknown_key(tmp_path):
    """An unknown ``options`` key fails fast rather than silently no-opping."""
    script = _write(tmp_path, "bad_opt.py", _BAD_OPTION_SCRIPT)
    backend = _RecordingBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "bogus" in str(exc.value)
    # The bad turn never reached the backend.
    assert backend.turns == []


_NOTIFY_SCRIPT = """
from swarmflow import agent_session

META = {"name": "notify", "description": "one-way notify", "phases": []}

async def run(args):
    s = agent_session(label="ann")
    reply = await s.send("question")
    pushed = await s.send("fyi: decided", notify=True)
    after = await s.send("next")
    return {"reply": reply, "pushed": pushed, "after": after}
"""


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
    src = """
from swarmflow import agent_session

META = {"name": "bad-notify", "description": "notify+schema", "phases": []}

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

async def run(args):
    s = agent_session()
    return await s.send("hi", schema=SCHEMA, notify=True)
"""
    script = _write(tmp_path, "bad_notify.py", src)
    with pytest.raises(EngineError):
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
    # Yet the member identity IS still reserved on the first turn (no avatar, no
    # LLM) — fork() needs it to locate the parent's persisted context after a
    # fully-hit resume. The name is identical across the runs (counter stable).
    assert second.named == first.named == ["m0"]
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
    src = """
from swarmflow import agent

META = {"name": "stateless", "description": "single-shot", "phases": []}

async def run(args):
    return await agent("do it", label="once")
"""
    script = _write(tmp_path, "stateless.py", src)
    journal = str(tmp_path / "run.jsonl")
    asyncio.run(run_workflow(script, backend=_RecordingBackend(), journal_path=journal))

    second = _RecordingBackend()
    asyncio.run(run_workflow(script, backend=second, resume=journal))
    # Pure replay through the single-shot path: run() never called.
    assert second.turns == []


_MULTI_LABEL_SESSION_SCRIPT = """
from swarmflow import agent_session, human_session, phase

META = {"name": "multi-label", "description": "same-label sessions", "phases": []}

async def run(args):
    phase("review")
    a = agent_session(label="chat")
    h = human_session(label="host")
    await a.send("a1")
    await a.send("a2")
    await h.send("h1")
    await h.send("h2")
    return None
"""


_NODE_TYPE_SCRIPT = """
from swarmflow import agent, agent_session, human, human_session

META = {"name": "node-types", "description": "primitive node types", "phases": []}

async def run(args):
    await agent("agent prompt", label="agent")
    await agent_session(label="agent_session").send("agent session prompt")
    await human("human prompt", label="human")
    await human_session(label="human_session").send("human session prompt")
"""


def test_agent_started_carries_explicit_node_type_for_each_primitive(tmp_path):
    """Each primitive identifies its exact node type on AGENT_STARTED."""
    script = _write(tmp_path, "node_types.py", _NODE_TYPE_SCRIPT)
    events: list[WorkflowProgressEvent] = []

    asyncio.run(
        run_workflow(
            script,
            backend=_RecordingBackend(),
            progress_sink=events.append,
        )
    )

    started = [event for event in events if event.kind == ProgressKind.AGENT_STARTED]
    assert {event.label: event.node_type for event in started} == {
        "agent": "agent",
        "agent_session": "agent_session",
        "human": "human",
        "human_session": "human_session",
    }


def test_agent_started_carries_agent_id_node_type_correlation_id(tmp_path):
    """AGENT_STARTED carries agent_id, node_type, and correlation_id for session turns.

    agent_session turns: node_type=agent_session, correlation_id=phase:label:turn, distinct agent_ids.
    human_session turns: node_type=human_session, correlation_id=phase:label:turn, distinct ids.
    No is_human flag is emitted; node_type is the sole source of node kind.
    """
    script = _write(tmp_path, "multi_label.py", _MULTI_LABEL_SESSION_SCRIPT)
    backend = _RecordingBackend()
    events: list[WorkflowProgressEvent] = []
    asyncio.run(run_workflow(script, backend=backend, progress_sink=events.append))

    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    assert len(started) == 4

    # agent_session turns (chat): agent_session, deterministic correlation_id, distinct agent_ids.
    agent_turns = [e for e in started if e.label == "chat"]
    assert len(agent_turns) == 2
    assert all(e.node_type == "agent_session" for e in agent_turns)
    assert [e.correlation_id for e in agent_turns] == ["review:chat:0", "review:chat:1"]
    assert all(e.agent_id is not None for e in agent_turns)
    assert len({e.agent_id for e in agent_turns}) == 2

    # human_session turns (host): human_session, deterministic correlation_id, distinct ids.
    human_turns = [e for e in started if e.label == "host"]
    assert len(human_turns) == 2
    assert all(e.node_type == "human_session" for e in human_turns)
    assert [e.correlation_id for e in human_turns] == ["review:host:0", "review:host:1"]
    assert all(e.agent_id is not None for e in human_turns)
    assert len({e.agent_id for e in human_turns}) == 2

    # No id collisions across agent and human turns of the same label pool.
    assert len({e.agent_id for e in started}) == 4


def test_agent_completed_matches_started_by_agent_id(tmp_path):
    """AGENT_COMPLETED carries the same agent_id as its AGENT_STARTED (exact match)."""
    script = _write(tmp_path, "multi_label2.py", _MULTI_LABEL_SESSION_SCRIPT)
    backend = _RecordingBackend()
    events: list[WorkflowProgressEvent] = []
    asyncio.run(run_workflow(script, backend=backend, progress_sink=events.append))

    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    completed = [e for e in events if e.kind == ProgressKind.AGENT_COMPLETED]
    assert len(started) == len(completed) == 4

    started_by_id = {e.agent_id: e for e in started}
    for c in completed:
        assert c.agent_id is not None
        assert c.agent_id in started_by_id
        assert started_by_id[c.agent_id].label == c.label


# ----------------------------------------------------------------------
# Fork: engine plumbing (eager capture, mirror inheritance, child seeding)
# ----------------------------------------------------------------------

_FORK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "fork", "description": "session fork", "phases": []}

async def run(args):
    a = agent_session(label="parent")
    await a.send("q1")
    await a.send("q2")
    b = await a.fork(fork_mode="before", keep_rounds=1, label="child")
    await b.send("q3")
    await a.send("q4")   # parent keeps evolving, child unaffected
    return None
"""


def test_fork_eagerly_captures_and_seeds_child(tmp_path):
    """fork() captures the parent eagerly, and the child's first turn opens with
    the captured fork_data (never re-runs the parent's turns)."""
    script = _write(tmp_path, "fork.py", _FORK_SCRIPT)
    backend = _RecordingBackend()
    events: list[WorkflowProgressEvent] = []
    asyncio.run(run_workflow(script, backend=backend, progress_sink=events.append))

    # One eager capture on the fork call, with the round-based split.
    assert len(backend.forks) == 1
    sid, keep_rounds, fork_mode = backend.forks[0]
    assert keep_rounds == 1 and fork_mode == "before"

    # The child session was opened with the captured fork_data.
    child_open = [s for s in backend.seeded if s[1] is not None]
    assert len(child_open) == 1
    child_sid, fork_data = child_open[0]
    assert child_sid != sid  # a distinct session identity
    assert fork_data == backend.fork_data

    # The parent's 2 turns + child's 1 + parent's 1 = 4 backend turns.
    assert len(backend.turns) == 4
    # The child turn carries the inherited history mirror (its own + parent's).
    child_turn = [t for t in backend.turns if t[1] == "q3"][0]
    assert child_turn[2] == 4  # parent's 2 rounds + child's own mirror prefix

    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    child_started = [e for e in started if e.label == "child"]
    assert len(child_started) == 1
    assert child_started[0].node_type == "agent_session_fork"


def test_fork_defaults_to_full_when_no_keep_rounds(tmp_path):
    """fork() with no keep_rounds captures a full-context fork (mode='full')."""
    script = _write(
        tmp_path,
        "fork_full.py",
        """
from swarmflow import agent_session

META = {"name": "fork-full", "description": "fork full", "phases": []}

async def run(args):
    a = agent_session(label="parent")
    await a.send("q1")
    b = await a.fork(label="child")
    await b.send("q2")
    return None
""",
    )
    backend = _RecordingBackend()
    asyncio.run(run_workflow(script, backend=backend))
    assert backend.forks == [("m0", None, "full")]  # captured by reserved member name


def test_fork_non_full_requires_keep_rounds(tmp_path):
    """fork_mode != 'full' without keep_rounds is a clear error, not a silent full."""
    script = _write(
        tmp_path,
        "fork_missing_rounds.py",
        """
from swarmflow import agent_session

META = {"name": "fork-missing-rounds", "description": "fork missing keep_rounds", "phases": []}

async def run(args):
    a = agent_session(label="parent")
    await a.send("q1")
    b = await a.fork(fork_mode="before", label="child")  # keep_rounds omitted
    await b.send("q2")
    return None
""",
    )
    backend = _RecordingBackend()
    with pytest.raises(EngineError, match="keep_rounds"):
        asyncio.run(run_workflow(script, backend=backend))
    # Nothing reached the backend: the error fires before any capture.
    assert backend.forks == []


def test_fork_of_human_session_rejected(tmp_path):
    """fork() on a human_session raises a clear EngineError."""
    script = _write(
        tmp_path,
        "fork_human.py",
        """
from swarmflow import human_session

META = {"name": "fork-human", "description": "human fork rejected", "phases": []}

async def run(args):
    h = human_session(label="host")
    await h.send("q1")
    await h.fork(label="child")
    return None
""",
    )
    backend = _RecordingBackend()
    with pytest.raises(EngineError, match="fork"):
        asyncio.run(run_workflow(script, backend=backend))
    # No capture ever reached the backend for the rejected human fork.
    assert backend.forks == []


def test_fork_child_can_fork_again_chained(tmp_path):
    """A forked child can itself fork (chain), each capturing its own context."""
    script = _write(
        tmp_path,
        "fork_chain.py",
        """
from swarmflow import agent_session

META = {"name": "fork-chain", "description": "chained fork", "phases": []}

async def run(args):
    a = agent_session(label="a")
    await a.send("q1")
    b = await a.fork(label="b")
    await b.send("q2")
    c = await b.fork(label="c")
    await c.send("q3")
    return None
""",
    )
    backend = _RecordingBackend()
    asyncio.run(run_workflow(script, backend=backend))
    # Two captures: b forks from a, c forks from b.
    assert len(backend.forks) == 2
    # Three sessions opened: the parent (no fork_data) + b and c (each seeded).
    assert len(backend.seeded) == 3
    assert backend.seeded[0][1] is None  # parent opened cold
    forked = [s for s in backend.seeded if s[1] is not None]
    assert len(forked) == 2  # both b and c opened with a fork_data
    assert forked[0][0] != forked[1][0]  # distinct child identities


def test_fork_parent_not_contaminated_by_child(monkeypatch):
    """fork() returns a fresh session object; the parent's history is untouched."""
    from openjiuwen.agent_teams.workflow.engine.primitives import AgentSession

    backend = _RecordingBackend()

    async def scenario():
        a = AgentSession(label="p")
        a._history.append({"role": "user", "content": "u1"})
        a._history.append({"role": "assistant", "content": "a1"})
        b = await a.fork(label="c")
        b._history.append({"role": "user", "content": "u2"})
        b._history.append({"role": "assistant", "content": "a2"})
        return a, b

    a, b = asyncio.run(scenario())
    assert len(a._history) == 2  # parent mirror untouched by the child's appends
    assert len(b._history) == 4  # child = inherited 2 + its own 2
    assert b._history[:2] == [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]
    # The child inherited the mirror deeply enough that mutating it is safe.
    assert a._history[0] == {"role": "user", "content": "u1"}
    assert a._history[1] == {"role": "assistant", "content": "a1"}
    # capture_fork was not called on the raw object (no backend/runtime).
    assert backend.forks == []
