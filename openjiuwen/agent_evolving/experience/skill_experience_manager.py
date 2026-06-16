# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill experience lifecycle orchestration."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionRecord,
)
from openjiuwen.agent_evolving.experience.common import (
    commit_pending_change,
    execute_simplify_actions,
    make_pending_change,
    reject_pending_change,
)
from openjiuwen.agent_evolving.experience.draft_schema import normalize_evolution_subject_kind
from openjiuwen.agent_evolving.experience.lifecycle import LocalApplyPreview, PendingCommitResult
from openjiuwen.agent_evolving.experience.query import ExperienceQueryService
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.agent_evolving.experience.scorer import ExperienceScorer
from openjiuwen.agent_evolving.experience.submission import ExperienceSubmissionService
from openjiuwen.agent_evolving.experience.types import (
    ExperienceApplyResult,
    ExperienceApprovalRequest,
    ExperienceProposal,
    PendingChange,
)
from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    APPROVE_ACTION,
    EXPERIENCES_TARGET,
    LOCAL_APPLY_COMPLETED,
    PENDING_CHANGE_EFFECT,
    REJECT_ACTION,
    RETRY_ACTION,
    SKILL_EXPERIENCE_ENTRY,
)
from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue
from openjiuwen.agent_evolving.update_execution import execute_updates
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.operator.skill_call import SkillExperienceOperator


