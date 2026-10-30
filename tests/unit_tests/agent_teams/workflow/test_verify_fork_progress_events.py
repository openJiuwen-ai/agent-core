# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for verify()/fork() structured progress events (SDD-0017 / SDD-0014).

Covers the engine's VERIFY_STARTED / VERIFY_COMPLETED progress events (round
boundary, verdict, per-reviewer votes with node-matching names) and the
``parent_session_id`` / ``member_name`` carried by session AGENT_STARTED turns.
Runs offline against MockBackend with pinned fixtures; events are captured via
``run_workflow(progress_sink=...)``.
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.workflow.engine import MockBackend, Reviewer, run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.mock import SKIP
from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind

# ─────────────────── shared script templates ───────────────────
_VERIFY_SCRIPT = """
from swarmflow import verify

META = {"name": "verify-events", "description": "verify progress events", "phases": []}

async def run(args):
    return await verify(args["reviewers"], **args.get("kwargs", {}))
"""

_FORK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "fork-events", "description": "fork progress events", "phases": []}

_ANSWER = {
    "type": "object", "additionalProperties": False, "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}

async def run(args):
    parent = agent_session(label="arch")
    await parent.send("baseline", schema=_ANSWER)
    child = await parent.fork(fork_mode="full", label="child")
    reply = await child.send("deepen", schema=_ANSWER)
    return {"child": reply}
"""


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def _run(script_path, args, fixtures, events):
    return asyncio.run(
        run_workflow(
            script_path,
            args=args,
            backend=MockBackend(fixtures=fixtures),
            progress_sink=events.append,
        )
    )


# ─────────────────── verify progress events ───────────────────
def test_verify_started_settled_round_boundary(tmp_path):
    """One verify() round emits exactly one STARTED then one COMPLETED, in order."""
    events = []
    reviewers = [
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="score", prompt="score", label="s"),
    ]
    _run(
        _write(tmp_path, "verify.py", _VERIFY_SCRIPT),
        {"reviewers": reviewers, "kwargs": {"threshold": 0.6}},
        {
            "verify-v": {"decision": "pass", "feedback": "good"},
            "verify-s": {"score": 0.92, "feedback": "solid"},
        },
        events,
    )
    kinds = [e.kind for e in events]
    assert kinds.count(ProgressKind.VERIFY_STARTED) == 1
    assert kinds.count(ProgressKind.VERIFY_COMPLETED) == 1
    assert kinds.index(ProgressKind.VERIFY_STARTED) < kinds.index(ProgressKind.VERIFY_COMPLETED)
    started = next(e for e in events if e.kind == ProgressKind.VERIFY_STARTED)
    assert started.verify_reviewers == 2
    assert started.verify_threshold == 0.6
    assert started.label == "verify"  # default base label
    # custom labels gain the base prefix; the settled verdict reads the
    # fixtures (not the synth fallback), proving the lookup keyed correctly
    assert started.verify_reviewer_labels == ["verify-v", "verify-s"]
    settled = next(e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED)
    assert settled.verify_verdict == "pass"


def test_verify_settled_votes_carry_node_matching_names(tmp_path):
    """Each vote mirrors the reviewer agent node's label rule (label or base-index)."""
    events = []
    reviewers = [
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="score", prompt="score"),  # no label -> "round-0-score-1"
    ]
    _run(
        _write(tmp_path, "verify.py", _VERIFY_SCRIPT),
        {"reviewers": reviewers, "kwargs": {"label": "round-0", "threshold": 0.5}},
        {
            "round-0-v": {"decision": "pass", "feedback": "ok"},
            "round-0-score-1": {"score": 0.7, "feedback": "decent"},
        },
        events,
    )
    settled = next(e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED)
    assert settled.label == "round-0"
    assert settled.verify_verdict == "pass"
    assert settled.verify_reviewers == 2
    by_name = {v["name"]: v for v in settled.verify_votes}
    assert set(by_name) == {"round-0-v", "round-0-score-1"}
    assert by_name["round-0-v"]["decision"] == "pass"
    assert by_name["round-0-v"]["voted"] is True
    assert by_name["round-0-score-1"]["score"] == 0.7
    # reviewer agent nodes exist under the same names
    started_names = {
        e.label for e in events if e.kind == ProgressKind.AGENT_STARTED
    }
    assert {"round-0-v", "round-0-score-1"} <= started_names


