# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the verify() vote contract (issue #6306).

Pins two contracts: every vote carries its reviewer's display ``label``
(reading ``v.label`` in a script must work — it was the issue's crash), and the
loader's pre-launch lint rejects vote/result attribute typos and a
used-but-unimported ``build_reviewers`` with a ``LintError`` before the module
is imported (zero backend calls).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.workflow.engine import MockBackend, run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.mock import SKIP


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def _run(tmp_path, name: str, src: str, fixtures: dict) -> Any:
    script = _write(tmp_path, name, src)
    return asyncio.run(
        run_workflow(script, args=None, backend=MockBackend(fixtures=fixtures))
    )


# ─────────────────── votes carry the reviewer label ───────────────────
_LABELS_SCRIPT = """
META = {"name": "vote-contract", "description": "vote contract checks", "phases": []}

async def run(args):
    r = await verify([
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="score", prompt="score", label="s"),
    ])
    return [v.label for v in r.votes]
"""


def test_votes_carry_reviewer_labels(tmp_path):
    """Both verdict- and score-kind votes expose the reviewer's (prefixed) label."""
    result = _run(tmp_path, "labels.py", _LABELS_SCRIPT, {
        "verify-v": {"decision": "pass", "feedback": "ok"},
        "verify-s": {"score": 0.92, "feedback": "solid"},
    })
    assert result == ["verify-v", "verify-s"]


def test_not_voted_vote_keeps_label(tmp_path):
    """A reviewer that did not vote still occupies a vote that keeps its label."""
    script = """
META = {"name": "not-voted", "description": "label survives a non-vote", "phases": []}

async def run(args):
    r = await verify([
        Reviewer(kind="verdict", prompt="check", label="v"),
        Reviewer(kind="verdict", prompt="skip me", label="n"),
    ])
    return [(v.label, v.decision, v.score) for v in r.votes]
"""
    result = _run(tmp_path, "not_voted.py", script, {
        "verify-v": {"decision": "pass", "feedback": ""},
        "verify-n": SKIP,
    })
    assert result[0] == ("verify-v", True, None)
    assert result[1] == ("verify-n", None, None)  # SKIP fixture: no vote, label stays


def test_script_reads_all_vote_fields(tmp_path):
    """A script reading every documented vote field completes the run."""
    script = """
META = {"name": "vote-fields", "description": "read all vote fields", "phases": []}

async def run(args):
    r = await verify([
        Reviewer(kind="verdict", prompt="check", label="v", role="verifier"),
        Reviewer(kind="score", prompt="score", label="s", role="inspector"),
        Reviewer(kind="verdict", prompt="skip", label="n"),
    ])
    return [
        {
            "label": v.label, "role": v.role, "kind": v.kind,
            "decision": v.decision, "score": v.score, "feedback": v.feedback,
        }
        for v in r.votes
    ]
"""
    result = _run(tmp_path, "fields.py", script, {
        "verify-v": {"decision": "pass", "feedback": "ok"},
        "verify-s": {"score": 0.9, "feedback": "solid"},
        "verify-n": SKIP,
    })
    by_label = {v["label"]: v for v in result}
    assert by_label["verify-v"]["role"] == "verifier"
    assert by_label["verify-v"]["decision"] is True
    assert by_label["verify-s"]["score"] == 0.9
    assert by_label["verify-n"]["decision"] is None and by_label["verify-n"]["score"] is None