class ExperienceManager:
    """Orchestrates the online lifecycle for skill and team-skill evolution.

    New code that only queries experiences or applies agent-submitted drafts
    should depend on ExperienceQueryService or ExperienceSubmissionService
    directly. Keep this manager for online approval and governance lifecycle
    orchestration that owns pending state.
    """

    def __init__(
        self,
        *,
        store: EvolutionStore,
        scorer: ExperienceScorer,
        language: str = "cn",
        skill_ops: Optional[Dict[str, SkillExperienceOperator]] = None,
        pending_approval_snapshots: Optional[Dict[str, PendingChange]] = None,
        pending_governance: Optional[Dict[str, Dict[str, Any]]] = None,
        query_service: ExperienceQueryService | None = None,
        submission_service: ExperienceSubmissionService | None = None,
        rebuild_service: ExperienceRebuildService | None = None,
        subject_kind: str | None = None,
    ) -> None:
        self._store = store
        self._scorer = scorer
        self._language = language
        self._subject_kind = normalize_evolution_subject_kind(subject_kind) if subject_kind else None
        self._skill_ops = skill_ops if skill_ops is not None else {}
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        self._pending_governance = pending_governance if pending_governance is not None else {}
        self.bind_pending_approval_snapshots(pending_approval_snapshots)
        self._query_service = self._coerce_service(
            service=query_service,
            service_name="query_service",
            default_factory=lambda: ExperienceQueryService(store=self._store),
        )
        self._submission_service = self._coerce_service(
            service=submission_service,
            service_name="submission_service",
            default_factory=lambda: ExperienceSubmissionService(
                store=self._store,
                pending_approval_snapshots=self._pending_approval_snapshots,
            ),
        )
        self._rebuild_service = self._coerce_service(
            service=rebuild_service,
            service_name="rebuild_service",
            default_factory=lambda: ExperienceRebuildService(store=self._store),
        )
        self.bind_pending_approval_snapshots(self._pending_approval_snapshots)

    @property
    def pending_approval_snapshots(self) -> Dict[str, PendingChange]:
        return self._pending_approval_snapshots

    @property
    def pending_governance(self) -> Dict[str, Dict[str, Any]]:
        return self._pending_governance

    @property
    def skill_ops(self) -> Dict[str, SkillExperienceOperator]:
        return self._skill_ops

    @property
    def experience_query_service(self) -> ExperienceQueryService:
        return self._query_service

    @property
    def experience_submission_service(self) -> ExperienceSubmissionService:
        return self._submission_service

    @property
    def rebuild_service(self) -> ExperienceRebuildService:
        return self._rebuild_service

    def bind_pending_approval_snapshots(
        self,
        pending_approval_snapshots: Optional[Dict[str, PendingChange]],
    ) -> None:
        """Bind the caller-owned pending snapshot store."""
        self._pending_approval_snapshots = pending_approval_snapshots if pending_approval_snapshots is not None else {}
        service = getattr(self, "_submission_service", None)
        bind = getattr(service, "bind_pending_approval_snapshots", None)
        if callable(bind):
            bind(self._pending_approval_snapshots)

    @staticmethod
    def _coerce_service(
        service: Any,
        *,
        service_name: str,
        default_factory,
    ) -> Any:
        if service is None:
            return default_factory()
        if isinstance(service, dict):
            raise TypeError(f"{service_name} must be a shared service instance, not a per-kind mapping")
        return service

    async def list_skill_experiences(
        self,
        subject: dict[str, Any],
        *,
        min_score: Optional[float] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
        target: Optional[str] = None,
        section: Optional[str] = None,
        query: Optional[str] = None,
        sort: str = "score_desc",
    ) -> dict[str, Any]:
        """Return bounded structured experience metadata for agent reasoning."""
        return await self._query_service.list_experiences(
            subject,
            min_score=min_score,
            limit=limit,
            cursor=cursor,
            target=target,
            section=section,
            query=query,
            sort=sort,
        )

    async def read_agent_experiences(
        self,
        subject: dict[str, Any],
        *,
        record_ids: list[str],
        max_content_chars: int = 2000,
    ) -> dict[str, Any]:
        """Read selected full experience records for agent reasoning."""
        return await self._query_service.read_experiences(
            subject,
            record_ids=record_ids,
            max_content_chars=max_content_chars,
        )

    async def apply_experience_drafts(
        self,
        subject: dict[str, Any],
        drafts: list[dict[str, Any]],
        *,
        source: str = "agent_evolve_tool",
    ) -> dict[str, Any]:
        """Persist approved agent-submitted experience drafts."""
        return await self._submission_service.apply_experience_drafts(subject, drafts, source=source)

    async def apply_simplify_drafts(
        self,
        subject: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute approved agent-submitted simplify actions."""
        return await self._submission_service.apply_simplify_actions(subject, actions)

    def stage_records(
        self,
        skill_name: str,
        records: list[EvolutionRecord],
        *,
        requires_approval: bool = True,
        source: str = "experience_optimizer",
        user_query: str = "",
        signal_type: Optional[str] = None,
        signal_source: Optional[str] = None,
        change_type: str = SKILL_EXPERIENCE_ENTRY,
        request_id_prefix: Optional[str] = None,
        trajectory: Any | None = None,
        messages: Optional[List[dict]] = None,
        is_shared_records: bool = False,
    ) -> ExperienceApprovalRequest:
        """Snapshot one record batch into approval state."""
        proposal = ExperienceProposal(
            skill_name=skill_name,
            records=records,
            requires_approval=requires_approval,
            source=source,
            user_query=user_query,
            signal_type=signal_type,
            signal_source=signal_source,
        )
        return self._stage_records(
            proposal=proposal,
            records=records,
            change_type=change_type,
            request_id_prefix=request_id_prefix,
            trajectory=trajectory,
            messages=messages,
            is_shared_records=is_shared_records,
        )

    def stage_apply_results(
        self,
        skill_name: str,
        apply_results: list[ApplyResult],
        *,
        requires_approval: bool = True,
        source: str = "experience_updater",
        request_id_prefix: Optional[str] = None,
        user_query: str = "",
        signal_type: Optional[str] = None,
        signal_source: Optional[str] = None,
        messages: Optional[List[dict]] = None,
    ) -> ExperienceApprovalRequest:
        """Stage already-generated online apply results through the shared lifecycle."""
        preview = self.build_local_apply_preview(skill_name, apply_results)

        proposal = ExperienceProposal(
            skill_name=skill_name,
            records=preview.records,
            requires_approval=requires_approval,
            source=source,
            user_query=user_query,
            signal_type=signal_type,
            signal_source=signal_source,
        )
        return self._stage_pending_request(
            proposal=proposal,
            preview=preview,
            request_id_prefix=request_id_prefix,
            messages=messages,
        )

    def _stage_records(
        self,
        *,
        proposal: ExperienceProposal,
        records: list[EvolutionRecord],
        change_type: str,
        request_id_prefix: Optional[str] = None,
        trajectory: Any | None = None,
        messages: Optional[List[dict]] = None,
        is_shared_records: bool = False,
    ) -> ExperienceApprovalRequest:
        """Shared stage flow for skill/team-skill pending approval batches."""
        operator = self._skill_ops.get(proposal.skill_name)
        if operator is None:
            from openjiuwen.core.operator.skill_call import SkillExperienceOperator

            operator = SkillExperienceOperator(proposal.skill_name)

        apply_results = self._preview_apply_results(
            proposal.skill_name,
            operator,
            UpdateValue(
                payload=records,
                mode=APPEND_MODE,
                effect=PENDING_CHANGE_EFFECT,
                change_type=change_type,
            ),
        )
        return self._stage_pending_request(
            proposal=proposal,
            preview=self.build_local_apply_preview(proposal.skill_name, apply_results),
            request_id_prefix=request_id_prefix,
            trajectory=trajectory,
            messages=messages,
            is_shared_records=is_shared_records,
        )

    def _stage_pending_request(
        self,
        *,
        proposal: ExperienceProposal,
        preview: LocalApplyPreview,
        request_id_prefix: Optional[str] = None,
        trajectory: Any | None = None,
        messages: Optional[List[dict]] = None,
        is_shared_records: bool = False,
    ) -> ExperienceApprovalRequest:
        """Stage one pending request from previewed apply results."""
        pending = self._make_pending_change_from_preview(
            preview,
            request_id_prefix=request_id_prefix,
            trajectory=trajectory,
            messages=messages,
            is_shared_records=is_shared_records,
        )
        staged_pending = self._stage_pending_change(pending)
        logger.info(
            "[ExperienceManager] staged approval request %s with %d record(s) for skill=%s",
            staged_pending.change_id,
            len(staged_pending.payload),
            proposal.skill_name,
        )
        return ExperienceApprovalRequest(
            skill_name=proposal.skill_name,
            proposal=proposal,
            pending_change=staged_pending,
            request_id=staged_pending.change_id,
            apply_results=preview.apply_results,
        )

    async def approve_request(
        self,
        request_id: str,
        *,
        approved_record_ids: Optional[List[str]] = None,
    ) -> ExperienceApplyResult:
        """Apply a staged approval batch to persistent storage."""
        return await self._apply_request(
            request_id,
            action=APPROVE_ACTION,
            approved_record_ids=approved_record_ids,
        )

    async def reject_request(self, request_id: str) -> ExperienceApplyResult:
        """Reject and discard a staged approval batch."""
        return await self._apply_request(request_id, action=REJECT_ACTION)

    async def retry_request(self, request_id: str) -> ExperienceApplyResult:
        """Retry a partially-applied staged approval batch."""
        return await self._apply_request(request_id, action=RETRY_ACTION)

    async def _apply_request(
        self,
        request_id: str,
        *,
        action: Literal["approve", "reject", "retry"],
        approved_record_ids: Optional[List[str]] = None,
    ) -> ExperienceApplyResult:
        """Shared request lifecycle for approve / reject / retry."""
        pending = self._pending_approval_snapshots.get(request_id)
        if not pending:
            logger.warning("[ExperienceManager] %s_request: unknown request_id=%s", action, request_id)
            return ExperienceApplyResult(skill_name="", errors=[f"unknown request_id: {request_id}"])

        if action == "reject":
            self._reject_pending_change(request_id)
            return reject_pending_change(pending)

        commit_result = await self._commit_pending_change(
            request_id,
            approved_record_ids=approved_record_ids if action == APPROVE_ACTION else None,
        )
        return self._to_apply_result(pending.skill_name, commit_result)

    async def commit_proposal(self, proposal: ExperienceProposal) -> ExperienceApplyResult:
        """Persist a generated proposal through the shared pending lifecycle."""
        request = self.stage_records(
            proposal.skill_name,
            proposal.records,
            requires_approval=proposal.requires_approval,
            source=proposal.source,
            user_query=proposal.user_query,
            signal_type=proposal.signal_type,
            signal_source=proposal.signal_source,
        )
        return await self._commit_staged_request(request)

    async def _commit_staged_request(self, request: ExperienceApprovalRequest) -> ExperienceApplyResult:
        """Commit one staged request through the shared pending lifecycle."""
        if not request.request_id:
            raise ValueError("staged request missing request_id")

        result = await self.approve_request(request.request_id)
        if not result.ok:
            raise RuntimeError(
                "auto-commit failed for "
                f"skill={request.skill_name}, request_id={request.request_id}, "
                f"applied={result.applied_count}, pending={result.pending_count}, "
                f"errors={result.errors}"
            )
        return result

    def _preview_apply_results(
        self,
        skill_name: str,
        operator: SkillExperienceOperator,
        update: UpdateValue,
    ) -> list[ApplyResult]:
        """Preview online apply results without entering pending or persistence."""
        return self.apply_updates(
            {operator.operator_id: operator},
            {(operator.operator_id, EXPERIENCES_TARGET): update},
        )

    @staticmethod
    def apply_updates(
        operators: Dict[str, SkillExperienceOperator],
        updates: Dict[tuple[str, str], UpdateValue | Any],
    ) -> list[ApplyResult]:
        """Compatibility hook for executing online preview updates."""
        return execute_updates(operators, updates)

    def _make_pending_change_from_preview(
        self,
        preview: LocalApplyPreview,
        *,
        request_id_prefix: Optional[str] = None,
        trajectory: Any | None = None,
        messages: Optional[List[dict]] = None,
        is_shared_records: bool = False,
    ) -> PendingChange:
        pending = make_pending_change(
            preview.skill_name,
            preview.records,
            request_id_prefix=request_id_prefix,
            subject_kind=self._subject_kind,
            trajectory=trajectory,
            messages=messages,
            is_shared_records=is_shared_records,
        )
        pending.change_type = preview.change_type
        return pending

    @staticmethod
    def build_local_apply_preview(
        skill_name: str,
        apply_results: list[ApplyResult],
    ) -> LocalApplyPreview:
        """Build the stable local-apply preview contract from apply results."""
        records: list[EvolutionRecord] = []
        change_type = SKILL_EXPERIENCE_ENTRY

        for result in apply_results:
            if not result.applied:
                continue
            if result.lifecycle_stage and result.lifecycle_stage != LOCAL_APPLY_COMPLETED:
                raise ValueError(f"unsupported apply lifecycle stage for {skill_name}: {result.lifecycle_stage}")
            records.extend(result.records)
            if result.change_type is not None:
                change_type = str(result.change_type)

        return LocalApplyPreview(
            skill_name=skill_name,
            records=records,
            apply_results=list(apply_results),
            change_type=change_type,
        )

    _build_local_apply_preview = build_local_apply_preview

    def _stage_pending_change(self, pending: PendingChange) -> PendingChange:
        """Register one staged pending change in the caller-owned snapshot store."""
        self._pending_approval_snapshots[pending.change_id] = pending
        return pending

    def _reject_pending_change(self, change_id: str) -> PendingChange:
        """Remove one staged pending change without persisting it."""
        pending = self._pending_approval_snapshots.pop(change_id, None)
        if pending is None:
            raise KeyError(change_id)
        return pending

    async def _commit_pending_change(
        self,
        change_id: str,
        *,
        approved_record_ids: Optional[List[str]] = None,
    ) -> PendingCommitResult:
        """Persist a staged pending change, retaining the unwritten tail on partial failure."""
        return await commit_pending_change(
            self._pending_approval_snapshots,
            change_id,
            store=self._store,
            approved_record_ids=approved_record_ids,
        )

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
    ) -> Optional[str]:
        """Stage simplify governance for a skill."""
        started_at = time.monotonic()
        subject_kind = self._subject_kind
        if not self._store.skill_exists(skill_name, subject_kind=subject_kind):
            logger.info(
                "[ExperienceManager] request_simplify skipped: skill=%s reason=skill_not_found",
                skill_name,
            )
            return None
        if not self._store.skill_definition_exists(skill_name):
            logger.info(
                "[ExperienceManager] request_simplify skipped: skill=%s reason=skill_definition_not_found",
                skill_name,
            )
            return None

        evo_log = await self._store.load_full_evolution_log(skill_name, subject_kind=subject_kind)
        records = evo_log.entries
        if not records:
            logger.info(
                "[ExperienceManager] request_simplify skipped: skill=%s reason=no_records",
                skill_name,
            )
            return None

        content = await self._store.read_skill_content(skill_name, strict=True)
        summary = self._store.extract_description_from_skill_md(content)
        logger.info(
            "[ExperienceManager] request_simplify loaded records: skill=%s records=%d",
            skill_name,
            len(records),
        )
        actions = await self._scorer.simplify(
            skill_name=skill_name,
            skill_summary=summary,
            records=records,
            user_intent=user_intent,
        )
        if not actions:
            logger.info(
                "[ExperienceManager] request_simplify finished without actions: skill=%s elapsed=%.1fs",
                skill_name,
                time.monotonic() - started_at,
            )
            return None

        request_id = f"evolve_simplify_{uuid.uuid4().hex[:8]}"
        self._pending_governance[request_id] = {
            "kind": "simplify",
            "skill_name": skill_name,
            "subject_kind": subject_kind,
            "actions": actions,
        }
        logger.info(
            "[ExperienceManager] request_simplify staged: skill=%s request=%s actions=%d elapsed=%.1fs",
            skill_name,
            request_id,
            len(actions),
            time.monotonic() - started_at,
        )
        return request_id

    async def approve_simplify(self, request_id: str) -> Dict[str, int]:
        """Execute staged simplify governance."""
        gov = self._pending_governance.pop(request_id, None)
        if not gov:
            return {}
        return await execute_simplify_actions(
            store=self._store,
            skill_name=gov["skill_name"],
            actions=gov["actions"],
            subject_kind=gov.get("subject_kind"),
        )

    async def reject_simplify(self, request_id: str) -> None:
        """Discard staged simplify governance."""
        self._pending_governance.pop(request_id, None)

    async def request_rebuild(
        self,
        skill_name: str,
        *,
        subject: dict[str, Any] | None = None,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
        max_context_records: int = 40,
        max_context_chars: int = 20000,
    ) -> Optional[str]:
        """Prepare a rebuild prompt for a skill."""
        default_kind = self._subject_kind or "skill"
        resolved_subject: dict[str, Any] = (
            subject if subject is not None else {"kind": default_kind, "name": skill_name}
        )
        context = await self._rebuild_service.prepare_rebuild_context(
            resolved_subject,
            user_intent=user_intent,
            min_score=min_score,
            max_context_records=max_context_records,
            max_context_chars=max_context_chars,
        )
        if context is None:
            return None
        from openjiuwen.harness.rails.evolution.commands import build_rebuild_command_prompt

        return build_rebuild_command_prompt(
            subject=context["subject"],
            user_intent=user_intent,
            rebuild_context=context,
            language=self._language,
        )

    @staticmethod
    def _to_apply_result(skill_name: str, result: PendingCommitResult) -> ExperienceApplyResult:
        return ExperienceApplyResult(
            skill_name=skill_name,
            applied_count=result.applied_count,
            rejected_count=result.rejected_count,
            pending_count=result.pending_count,
            errors=list(result.errors),
        )

    @staticmethod
    def format_evolution_records(
        records: List[EvolutionRecord],
        *,
        language: str = "cn",
    ) -> str:
        if language == "en":
            header = "Experience"
            content_label = "Content"
            empty = "(no evolution records)"
        else:
            header = "经验"
            content_label = "内容"
            empty = "（无演进经验）"

        lines: List[str] = []
        for idx, record in enumerate(records, 1):
            change = record.change
            section = getattr(change, "section", "?")
            content = getattr(change, "content", "")
            source = getattr(record, "source", "unknown")
            timestamp = getattr(record, "timestamp", "")
            score = getattr(record, "score", 0.0)
            lines.append(
                f"### {header} #{idx} [{timestamp}] - source: {source}, score: {score:.2f}\n"
                f"- Section: {section}\n"
                f"- {content_label}: {content}"
            )
        return "\n\n".join(lines) if lines else empty
