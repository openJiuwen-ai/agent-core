# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Business-agnostic verify primitives for SwarmFlow (SDD-0017).

This module owns the *generic* review semantics of the ``verify()`` primitive:
the neutral ``Reviewer`` / ``VerifyVote`` / ``VerifyResult`` data shapes, the
two vote schemas (binary pass/fail and 0-1 score), and the pure tally
``settle_verify_tally`` (one-vote veto over the binary pool + threshold over
the score pool). It imports **no** ``agent_teams`` business module — the engine
stays pluggable (铁律 1), and the orchestration itself (spawning one ``agent()``
per reviewer) lives in :mod:`workflow.engine.primitives`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Vote kind: ``verdict`` = binary pass/fail (business: verifier/challenger);
#: ``score`` = 0-1 numeric (business: inspector). The engine only knows the
#: *tally semantics* behind a kind, never the business persona behind it.
ReviewerKind = Literal["verdict", "score"]
Verdict = Literal["pass", "fail"]

# Neutral JSON-Schema for a verdict vote. The engine validates against it via
# ``agent(schema=...)``; the mock backend synthesises conforming objects for
# offline runs (fixtures pin real values in tests).
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "fail"]},
        "feedback": {"type": "string"},
    },
    "required": ["decision", "feedback"],
}

# Neutral JSON-Schema for a score vote (0-1, inclusive).
SCORE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "feedback": {"type": "string"},
    },
    "required": ["score", "feedback"],
}


@dataclass
class Reviewer:
    """One reviewer to run against a deliverable.

    ``kind`` selects the vote schema / tally pool; ``prompt`` is the fully
    rendered review instruction (the business layer builds it, e.g. from the
    ``swarmflow_reviewer_*`` templates); ``label`` / ``options`` ride through
    to the underlying ``agent()`` call.
    """

    kind: ReviewerKind
    prompt: str
    label: str | None = None
    options: dict | None = None


@dataclass
class VerifyVote:
    """The resolved vote of one reviewer (None fields = reviewer did not vote)."""

    kind: ReviewerKind
    decision: bool | None = None  # verdict kind: True = pass
    score: float | None = None  # score kind: 0-1
    feedback: str = ""  # reason / scoring report / threat list


@dataclass
class VerifyResult:
    """Outcome of one ``verify()`` round.

    ``verdict`` is ``None`` (undecided) when any reviewer did not vote; the
    script decides whether to retry, since a single ``verify()`` never waits on
    a rework pass.
    """

    verdict: Verdict | None
    votes: list[VerifyVote] = field(default_factory=list)
    feedback: str = ""  # aggregated review feedback (feed into a rework pass)
    passed: bool = False


def settle_verify_tally(tally: dict, threshold: float = 0.85) -> Verdict | None:
    """Judge a review tally into ``pass`` / ``fail`` / ``None`` (undecided).

    Mirrors the scheduled-dispatch verdict semantics (SDD-0015): the binary
    pool (``verdict``) waits for every reviewer to vote, then any fail fails
    the round; the score pool (``score``) waits for every reviewer to vote,
    then the average must meet ``threshold``.

    A pool with expected reviewers that has not voted yet returns undecided, so
    a reviewer whose ``agent()`` returned ``None`` never silently passes. The
    "no pools" branch (both totals zero) is a defensive default — ``verify()``
    rejects an empty reviewer list at the entry, so it is unreachable via the
    primitive.
    """
    verdict_total = tally.get("verdict_total", 0)
    if verdict_total > 0:
        if tally.get("verdict_voted", 0) < verdict_total:
            return None
        if tally.get("verdict_fail_count", 0) > 0:
            return "fail"

    score_total = tally.get("score_count", 0)
    if score_total > 0:
        if tally.get("score_voted", 0) < score_total:
            return None
        avg = tally.get("score_avg")
        if avg is None or avg < threshold:
            return "fail"

    return "pass"


__all__ = [
    "Reviewer",
    "VerifyVote",
    "VerifyResult",
    "ReviewerKind",
    "Verdict",
    "VERDICT_SCHEMA",
    "SCORE_SCHEMA",
    "settle_verify_tally",
]
