# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime state and gate for restricted Skill evolution review."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.harness.rails.evolution.review.result_schema import (
    EvolutionReviewResult,
    normalize_review_result,
)

ScopeStatus = Literal[
    "review_required",
    "review_completed",
    "submitted",
    "no_evolution",
    "expired",
    "cancelled",
    "failed",
]


@dataclass(frozen=True)
class EvolutionReviewLaunch:
    scope_id: str
    evolution_review_ref: str
    subject: dict[str, Any]


@dataclass
class EvolutionReviewScope:
    scope_id: str
    source: str
    subject: dict[str, Any]
    session_id: str
    evolution_review_ref: str
    user_intent: str = ""
    scoped_materials: dict[str, Any] = field(default_factory=dict)
    status: ScopeStatus = "review_required"
    proposal_ids: set[str] = field(default_factory=set)
    proposal_drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_trace: set[str] = field(default_factory=set)
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc) + timedelta(minutes=30))
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class EvolutionProposalDetail:
    proposal_id: str
    summary: str
    content: str
    target: str
    section: str
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvolutionProposalSelection:
    evolution_review_ref: str
    subject: dict[str, Any]
    selected_proposal_ids: tuple[str, ...]
    proposals: tuple[EvolutionProposalDetail, ...]
    experience_drafts: tuple[dict[str, Any], ...]
    record_source: str | None = None