def test_verify_settled_votes_carry_agent_ids(tmp_path):
    """Each vote's agent_id is its reviewer agent node's deterministic id."""
    events = []
    reviewers = [
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="score", prompt="score", label="s"),
    ]
    _run(
        _write(tmp_path, "verify.py", _VERIFY_SCRIPT),
        {"reviewers": reviewers},
        {"verify-v": {"decision": "pass", "feedback": ""}, "verify-s": {"score": 0.9, "feedback": ""}},
        events,
    )
    started_ids = [e.agent_id for e in events if e.kind == ProgressKind.AGENT_STARTED]
    settled = next(e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED)
    assert [v["agent_id"] for v in settled.verify_votes] == started_ids


def test_verify_labels_unique_across_rounds(tmp_path):
    """Two verify() rounds under one label produce disjoint reviewer labels.

    The business layer's default {type}-{i} label restarts at 0 every round;
    verify() must namespace each reviewer label with the round's base label so
    a consumer can join votes/reviewers to rounds by exact name.
    """
    script = """
from swarmflow import verify, Reviewer

META = {"name": "verify-rounds", "description": "two rounds", "phases": []}

async def run(args):
    spec = [Reviewer(kind="verdict", prompt="check", label=None)]
    await verify(spec, label="verify")
    await verify(spec, label="verify")
"""
    events = []
    _run(
        _write(tmp_path, "rounds.py", script),
        {},
        {"verify-verdict-0": {"decision": "pass", "feedback": ""}},
        events,
    )
    rosters = [e.verify_reviewer_labels for e in events if e.kind == ProgressKind.VERIFY_STARTED]
    assert rosters == [["verify-verdict-0"], ["verify-verdict-0"]]
    # labels collide only in name, never in node identity: each round's agent
    # node carries a distinct structural agent_id.
    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    assert len({e.agent_id for e in started}) == 2


def test_concurrent_default_label_verifies_pair_by_verify_id(tmp_path):
    """Parallel same-label verify() rounds stay distinct via verify_id.

    Production repro (fork-multi-model): 9 fan-out verify() calls all default
    to label "verify". Rounds are NOT re-labeled (same label = one card ×N
    rounds, mirroring same-name agents); instead every round carries a
    verify_id (its structural call position — the verify analog of an
    agent node's agent_id), and each COMPLETED pairs with its own STARTED by
    that id, never by label alone.
    """
    script = """
from swarmflow import verify, Reviewer, parallel

META = {"name": "verify-parallel", "description": "concurrent same label", "phases": []}

async def one():
    return await verify([Reviewer(kind="verdict", prompt="check", label=None)])

async def run(args):
    return await parallel([one, one, one])
"""
    events = []
    result = _run(
        _write(tmp_path, "par.py", script),
        {},
        {"verify-verdict-0": {"decision": "pass", "feedback": ""}},
        events,
    )
    assert all(r.verdict == "pass" for r in result)
    started = [e for e in events if e.kind == ProgressKind.VERIFY_STARTED]
    assert len(started) == 3
    # one shared label — rounds, not renamed groups
    assert {e.label for e in started} == {"verify"}
    # verify ids are pairwise distinct and present on both event kinds
    ids = [e.verify_id for e in started]
    assert all(ids) and len(set(ids)) == 3
    settled_by_id = {
        e.verify_id: e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED
    }
    assert set(settled_by_id) == set(ids)
    # reviewer labels repeat across rounds (same default label) — round
    # identity, not name uniqueness, keeps the rounds apart
    for st in started:
        se = settled_by_id[st.verify_id]
        assert [v["name"] for v in se.verify_votes] == st.verify_reviewer_labels


