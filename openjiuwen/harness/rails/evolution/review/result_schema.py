# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Review result schema normalization for agent-driven skill evolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from openjiuwen.agent_evolving.experience.draft_schema import (
    EvolutionSubject,
    MAX_EVOLUTION_REVIEW_PROPOSALS,
    normalize_evolve_draft,
    normalize_subject,
)


@dataclass(frozen=True)
class EvolutionReviewProposal:
    """A single proposal from the review agent."""

    proposal_id: str
    experience: dict[str, Any]
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvolutionReviewResult:
    """Structured review result from the review agent."""

    subject: EvolutionSubject
    outcome: Literal["recommend_evolve", "no_evolution"]
    evidence_refs: tuple[str, ...]
    proposals: tuple[EvolutionReviewProposal, ...]
    summary: str = ""


def normalize_review_result(
    raw: dict[str, Any],
    *,
    scope_subject: dict[str, Any],
) -> EvolutionReviewResult:
    """Normalize a raw review result dict into a structured EvolutionReviewResult.

    Validates subject match, outcome, and proposal structure.
    Does NOT check scope.read_trace — that is the runtime's responsibility.
    """
    raw = dict(raw)
    normalized_subject = normalize_subject(raw.get("subject"))
    if normalized_subject.to_payload() != scope_subject:
        raise ValueError("review result subject mismatch")

    evidence_refs = _collect_evidence_refs(raw)
    outcome = raw.get("outcome")
    if outcome not in {"recommend_evolve", "no_evolution"}:
        raise ValueError(f"invalid review outcome: {outcome}")

    summary = str(raw.get("summary", "") or "")

    proposals: tuple[EvolutionReviewProposal, ...] = ()
    if outcome == "recommend_evolve":
        raw_proposals = raw.get("proposals", []) or []
        if not raw_proposals:
            raise ValueError("recommend_evolve requires proposals")
        proposals = normalize_review_proposals(normalized_subject, raw_proposals)
    else:
        raw_proposals = raw.get("proposals", []) or []
        if raw_proposals:
            raise ValueError("no_evolution must not include proposals")

    return EvolutionReviewResult(
        subject=normalized_subject,
        outcome=outcome,
        evidence_refs=tuple(sorted(evidence_refs)),
        proposals=proposals,
        summary=summary,
    )


def normalize_review_proposals(
    subject: EvolutionSubject,
    proposals: list[dict[str, Any]],
) -> tuple[EvolutionReviewProposal, ...]:
    """Normalize and validate review proposals.

    Validates proposal_id uniqueness, experience presence, and runs
    experience drafts through normalize_evolve_draft.
    """
    if not isinstance(proposals, list):
        raise ValueError("proposals must be a list")
    if len(proposals) > MAX_EVOLUTION_REVIEW_PROPOSALS:
        raise ValueError(f"proposals must contain at most {MAX_EVOLUTION_REVIEW_PROPOSALS} items")

    proposal_ids: set[str] = set()
    draft_items: list[dict[str, Any]] = []
    ordered_ids: list[str] = []
    reasons: dict[str, str] = {}
    evidence: dict[str, tuple[str, ...]] = {}

    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise ValueError(f"proposal at index {index} must be an object")
        proposal_id = str(proposal.get("proposal_id", "") or "").strip()
        if not proposal_id:
            raise ValueError("proposal_id is required")
        if proposal_id in proposal_ids:
            raise ValueError(f"duplicate proposal_id: {proposal_id}")
        proposal_ids.add(proposal_id)
        experience = proposal.get("experience")
        if not isinstance(experience, dict):
            raise ValueError("proposal experience is required")
        ordered_ids.append(proposal_id)
        draft_items.append(experience)
        reasons[proposal_id] = str(proposal.get("reason", "") or "")
        evidence[proposal_id] = tuple(str(ref) for ref in proposal.get("evidence_refs", []) or [])

    try:
        draft = normalize_evolve_draft(subject, draft_items)
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    result: list[EvolutionReviewProposal] = []
    for i, proposal_id in enumerate(ordered_ids):
        result.append(
            EvolutionReviewProposal(
                proposal_id=proposal_id,
                experience=draft.experiences[i],
                reason=reasons[proposal_id],
                evidence_refs=evidence[proposal_id],
            )
        )
    return tuple(result)


def _collect_evidence_refs(result: dict[str, Any]) -> set[str]:
    """Collect all evidence refs from top-level and per-proposal."""
    refs = {str(ref) for ref in result.get("evidence_refs", [])}
    for proposal in result.get("proposals", []) or []:
        if isinstance(proposal, dict):
            refs.update(str(ref) for ref in proposal.get("evidence_refs", []) or [])
    return refs


__all__ = [
    "EvolutionReviewProposal",
    "EvolutionReviewResult",
    "MAX_EVOLUTION_REVIEW_PROPOSALS",
    "normalize_review_proposals",
    "normalize_review_result",
]
