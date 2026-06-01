# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit intermediate types for online experience lifecycle orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.lifecycle import HostFacingExperienceResult
from openjiuwen.agent_evolving.protocols import SKILL_EXPERIENCE_ENTRY
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.types import ApplyResult


@dataclass
class EvolutionContext:
    """Canonical online/offline inputs required for experience generation."""

    skill_name: str
    signals: List[EvolutionSignal]
    skill_content: str
    messages: List[dict]
    existing_desc_records: List[EvolutionRecord]
    existing_body_records: List[EvolutionRecord]
    user_query: str = ""
    trajectory: Any | None = None
    existing_script_records: List[EvolutionRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


OnlineEvolutionContext = EvolutionContext


@dataclass
class PendingChange:
    """Snapshot of staged evolution records awaiting user approval."""

    operator_id: str
    skill_name: str
    change_type: str
    payload: List[EvolutionRecord]
    created_at: str
    change_id: str = field(default_factory=lambda: f"skill_evolve_{uuid.uuid4().hex[:8]}")
    is_shared_records: bool = False
    trajectory: Any | None = None
    messages: List[dict] | None = None

    @classmethod
    def make(
        cls,
        skill_name: str,
        records: List[EvolutionRecord],
        *,
        trajectory: Any | None = None,
        messages: List[dict] | None = None,
    ) -> "PendingChange":
        return cls(
            operator_id=f"skill_experience_{skill_name}",
            skill_name=skill_name,
            change_type=SKILL_EXPERIENCE_ENTRY,
            payload=list(records),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            trajectory=trajectory,
            messages=list(messages) if messages is not None else None,
        )

    @classmethod
    def make_for_shared_records(
        cls,
        skill_name: str,
        records: List[EvolutionRecord],
        *,
        trajectory: Any | None = None,
        messages: List[dict] | None = None,
    ) -> "PendingChange":
        pending = cls.make(skill_name, records, trajectory=trajectory, messages=messages)
        pending.is_shared_records = True
        return pending


@dataclass
class ExperienceProposal:
    """Generated experience proposal before persistence or approval."""

    skill_name: str
    records: List[EvolutionRecord]
    requires_approval: bool
    source: str = "experience_optimizer"
    user_query: str = ""
    signal_type: Optional[str] = None
    signal_source: Optional[str] = None

    @property
    def record_count(self) -> int:
        return len(self.records)


@dataclass
class ExperienceApprovalRequest:
    """Approval-facing view over a staged experience proposal."""

    skill_name: str
    proposal: ExperienceProposal
    pending_change: Optional[PendingChange] = None
    request_id: Optional[str] = None
    apply_results: List[ApplyResult] = field(default_factory=list)

    def to_host_result(self) -> HostFacingExperienceResult:
        """Return the stable host-facing shape for a staged request."""
        pending_count = len(self.pending_change.payload) if self.pending_change is not None else 0
        change_type = self.pending_change.change_type if self.pending_change is not None else SKILL_EXPERIENCE_ENTRY
        return HostFacingExperienceResult.pending_approval(
            skill_name=self.skill_name,
            request_id=self.request_id or "",
            change_type=change_type,
            pending_count=pending_count,
        )


OnlineEvolutionStatus = Literal[
    "staged",
    "auto_approved",
    "no_evolution_no_records",
    "skipped_no_input",
    "skipped_skill_not_found",
    "skipped_skill_definition_not_found",
    "persistence_failed",
    "generation_failed",
]

ONLINE_EVOLUTION_OUTCOME_STATUSES = frozenset(
    {
        "no_evolution_no_records",
        "generation_failed",
        "persistence_failed",
        "skipped_skill_definition_not_found",
    }
)


@dataclass
class OnlineEvolutionResult:
    """Structured outcome returned by the online evolution orchestrator."""

    skill_name: str
    status: OnlineEvolutionStatus
    request: Optional[ExperienceApprovalRequest] = None
    message: str = ""


def request_for_online_evolution_result(
    result: OnlineEvolutionResult,
) -> Optional[ExperienceApprovalRequest]:
    """Return the staged request that callers should expose for an online outcome."""
    if result.status in ONLINE_EVOLUTION_OUTCOME_STATUSES and result.status != "persistence_failed":
        return None
    return result.request


@dataclass
class ExperienceApplyResult:
    """Result of applying staged experience changes."""

    skill_name: str
    applied_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and self.pending_count == 0

    def to_host_result(
        self,
        *,
        request_id: Optional[str] = None,
        change_type: str = SKILL_EXPERIENCE_ENTRY,
    ) -> HostFacingExperienceResult:
        """Return the stable host-facing shape for an apply/reject result."""
        pure_rejection = (
            self.rejected_count > 0 and self.applied_count == 0 and self.pending_count == 0 and not self.errors
        )
        if pure_rejection:
            return HostFacingExperienceResult.rejected(
                skill_name=self.skill_name,
                request_id=request_id,
                change_type=change_type,
                rejected_count=self.rejected_count,
            )
        return HostFacingExperienceResult.persisted(
            skill_name=self.skill_name,
            request_id=request_id,
            change_type=change_type,
            applied_count=self.applied_count,
            rejected_count=self.rejected_count,
            pending_count=self.pending_count,
            errors=self.errors,
        )
