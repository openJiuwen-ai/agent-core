# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the SwarmFlow verify() primitive (SDD-0017).

Covers the business-agnostic engine tally + orchestration (run offline against
MockBackend with pinned fixtures), the business-side ``build_reviewers``, and
the ``swarmflow.md`` tool-description guidance assertion.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.workflow.engine import MockBackend, Reviewer, run_workflow
from openjiuwen.agent_teams.workflow.engine.errors import EngineError
from openjiuwen.agent_teams.workflow.engine.verify import settle_verify_tally
from openjiuwen.agent_teams.workflow.engine.backends.mock import SKIP
from openjiuwen.agent_teams.workflow.review import build_reviewers


# ─────────────────── pure tally (settle_verify_tally) ───────────────────
def test_tally_verdict_one_vote_veto():
    """Any verdict fail fails the round; all pass passes it."""
    assert settle_verify_tally({"verdict_total": 2, "verdict_voted": 2, "verdict_fail_count": 1}) == "fail"
    assert settle_verify_tally({"verdict_total": 2, "verdict_voted": 2, "verdict_fail_count": 0}) == "pass"


def test_tally_verdict_undecided_when_not_fully_voted():
    """A verdict pool with a not-voted reviewer is undecided, never a silent pass."""
    assert settle_verify_tally({"verdict_total": 2, "verdict_voted": 1, "verdict_fail_count": 0}) is None


def test_tally_score_threshold():
    """Score pool passes only when the average reaches the threshold."""
    at = {"score_count": 2, "score_voted": 2, "score_avg": 0.85}
    below = {"score_count": 2, "score_voted": 2, "score_avg": 0.84}
    assert settle_verify_tally(at) == "pass"
    assert settle_verify_tally(below) == "fail"
    assert settle_verify_tally(at, threshold=0.9) == "fail"


def test_tally_score_undecided_when_not_fully_voted():
    """A score pool with a not-voted reviewer is undecided."""
    assert settle_verify_tally({"score_count": 2, "score_voted": 1, "score_avg": 0.9}) is None


def test_tally_empty_pools_defaults_to_pass():
    """Both pools absent is a defensive pass (unreachable via verify())."""
    assert settle_verify_tally({}) == "pass"


def test_tally_mixed_pools_combine_strictest():
    """verdict fail beats a passing score pool; a passing verdict plus low score fails."""
    verdict_fail = {
        "verdict_total": 1,
        "verdict_voted": 1,
        "verdict_fail_count": 1,
        "score_count": 1,
        "score_voted": 1,
        "score_avg": 0.9,
    }
    low_score = {
        "verdict_total": 1,
        "verdict_voted": 1,
        "verdict_fail_count": 0,
        "score_count": 1,
        "score_voted": 1,
        "score_avg": 0.7,
    }
    assert settle_verify_tally(verdict_fail) == "fail"
    assert settle_verify_tally(low_score) == "fail"


# ─────────────────── verify() orchestration (MockBackend) ───────────────────
_VERIFY_SCRIPT = """
from swarmflow import verify, Reviewer

META = {"name": "verify-round", "description": "run a verify round", "phases": []}

async def run(args):
    return await verify(args["reviewers"])
"""


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def _run_verify(tmp_path, reviewers, fixtures):
    script = _write(tmp_path, "verify.py", _VERIFY_SCRIPT)
    return asyncio.run(run_workflow(str(script), args={"reviewers": reviewers}, backend=MockBackend(fixtures=fixtures)))


def test_verify_all_pass(tmp_path):
    """A passing verdict reviewer plus a high score reviewer passes the round."""
    reviewers = [
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="score", prompt="score", label="s"),
    ]
    fixtures = {
        "v": {"decision": "pass", "feedback": "good"},
        "s": {"score": 0.92, "feedback": "solid"},
    }
    result = _run_verify(tmp_path, reviewers, fixtures)
    assert result.passed is True
    assert result.verdict == "pass"
    assert len(result.votes) == 2
    assert result.votes[0].decision is True
    assert result.votes[1].score == 0.92


def test_verify_fail_aggregates_feedback(tmp_path):
    """A verdict fail fails the round and aggregates non-empty feedback."""
    reviewers = [Reviewer(kind="verdict", prompt="check", label="v")]
    fixtures = {"v": {"decision": "fail", "feedback": "edge case broken"}}
    result = _run_verify(tmp_path, reviewers, fixtures)
    assert result.passed is False
    assert result.verdict == "fail"
    assert "edge case broken" in result.feedback
    assert result.votes[0].decision is False


def test_verify_undecided_when_reviewer_did_not_vote(tmp_path):
    """A reviewer whose agent() is skipped yields undecided, not a silent pass."""
    reviewers = [Reviewer(kind="verdict", prompt="check", label="v")]
    result = _run_verify(tmp_path, reviewers, {"v": SKIP})
    assert result.verdict is None
    assert result.passed is False
    assert result.votes[0].decision is None


def test_verify_empty_reviewers_raises(tmp_path):
    """An empty reviewer list is rejected rather than silently passing."""
    script = _write(tmp_path, "empty.py", _VERIFY_SCRIPT)
    with pytest.raises(EngineError):
        asyncio.run(run_workflow(str(script), args={"reviewers": []}, backend=MockBackend()))


