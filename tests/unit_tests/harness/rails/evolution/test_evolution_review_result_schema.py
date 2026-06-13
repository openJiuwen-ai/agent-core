# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for review result schema normalization."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.experience.draft_schema import EvolutionSubject
from openjiuwen.harness.rails.evolution.review.result_schema import (
    EvolutionReviewProposal,
    EvolutionReviewResult,
    normalize_review_proposals,
    normalize_review_result,
)


def _proposal(proposal_id: str, *, content: str = "fix", reason: str = "") -> dict:
    return {
        "proposal_id": proposal_id,
        "experience": {
            "summary": f"summary {proposal_id}",
            "content": content,
            "target": "body",
            "section": "Troubleshooting",
        },
        "reason": reason,
        "evidence_refs": [],
    }


def _scope_subject(kind: str = "skill", name: str = "test-skill") -> dict:
    return {"kind": kind, "name": name}


class TestNormalizeReviewResult:
    def test_recommend_evolve_with_proposals(self):
        result = normalize_review_result(
            {
                "subject": {"kind": "skill", "name": "test-skill"},
                "outcome": "recommend_evolve",
                "evidence_refs": ["ref-1"],
                "proposals": [_proposal("p1", content="add logging")],
            },
            scope_subject=_scope_subject(),
        )
        assert result.outcome == "recommend_evolve"
        assert len(result.proposals) == 1
        assert result.proposals[0].proposal_id == "p1"
        assert result.proposals[0].experience["content"] == "add logging"

    def test_no_evolution_without_proposals(self):
        result = normalize_review_result(
            {
                "subject": {"kind": "skill", "name": "test-skill"},
                "outcome": "no_evolution",
                "evidence_refs": [],
            },
            scope_subject=_scope_subject(),
        )
        assert result.outcome == "no_evolution"
        assert len(result.proposals) == 0

    def test_subject_mismatch(self):
        with pytest.raises(ValueError, match="subject mismatch"):
            normalize_review_result(
                {
                    "subject": {"kind": "skill", "name": "other-skill"},
                    "outcome": "recommend_evolve",
                    "evidence_refs": [],
                    "proposals": [_proposal("p1")],
                },
                scope_subject=_scope_subject(name="test-skill"),
            )

    def test_team_skill_alias_normalized(self):
        result = normalize_review_result(
            {
                "subject": {"kind": "team-skill", "name": "team-a"},
                "outcome": "no_evolution",
                "evidence_refs": [],
            },
            scope_subject=_scope_subject(kind="swarm-skill", name="team-a"),
        )
        assert result.subject.kind == "swarm-skill"
        assert result.subject.name == "team-a"

    def test_duplicate_proposal_id(self):
        with pytest.raises(ValueError, match="duplicate proposal_id"):
            normalize_review_result(
                {
                    "subject": _scope_subject(),
                    "outcome": "recommend_evolve",
                    "evidence_refs": [],
                    "proposals": [_proposal("p1"), _proposal("p1")],
                },
                scope_subject=_scope_subject(),
            )

    def test_recommend_evolve_requires_proposals(self):
        with pytest.raises(ValueError, match="recommend_evolve requires proposals"):
            normalize_review_result(
                {
                    "subject": _scope_subject(),
                    "outcome": "recommend_evolve",
                    "evidence_refs": [],
                    "proposals": [],
                },
                scope_subject=_scope_subject(),
            )

    def test_no_evolution_rejects_proposals(self):
        with pytest.raises(ValueError, match="no_evolution must not include proposals"):
            normalize_review_result(
                {
                    "subject": _scope_subject(),
                    "outcome": "no_evolution",
                    "evidence_refs": [],
                    "proposals": [_proposal("p1")],
                },
                scope_subject=_scope_subject(),
            )

    def test_invalid_outcome(self):
        with pytest.raises(ValueError, match="invalid review outcome"):
            normalize_review_result(
                {
                    "subject": _scope_subject(),
                    "outcome": "unknown",
                    "evidence_refs": [],
                },
                scope_subject=_scope_subject(),
            )

    def test_collects_evidence_refs(self):
        result = normalize_review_result(
            {
                "subject": _scope_subject(),
                "outcome": "recommend_evolve",
                "evidence_refs": ["top-ref"],
                "proposals": [
                    {
                        **_proposal("p1"),
                        "evidence_refs": ["prop-ref-1", "prop-ref-2"],
                    }
                ],
            },
            scope_subject=_scope_subject(),
        )
        assert "top-ref" in result.evidence_refs
        assert "prop-ref-1" in result.evidence_refs
        assert "prop-ref-2" in result.evidence_refs

    def test_returns_frozen_dataclass(self):
        result = normalize_review_result(
            {
                "subject": _scope_subject(),
                "outcome": "recommend_evolve",
                "evidence_refs": [],
                "proposals": [_proposal("p1")],
            },
            scope_subject=_scope_subject(),
        )
        assert isinstance(result, EvolutionReviewResult)
        assert isinstance(result.proposals, tuple)
        assert isinstance(result.proposals[0], EvolutionReviewProposal)


class TestNormalizeReviewProposals:
    def test_valid_proposals(self):
        subject = EvolutionSubject(kind="skill", name="test-skill")
        proposals = normalize_review_proposals(
            subject,
            [_proposal("p1", content="fix 1"), _proposal("p2", content="fix 2")],
        )
        assert len(proposals) == 2
        assert proposals[0].proposal_id == "p1"
        assert proposals[0].experience["content"] == "fix 1"
        assert proposals[1].proposal_id == "p2"

    def test_rejects_more_than_max_proposals(self):
        subject = EvolutionSubject(kind="skill", name="test-skill")

        with pytest.raises(ValueError, match="proposals must contain at most 3 items"):
            normalize_review_proposals(
                subject,
                [_proposal("p1"), _proposal("p2"), _proposal("p3"), _proposal("p4")],
            )

    def test_missing_proposal_id(self):
        with pytest.raises(ValueError, match="proposal_id is required"):
            normalize_review_proposals(
                EvolutionSubject(kind="skill", name="test-skill"),
                [{"experience": {"summary": "s", "content": "c", "target": "body", "section": "Troubleshooting"}}],
            )

    def test_missing_experience(self):
        with pytest.raises(ValueError, match="proposal experience is required"):
            normalize_review_proposals(
                EvolutionSubject(kind="skill", name="test-skill"),
                [{"proposal_id": "p1"}],
            )

    def test_not_a_list(self):
        with pytest.raises(ValueError, match="proposals must be a list"):
            normalize_review_proposals(
                EvolutionSubject(kind="skill", name="test-skill"),
                "not-a-list",
            )

    def test_not_a_dict_item(self):
        with pytest.raises(ValueError, match="proposal at index 0 must be an object"):
            normalize_review_proposals(
                EvolutionSubject(kind="skill", name="test-skill"),
                ["not-a-dict"],
            )

    def test_preserves_reason(self):
        subject = EvolutionSubject(kind="skill", name="test-skill")
        proposals = normalize_review_proposals(
            subject,
            [_proposal("p1", reason="good fix")],
        )
        assert proposals[0].reason == "good fix"

    def test_preserves_evidence_refs(self):
        subject = EvolutionSubject(kind="skill", name="test-skill")
        proposal_data = _proposal("p1")
        proposal_data["evidence_refs"] = ["ref-a", "ref-b"]
        proposals = normalize_review_proposals(subject, [proposal_data])
        assert proposals[0].evidence_refs == ("ref-a", "ref-b")
