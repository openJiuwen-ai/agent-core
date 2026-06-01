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
    request_rebuild_context,
)
from openjiuwen.agent_evolving.experience.lifecycle import LocalApplyPreview, PendingCommitResult, RebuildRequest
from openjiuwen.agent_evolving.experience.scorer import ExperienceScorer
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
    """Orchestrates the online lifecycle for skill and team-skill evolution."""

    _SUPPORTED_KINDS = {"skill", "team-skill"}
    _REBUILD_PROMPT_TEMPLATES = {
        "skill": {
            "cn": (
                "你收到了一个技能的重建请求。旧版本已归档，请执行以下步骤：\n\n"
                "## 已筛选的历史演进经验（score >= {min_score}）\n\n"
                "{evolution_records}\n\n"
                "## 用户意图\n\n"
                "{user_intent}\n\n"
                "## 执行要求\n\n"
                "请调用 skill-creator 技能：\n"
                "1. 基于以上历史经验和用户意图，生成新的 SKILL.md\n"
                "2. 重置 evolutions.json 为空列表\n\n"
                "旧版本已归档至 archive/ 目录，可直接创建新版本。"
            ),
            "en": (
                "You received a skill rebuild request. Old version has been archived. Please follow these steps:\n\n"
                "## Filtered Historical Evolution Records (score >= {min_score})\n\n"
                "{evolution_records}\n\n"
                "## User Intent\n\n"
                "{user_intent}\n\n"
                "## Execution Requirements\n\n"
                "Please invoke the skill-creator skill:\n"
                "1. Generate new SKILL.md based on the historical records and user intent above\n"
                "2. Reset evolutions.json to empty list\n\n"
                "Old version has been archived to archive/ directory, you can directly create the new version."
            ),
        },
        "team-skill": {
            "cn": (
                "你收到了一个团队技能的重建请求。旧版本已归档，请执行以下步骤：\n\n"
                "## 已筛选的历史演进经验（score >= {min_score}）\n\n"
                "{evolution_records}\n\n"
                "## 用户意图\n\n"
                "{user_intent}\n\n"
                "## 执行要求\n\n"
                "请调用 teamskill-creator 技能：\n"
                "1. 基于以上历史经验和用户意图，生成新的 SKILL.md\n"
                "2. 重置 evolutions.json 为空列表\n\n"
                "旧版本已归档至 archive/ 目录，可直接创建新版本。"
            ),
            "en": (
                "You received a team skill rebuild request. Old version has been archived. "
                "Please follow these steps:\n\n"
                "## Filtered Historical Evolution Records (score >= {min_score})\n\n"
                "{evolution_records}\n\n"
                "## User Intent\n\n"
                "{user_intent}\n\n"
                "## Execution Requirements\n\n"
                "Please invoke the teamskill-creator skill:\n"
                "1. Generate new SKILL.md based on the historical records and user intent above\n"
                "2. Reset evolutions.json to empty list\n\n"
                "Old version has been archived to archive/ directory, you can directly create the new version."
            ),
        },
    }
    _DEFAULT_REBUILD_INTENTS = {
        "skill": {
            "cn": "根据以上演进经验，对技能进行全面优化和重建。",
            "en": "Based on the evolution records above, perform a comprehensive rebuild of the skill.",
        },
        "team-skill": {
            "cn": "根据以上演进经验，对团队技能进行全面优化和重建。",
            "en": "Based on the evolution records above, perform a comprehensive rebuild of the team skill.",
        },
    }

    def __init__(
        self,
        *,
        store: EvolutionStore,
        scorer: ExperienceScorer,
        kind: str = "skill",
        language: str = "cn",
        skill_ops: Optional[Dict[str, SkillExperienceOperator]] = None,
        pending_approval_snapshots: Optional[Dict[str, PendingChange]] = None,
        pending_governance: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        if kind not in self._SUPPORTED_KINDS:
            raise ValueError(f"unsupported experience manager kind: {kind}")
        self._store = store
        self._scorer = scorer
        self._kind = kind
        self._language = language
        self._skill_ops = skill_ops if skill_ops is not None else {}
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        self._pending_governance = pending_governance if pending_governance is not None else {}
        self.bind_pending_approval_snapshots(pending_approval_snapshots)

    @property
    def pending_approval_snapshots(self) -> Dict[str, PendingChange]:
        return self._pending_approval_snapshots

    @property
    def pending_governance(self) -> Dict[str, Dict[str, Any]]:
        return self._pending_governance

    @property
    def skill_ops(self) -> Dict[str, SkillExperienceOperator]:
        return self._skill_ops

    def bind_pending_approval_snapshots(
        self,
        pending_approval_snapshots: Optional[Dict[str, PendingChange]],
    ) -> None:
        """Bind the caller-owned pending snapshot store."""
        self._pending_approval_snapshots = pending_approval_snapshots if pending_approval_snapshots is not None else {}

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

    @staticmethod
    def _make_pending_change_from_preview(
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
        if not self._store.skill_exists(skill_name):
            logger.info(
                "[ExperienceManager] request_simplify skipped: kind=%s skill=%s reason=skill_not_found",
                self._kind,
                skill_name,
            )
            return None
        if not self._store.skill_definition_exists(skill_name):
            logger.info(
                "[ExperienceManager] request_simplify skipped: kind=%s skill=%s reason=skill_definition_not_found",
                self._kind,
                skill_name,
            )
            return None

        evo_log = await self._store.load_full_evolution_log(skill_name)
        records = evo_log.entries
        if not records:
            logger.info(
                "[ExperienceManager] request_simplify skipped: kind=%s skill=%s reason=no_records",
                self._kind,
                skill_name,
            )
            return None

        content = await self._store.read_skill_content(skill_name, strict=True)
        summary = self._store.extract_description_from_skill_md(content)
        logger.info(
            "[ExperienceManager] request_simplify loaded records: kind=%s skill=%s records=%d",
            self._kind,
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
                "[ExperienceManager] request_simplify finished without actions: kind=%s skill=%s elapsed=%.1fs",
                self._kind,
                skill_name,
                time.monotonic() - started_at,
            )
            return None

        request_id = f"evolve_simplify_{uuid.uuid4().hex[:8]}"
        self._pending_governance[request_id] = {
            "kind": "simplify",
            "skill_name": skill_name,
            "actions": actions,
        }
        logger.info(
            "[ExperienceManager] request_simplify staged: kind=%s skill=%s request=%s actions=%d elapsed=%.1fs",
            self._kind,
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
        )

    async def reject_simplify(self, request_id: str) -> None:
        """Discard staged simplify governance."""
        self._pending_governance.pop(request_id, None)

    def _get_rebuild_template(self) -> str:
        return self._REBUILD_PROMPT_TEMPLATES[self._kind].get(
            self._language,
            self._REBUILD_PROMPT_TEMPLATES[self._kind]["en"],
        )

    def _get_default_rebuild_intent(self) -> str:
        return self._DEFAULT_REBUILD_INTENTS[self._kind].get(
            self._language,
            self._DEFAULT_REBUILD_INTENTS[self._kind]["en"],
        )

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
    ) -> Optional[str]:
        """Prepare a rebuild prompt for a skill."""
        context = await request_rebuild_context(
            self._store,
            RebuildRequest(skill_name=skill_name, user_intent=user_intent, min_score=min_score),
            format_records=lambda records: self.format_evolution_records(records, language=self._language),
            default_intent=self._get_default_rebuild_intent(),
            template=self._get_rebuild_template(),
        )
        if context is None:
            return None
        return str(context["prompt"])

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