def test_verify_parallel_reviewers_get_unique_ids(tmp_path):
    """Each reviewer runs as its own structured agent with a unique id."""
    from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind, WorkflowProgressEvent

    reviewers = [
        Reviewer(kind="verdict", prompt="a", label="va"),
        Reviewer(kind="verdict", prompt="b", label="vb"),
    ]
    events: list[WorkflowProgressEvent] = []
    script = _write(tmp_path, "verify_ids.py", _VERIFY_SCRIPT)
    asyncio.run(
        run_workflow(
            str(script),
            args={"reviewers": reviewers},
            backend=MockBackend(
                fixtures={"va": {"decision": "pass", "feedback": ""}, "vb": {"decision": "pass", "feedback": ""}}
            ),
            progress_sink=events.append,
        )
    )
    started = [e.agent_id for e in events if e.kind == ProgressKind.AGENT_STARTED]
    assert len(started) == 2 and len(set(started)) == 2


# ─────────────────── build_reviewers (business helper) ───────────────────
def test_build_reviewers_type_kind_mapping():
    """verifier/challenger map to verdict, inspector maps to score."""
    reviewers = build_reviewers(
        "text",
        [{"type": "verifier"}, {"type": "inspector"}, {"type": "challenger"}],
    )
    assert [r.kind for r in reviewers] == ["verdict", "score", "verdict"]


def test_build_reviewers_text_deliverable_inline():
    """Inline text is embedded verbatim, even with braces, and acceptance is filled."""
    reviewers = build_reviewers(
        "def f(x): return {x}",
        [{"type": "verifier", "instruction": "run tests"}],
        acceptance="must compile",
    )
    prompt = reviewers[0].prompt
    assert "def f(x): return {x}" in prompt
    assert "run tests" in prompt
    assert "must compile" in prompt


def test_build_reviewers_path_deliverable_lists_files():
    """A path list is rendered as a bulleted list for the reviewer to read."""
    reviewers = build_reviewers(["src/a.py", "docs/b.md"], [{"type": "challenger"}], language="en")
    assert "- src/a.py" in reviewers[0].prompt
    assert "- docs/b.md" in reviewers[0].prompt


def test_build_reviewers_inspector_default_rubric():
    """An inspector without an instruction falls back to the default dimension table."""
    for language in ("cn", "en"):
        reviewers = build_reviewers("x", [{"type": "inspector"}], language=language)
        prompt = reviewers[0].prompt
        assert ("维度" in prompt) or ("Dimension" in prompt)


def test_build_reviewers_label_default():
    """Reviewers without a label get a type-indexed default label."""
    reviewers = build_reviewers("x", [{"type": "verifier"}, {"type": "inspector"}])
    assert reviewers[0].label == "verifier-0"
    assert reviewers[1].label == "inspector-1"


def test_build_reviewers_unknown_type_raises():
    """An explicitly unknown reviewer type fails fast instead of silently falling back."""
    with pytest.raises(ValueError, match="unknown reviewer type"):
        build_reviewers("x", [{"type": "verfy"}])


def test_build_reviewers_missing_type_defaults_to_verifier():
    """A spec without a type still defaults to verifier (a lenient default, not a typo)."""
    reviewers = build_reviewers("x", [{"instruction": "check"}])
    assert len(reviewers) == 1
    assert reviewers[0].kind == "verdict"


def test_verify_malformed_decision_is_undecided_not_pass(tmp_path):
    """A reviewer returning a non-pass/fail decision records a None vote and stays undecided."""
    reviewers = [Reviewer(kind="verdict", prompt="check", label="v")]
    # A malformed decision (not a real backend path, but the defensive branch must be consistent).
    result = _run_verify(tmp_path, reviewers, {"v": {"decision": "banana", "feedback": "?"}})
    assert result.verdict is None
    assert result.passed is False
    assert result.votes[0].decision is None


def test_verify_score_reviewer_not_voted_is_undecided(tmp_path):
    """A score reviewer that did not vote (SKIP) yields undecided, not a silent pass."""
    reviewers = [Reviewer(kind="score", prompt="score", label="s")]
    result = _run_verify(tmp_path, reviewers, {"s": SKIP})
    assert result.verdict is None
    assert result.passed is False
    assert result.votes[0].score is None


def test_verify_malformed_score_is_undecided_not_crash(tmp_path):
    """A non-numeric score (fixture bypassing schema) does not crash; it reads undecided."""
    reviewers = [Reviewer(kind="score", prompt="score", label="s")]
    result = _run_verify(tmp_path, reviewers, {"s": {"score": "high", "feedback": "?"}})
    assert result.verdict is None
    assert result.passed is False
    assert result.votes[0].score is None


# ─────────────────── swarmflow.md guidance assertion ───────────────────
def test_swarmflow_md_contains_reviewer_guidance():
    """Both language tool descriptions document the reviewer composition guidance."""
    from pathlib import Path

    from openjiuwen.agent_teams import paths as team_paths

    base = Path(team_paths.__file__).parent / "tools" / "locales" / "descs"
    cn = (base / "cn" / "workflow" / "swarmflow.md").read_text(encoding="utf-8")
    en = (base / "en" / "workflow" / "swarmflow.md").read_text(encoding="utf-8")

    assert "组成建议" in cn
    assert "composition guidance" in en
    # Both list the three reviewer types.
    for text in (cn, en):
        assert "verifier" in text and "inspector" in text and "challenger" in text