def test_verify_settled_undecided_marks_unvoted(tmp_path):
    """A skipped reviewer yields verdict=None and its vote reads voted=False."""
    events = []
    reviewers = [
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="verdict", prompt="check2", label="v2"),
    ]
    _run(
        _write(tmp_path, "verify.py", _VERIFY_SCRIPT),
        {"reviewers": reviewers},
        {"verify-v": {"decision": "pass", "feedback": ""}, "verify-v2": SKIP},
        events,
    )
    settled = next(e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED)
    assert settled.verify_verdict is None  # undecided, never a silent pass
    by_name = {v["name"]: v for v in settled.verify_votes}
    assert by_name["verify-v"]["decision"] == "pass"
    assert by_name["verify-v"]["voted"] is True
    assert by_name["verify-v2"]["voted"] is False
    assert by_name["verify-v2"]["decision"] is None


def test_verify_fail_decision_mapped(tmp_path):
    """A verdict fail maps to decision='fail' in the settled votes."""
    events = []
    reviewers = [Reviewer(kind="verdict", prompt="check", label="v")]
    _run(
        _write(tmp_path, "verify.py", _VERIFY_SCRIPT),
        {"reviewers": reviewers},
        {"verify-v": {"decision": "fail", "feedback": "broken"}},
        events,
    )
    settled = next(e for e in events if e.kind == ProgressKind.VERIFY_COMPLETED)
    assert settled.verify_verdict == "fail"
    assert settled.verify_votes[0]["decision"] == "fail"


# ─────────────────── fork parent_session_id ───────────────────
def test_fork_child_started_carries_parent_member_name(tmp_path):
    """A fork child's AGENT_STARTED carries the parent's member_name (unique key)."""
    events = []
    result = _run(
        _write(tmp_path, "fork.py", _FORK_SCRIPT),
        {},
        {
            "arch": {"answer": "base"},
            "child": {"answer": "branch"},
        },
        events,
    )
    assert result["child"] == {"answer": "branch"}
    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    assert [e.node_type for e in started] == ["agent_session", "agent_session_fork"]
    assert started[0].parent_session_id is None  # the parent turn itself
    assert started[1].parent_session_id == "mock-arch"  # parent's member_name
    assert started[1].label == "child"
    # Session turns carry their member_name as the stable UI join key.
    assert started[0].member_name == "mock-arch"
    assert started[1].member_name == "mock-child"


def test_plain_session_turns_have_no_parent(tmp_path):
    """agent_session turns outside a fork never carry parent_session_id."""
    script = """
from swarmflow import agent_session

META = {"name": "plain-session", "description": "plain session", "phases": []}

async def run(args):
    s = agent_session(label="solo")
    reply = await s.send("hi")
    return {"reply": reply}
"""
    events = []
    _run(_write(tmp_path, "plain.py", script), {}, {"solo": "hello"}, events)
    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED]
    assert len(started) == 1
    assert started[0].node_type == "agent_session"
    assert started[0].parent_session_id is None
    assert started[0].member_name == "mock-solo"


# ─────────────────── agent_completed cache_tokens ───────────────────
def test_agent_completed_carries_cache_tokens(tmp_path):
    """AGENT_COMPLETED passes the backend's cache/token split through to the sink."""
    from openjiuwen.agent_teams.workflow.engine.backends.base import AgentResult

    script = """
from swarmflow import agent

META = {"name": "cache-tokens", "description": "cache passthrough", "phases": []}

async def run(args):
    return await agent("hi", label="greeter")
"""
    events = []

    class _CacheBackend(MockBackend):
        async def run(self, prompt, opts, schema_json, *, call_key=None):
            return AgentResult(
                text="hello", tokens=100, cache_tokens=64,
                input_tokens=80, output_tokens=20,
            )

    asyncio.run(
        run_workflow(
            _write(tmp_path, "cache.py", script),
            args={},
            backend=_CacheBackend(),
            progress_sink=events.append,
        )
    )
    completed = next(e for e in events if e.kind == ProgressKind.AGENT_COMPLETED)
    assert completed.tokens == 100
    assert completed.cache_tokens == 64
    assert completed.token_input == 80
    assert completed.token_output == 20
