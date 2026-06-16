# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured experience submission persistence service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.experience.common import (
    commit_pending_change,
    execute_simplify_actions,
    make_pending_change,
)
from openjiuwen.agent_evolving.experience.draft_schema import (
    EvolutionSubject,
    EvolveDraft,
    SimplifyDraft,
    normalize_evolve_draft,
    normalize_simplify_draft,
    normalize_subject,
    validate_simplify_record_refs,
)
from openjiuwen.agent_evolving.experience.types import PendingChange


class ExperienceSubmissionService:
    """Apply approved structured experience submissions."""

    def __init__(
        self,
        *,
        store: Any,
        pending_approval_snapshots: Optional[Dict[str, PendingChange]] = None,
    ) -> None:
        self._store = store
        self._pending_approval_snapshots = pending_approval_snapshots if pending_approval_snapshots is not None else {}

    def bind_pending_approval_snapshots(self, pending_approval_snapshots: Optional[Dict[str, PendingChange]]) -> None:
        """Bind the caller-owned pending snapshot store."""
        self._pending_approval_snapshots = pending_approval_snapshots if pending_approval_snapshots is not None else {}

    async def apply_experience_drafts(
        self,
        subject: dict[str, Any],
        drafts: list[dict[str, Any]],
        *,
        source: str = "agent_evolve_tool",
    ) -> dict[str, Any]:
        """Persist approved agent-submitted experience drafts."""
        normalized_subject, draft = self.validate_experience_drafts(subject, drafts)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        records = [
            _build_record_from_evolve_item(item, source=source) for item in draft.persistence_view()["experiences"]
        ]
        pending = make_pending_change(
            skill_name,
            records,
            request_id_prefix="agent_evolve",
            subject_kind=subject_kind,
        )
        self._pending_approval_snapshots[pending.change_id] = pending
        result = await commit_pending_change(
            self._pending_approval_snapshots,
            pending.change_id,
            store=self._store,
        )
        errors = list(getattr(result, "errors", []))
        status = "applied" if result.pending_count == 0 and not errors else "partial"
        return {
            "success": status == "applied",
            "operation": "evolve",
            "status": status,
            "request_id": pending.change_id,
            "retry_request_id": pending.change_id if status == "partial" else None,
            "subject": normalized_subject.to_payload(),
            "applied_count": result.applied_count,
            "pending_count": result.pending_count,
            "record_ids": [record.id for record in records[: result.applied_count]],
            "errors": errors,
        }

    async def apply_simplify_actions(
        self,
        subject: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute approved agent-submitted simplify actions."""
        normalized_subject, draft = await self.validate_simplify_actions(subject, actions)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        counts = await execute_simplify_actions(
            store=self._store,
            skill_name=skill_name,
            actions=draft.persistence_view()["actions"],
            subject_kind=subject_kind,
        )
        applied_count = counts["deleted"] + counts["merged"] + counts["refined"]
        status = "applied" if counts["errors"] == 0 else "partial"
        return {
            "success": status == "applied",
            "operation": "simplify",
            "status": status,
            "subject": normalized_subject.to_payload(),
            "applied_count": applied_count,
            "action_counts": counts,
        }

    def validate_experience_drafts(
        self,
        subject: dict[str, Any],
        drafts: list[dict[str, Any]],
    ) -> tuple[EvolutionSubject, EvolveDraft]:
        """Validate evolve drafts without persisting them."""
        normalized_subject = self._normalize_subject(subject)
        skill_name = normalized_subject.name
        if not self._store.skill_exists(skill_name, subject_kind=normalized_subject.kind):
            raise ValueError(f"skill not found: {skill_name}")
        return normalized_subject, normalize_evolve_draft(normalized_subject, drafts)

    async def validate_simplify_actions(
        self,
        subject: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> tuple[EvolutionSubject, SimplifyDraft]:
        """Validate simplify actions without executing them."""
        normalized_subject = self._normalize_subject(subject)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        if not self._store.skill_exists(skill_name, subject_kind=subject_kind):
            raise ValueError(f"skill not found: {skill_name}")
        evo_log = await self._store.load_full_evolution_log(skill_name, subject_kind=subject_kind)
        existing_record_ids = {record.id for record in evo_log.entries}
        draft = normalize_simplify_draft(normalized_subject, actions)
        validate_simplify_record_refs(draft, existing_record_ids=existing_record_ids)
        return normalized_subject, draft

    @staticmethod
    def _normalize_subject(subject: dict[str, Any]) -> EvolutionSubject:
        return normalize_subject(subject)

    def prepare_evolve_submission(
        self,
        *,
        review_runtime: Any,
        evolution_review_ref: str,
        subject: dict[str, Any],
        selected_proposal_ids: list[str],
        session_id: str,
    ) -> Any:
        """Shared preflight: resolve selected proposals and validate drafts without consuming the ref."""
        resolved = review_runtime.resolve_selected_proposals(
            evolution_review_ref,
            subject=subject,
            selected_proposal_ids=selected_proposal_ids,
            session_id=session_id,
        )
        self.validate_experience_drafts(subject, list(resolved.experience_drafts))
        return resolved

    async def apply_prepared_evolve_submission(
        self,
        prepared: Any,
        *,
        source: str = "agent_evolve_tool",
    ) -> dict[str, Any]:
        """Persist drafts from a prepared submission without re-validating."""
        normalized_subject = self._normalize_subject(prepared.subject)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        record_source = getattr(prepared, "record_source", None) or source
        records = [_build_record_from_evolve_item(item, source=record_source) for item in prepared.experience_drafts]
        pending = make_pending_change(
            skill_name,
            records,
            request_id_prefix="agent_evolve",
            subject_kind=subject_kind,
        )
        self._pending_approval_snapshots[pending.change_id] = pending
        result = await commit_pending_change(
            self._pending_approval_snapshots,
            pending.change_id,
            store=self._store,
        )
        errors = list(getattr(result, "errors", []))
        status = "applied" if result.pending_count == 0 and not errors else "partial"
        return {
            "success": status == "applied",
            "operation": "evolve",
            "status": status,
            "request_id": pending.change_id,
            "retry_request_id": pending.change_id if status == "partial" else None,
            "subject": normalized_subject.to_payload(),
            "applied_count": result.applied_count,
            "pending_count": result.pending_count,
            "record_ids": [record.id for record in records[: result.applied_count]],
            "errors": errors,
        }

    async def prepare_simplify_submission(
        self,
        subject: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> tuple[EvolutionSubject, SimplifyDraft]:
        """Shared preflight: validate simplify actions without executing."""
        return await self.validate_simplify_actions(subject, actions)


def _build_record_from_evolve_item(item: dict[str, Any], *, source: str) -> EvolutionRecord:
    return EvolutionRecord.make(
        source=source,
        context=item["summary"],
        summary=item["summary"],
        change=EvolutionPatch(
            target=item["target"],
            section=item["section"],
            action="append",
            content=item["content"],
            script_filename=item.get("script_filename"),
            script_language=item.get("script_language"),
            script_purpose=item.get("script_purpose"),
        ),
    )


__all__ = [
    "ExperienceSubmissionService",
]
