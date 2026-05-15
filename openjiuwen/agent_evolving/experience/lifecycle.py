# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal lifecycle values for experience orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.protocols import (
    LOCAL_APPLY_COMPLETED,
    PENDING_CHANGE_EFFECT,
    SKILL_EXPERIENCE_ENTRY,
    STATE_EFFECT,
)
from openjiuwen.agent_evolving.types import ApplyResult


@dataclass(frozen=True)
class LocalApplyPreview:
    """Stable preview contract for results produced by local update apply."""

    skill_name: str
    records: List[EvolutionRecord]
    apply_results: List[ApplyResult]
    change_type: str = SKILL_EXPERIENCE_ENTRY
    lifecycle_stage: Literal["local_apply_completed"] = LOCAL_APPLY_COMPLETED


@dataclass(frozen=True)
class PendingCommitResult:
    """Outcome of committing a staged pending change."""

    applied_count: int
    pending_count: int


@dataclass(frozen=True)
class HostFacingExperienceResult:
    """Stable result contract visible to hosts after staging or applying updates."""

    skill_name: str
    request_id: Optional[str]
    effect: str
    change_type: str
    applied_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    status: str = "pending_approval"
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def pending_approval(
        cls,
        *,
        skill_name: str,
        request_id: str,
        change_type: str = SKILL_EXPERIENCE_ENTRY,
        pending_count: int,
    ) -> "HostFacingExperienceResult":
        return cls(
            skill_name=skill_name,
            request_id=request_id,
            effect=PENDING_CHANGE_EFFECT,
            change_type=change_type,
            pending_count=pending_count,
            status="pending_approval",
        )

    @classmethod
    def persisted(
        cls,
        *,
        skill_name: str,
        request_id: Optional[str],
        change_type: str = SKILL_EXPERIENCE_ENTRY,
        applied_count: int,
        pending_count: int = 0,
        errors: Optional[List[str]] = None,
    ) -> "HostFacingExperienceResult":
        status = "persisted" if pending_count == 0 and not errors else "partial"
        return cls(
            skill_name=skill_name,
            request_id=request_id,
            effect=STATE_EFFECT,
            change_type=change_type,
            applied_count=applied_count,
            pending_count=pending_count,
            status=status,
            errors=list(errors or []),
        )

    @classmethod
    def rejected(
        cls,
        *,
        skill_name: str,
        request_id: Optional[str],
        change_type: str = SKILL_EXPERIENCE_ENTRY,
        rejected_count: int,
    ) -> "HostFacingExperienceResult":
        return cls(
            skill_name=skill_name,
            request_id=request_id,
            effect=STATE_EFFECT,
            change_type=change_type,
            rejected_count=rejected_count,
            status="rejected",
        )


@dataclass
class RebuildRequest:
    """Parameters required to prepare a skill rebuild request."""

    skill_name: str
    user_intent: Optional[str] = None
    min_score: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)


__all__ = [
    "LocalApplyPreview",
    "HostFacingExperienceResult",
    "PendingCommitResult",
    "RebuildRequest",
]