class EvolutionReviewRuntime:
    """Holds evolution review scope state outside SkillEvolutionRail."""

    def __init__(
        self,
        *,
        submitted_scope_retention_secs: float = 60.0,
    ) -> None:
        self._scopes_by_ref: dict[str, EvolutionReviewScope] = {}
        self._submitted_scope_retention = timedelta(seconds=max(0.0, submitted_scope_retention_secs))

    def prune_scopes(self) -> None:
        """Remove expired scopes and submitted scopes past the replay window."""
        now = datetime.now(tz=timezone.utc)
        submitted_cutoff = now - self._submitted_scope_retention
        refs_to_delete = []
        for ref, scope in self._scopes_by_ref.items():
            if scope.status == "expired" or scope.expires_at <= now:
                refs_to_delete.append(ref)
                continue
            if scope.status == "submitted" and scope.consumed_at is not None and scope.consumed_at <= submitted_cutoff:
                refs_to_delete.append(ref)
        for ref in refs_to_delete:
            self._scopes_by_ref.pop(ref, None)

    def create_scope(
        self,
        *,
        source: str,
        subject: dict[str, Any],
        session_id: str,
        user_intent: str = "",
        scoped_materials: dict[str, Any] | None = None,
    ) -> EvolutionReviewLaunch:
        self.prune_scopes()
        normalized_subject = self._normalize_subject(subject).to_payload()
        scope_id = f"evrs_{uuid.uuid4().hex[:12]}"
        ref = f"evrr_{scope_id}_{uuid.uuid4().hex[:8]}"
        scope = EvolutionReviewScope(
            scope_id=scope_id,
            source=source,
            subject=normalized_subject,
            session_id=session_id,
            evolution_review_ref=ref,
            user_intent=user_intent,
            scoped_materials=dict(scoped_materials or {}),
        )
        self._scopes_by_ref[ref] = scope
        return EvolutionReviewLaunch(
            scope_id=scope_id,
            evolution_review_ref=ref,
            subject=dict(normalized_subject),
        )

    def resolve_scope(self, evolution_review_ref: str, *, session_id: str) -> EvolutionReviewScope:
        self.prune_scopes()
        scope = self._scopes_by_ref[evolution_review_ref]
        if scope.session_id == "" and session_id:
            scope.session_id = session_id
        if not self._session_matches_scope(scope.session_id, session_id):
            raise KeyError(evolution_review_ref)
        if scope.expires_at <= datetime.now(tz=timezone.utc):
            scope.status = "expired"
            self._scopes_by_ref.pop(evolution_review_ref, None)
            raise KeyError(evolution_review_ref)
        return scope

    @staticmethod
    def _session_matches_scope(scope_session_id: str, session_id: str) -> bool:
        if scope_session_id == session_id:
            return True
        prefix = f"{scope_session_id}_sub_evolution_reviewer_"
        session_text = str(session_id)
        if not scope_session_id or not session_text.startswith(prefix):
            return False
        suffix = session_text.removeprefix(prefix)
        return len(suffix) == 8 and all(char in "0123456789abcdef" for char in suffix)

    def has_scope(self, evolution_review_ref: str) -> bool:
        self.prune_scopes()
        return evolution_review_ref in self._scopes_by_ref

    def record_evidence_read(self, evolution_review_ref: str, *, session_id: str, refs: list[str]) -> None:
        scope = self.resolve_scope(evolution_review_ref, session_id=session_id)
        scope.read_trace.update(str(ref) for ref in refs)

    def record_trajectory_read(self, evolution_review_ref: str, *, session_id: str, refs: list[str]) -> None:
        self.record_evidence_read(evolution_review_ref, session_id=session_id, refs=refs)

    def record_review_result(
        self,
        evolution_review_ref: str,
        *,
        session_id: str,
        result: dict[str, Any],
    ) -> EvolutionReviewScope:
        scope = self.resolve_scope(evolution_review_ref, session_id=session_id)
        normalized: EvolutionReviewResult = normalize_review_result(dict(result), scope_subject=scope.subject)

        missing_refs = set(normalized.evidence_refs) - scope.read_trace
        if missing_refs:
            raise ValueError(f"review result references unread evidence refs: {sorted(missing_refs)}")

        proposal_drafts: dict[str, dict[str, Any]] = {}
        proposal_ids: set[str] = set()
        if normalized.outcome == "recommend_evolve":
            for proposal in normalized.proposals:
                proposal_drafts[proposal.proposal_id] = proposal.experience
                proposal_ids.add(proposal.proposal_id)

        scope.result = {
            "subject": normalized.subject.to_payload(),
            "outcome": normalized.outcome,
            "evidence_refs": list(normalized.evidence_refs),
            "proposals": [
                {
                    "proposal_id": p.proposal_id,
                    "experience": p.experience,
                    "reason": p.reason,
                    "evidence_refs": list(p.evidence_refs),
                }
                for p in normalized.proposals
            ],
            "summary": normalized.summary,
        }
        scope.proposal_ids = proposal_ids
        scope.proposal_drafts = proposal_drafts
        scope.status = "review_completed" if normalized.outcome == "recommend_evolve" else "no_evolution"
        return scope

    def resolve_selected_proposals(
        self,
        evolution_review_ref: str,
        *,
        subject: dict[str, Any],
        selected_proposal_ids: list[str],
        session_id: str,
    ) -> EvolutionProposalSelection:
        if not evolution_review_ref:
            raise ValueError("evolution_review_ref is required")
        scope = self.resolve_scope(evolution_review_ref, session_id=session_id)
        normalized_subject = self._normalize_subject(subject).to_payload()
        if normalized_subject != scope.subject:
            raise ValueError("evolution review subject mismatch")
        if scope.status == "submitted":
            raise ValueError("evolution review ref already submitted")
        if scope.status == "no_evolution":
            raise ValueError("evolution review has no evolution proposals")
        if scope.status != "review_completed":
            raise ValueError("evolution review is not completed")
        ids = [str(item).strip() for item in selected_proposal_ids or [] if str(item).strip()]
        if not ids:
            raise ValueError("selected_proposal_ids are required")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate proposal id")

        proposal_by_id = {
            str(proposal.get("proposal_id")): proposal
            for proposal in (scope.result or {}).get("proposals", []) or []
            if isinstance(proposal, dict)
        }
        resolved_proposals: list[EvolutionProposalDetail] = []
        drafts: list[dict[str, Any]] = []
        for proposal_id in ids:
            draft = scope.proposal_drafts.get(proposal_id)
            proposal = proposal_by_id.get(proposal_id)
            if draft is None or proposal is None:
                raise ValueError(f"unknown proposal_id: {proposal_id}")
            reason = str(proposal.get("reason", "") or draft.get("reason", "") or "")
            evidence_refs = tuple(str(ref) for ref in proposal.get("evidence_refs", []) or [])
            resolved_proposals.append(
                EvolutionProposalDetail(
                    proposal_id=proposal_id,
                    summary=str(draft.get("summary", "") or ""),
                    content=str(draft.get("content", "") or ""),
                    target=str(draft.get("target", "") or ""),
                    section=str(draft.get("section", "") or ""),
                    reason=reason,
                    evidence_refs=evidence_refs,
                )
            )
            draft_for_execution = dict(draft)
            if reason:
                draft_for_execution["reason"] = reason
            drafts.append(draft_for_execution)

        return EvolutionProposalSelection(
            evolution_review_ref=evolution_review_ref,
            subject=dict(scope.subject),
            selected_proposal_ids=tuple(ids),
            proposals=tuple(resolved_proposals),
            experience_drafts=tuple(drafts),
            record_source="user_intent" if scope.user_intent.strip() else "agent_inferred",
        )

    @staticmethod
    def _normalize_subject(subject: Any):
        try:
            return normalize_subject(subject)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    def consume_prepared_submission(
        self,
        prepared: EvolutionProposalSelection,
        *,
        session_id: str,
    ) -> None:
        """Mark the scope as submitted without re-resolving proposals."""
        self.prune_scopes()
        scope = self.resolve_scope(prepared.evolution_review_ref, session_id=session_id)
        scope.status = "submitted"
        scope.consumed_at = datetime.now(tz=timezone.utc)


__all__ = [
    "EvolutionReviewLaunch",
    "EvolutionReviewRuntime",
    "EvolutionReviewScope",
    "EvolutionProposalDetail",
    "EvolutionProposalSelection",
]
