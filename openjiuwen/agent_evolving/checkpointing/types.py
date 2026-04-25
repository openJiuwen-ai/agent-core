# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Checkpointing and evolution record types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.signal.base import EvolutionSignal, EvolutionTarget


# Valid sections for skill evolution
VALID_SECTIONS = {"Instructions", "Examples", "Troubleshooting", "Scripts"}


@dataclass
class UsageStats:
    """Usage tracking for an evolution experience."""

    times_presented: int = 0
    times_used: int = 0
    times_positive: int = 0
    times_negative: int = 0
    last_presented_at: Optional[str] = None
    last_evaluated_at: Optional[str] = None

    def to_dict(self) -> dict:
        payload: dict = {
            "times_presented": self.times_presented,
            "times_used": self.times_used,
            "times_positive": self.times_positive,
            "times_negative": self.times_negative,
        }
        if self.last_presented_at:
            payload["last_presented_at"] = self.last_presented_at
        if self.last_evaluated_at:
            payload["last_evaluated_at"] = self.last_evaluated_at
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "UsageStats":
        return cls(
            times_presented=data.get("times_presented", 0),
            times_used=data.get("times_used", 0),
            times_positive=data.get("times_positive", 0),
            times_negative=data.get("times_negative", 0),
            last_presented_at=data.get("last_presented_at"),
            last_evaluated_at=data.get("last_evaluated_at"),
        )


@dataclass
class EvolutionPatch:
    """One generated evolution change."""

    section: str
    action: str
    content: str
    target: EvolutionTarget = EvolutionTarget.BODY
    skip_reason: Optional[str] = None
    merge_target: Optional[str] = None
    script_filename: Optional[str] = None
    script_language: Optional[str] = None
    script_purpose: Optional[str] = None

    _OPTIONAL_FIELDS = ("skip_reason", "merge_target", "script_filename", "script_language", "script_purpose")

    def to_dict(self) -> dict:
        payload = {
            "section": self.section,
            "action": self.action,
            "content": self.content,
            "target": self.target.value,
        }
        for key in self._OPTIONAL_FIELDS:
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionPatch":
        raw_target = data.get("target", "body")
        try:
            target = EvolutionTarget(raw_target)
        except ValueError:
            target = EvolutionTarget.BODY
        return cls(
            section=data.get("section", "Troubleshooting"),
            action=data.get("action", "append"),
            content=data.get("content", ""),
            target=target,
            skip_reason=data.get("skip_reason"),
            merge_target=data.get("merge_target"),
            script_filename=data.get("script_filename"),
            script_language=data.get("script_language"),
            script_purpose=data.get("script_purpose"),
        )


@dataclass
class EvolutionRecord:
    """One stored evolution record."""

    id: str
    source: str
    timestamp: str
    context: str
    change: EvolutionPatch
    applied: bool = False
    score: float = 0.6
    usage_stats: Optional[UsageStats] = None
    skill_version: Optional[str] = None

    @classmethod
    def make(
        cls,
        source: str,
        context: str,
        change: EvolutionPatch,
        score: float = 0.6,
        skill_version: Optional[str] = None,
    ) -> "EvolutionRecord":
        return cls(
            id=f"ev_{uuid.uuid4().hex[:8]}",
            source=source,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            context=context,
            change=change,
            score=score,
            usage_stats=UsageStats(),
            skill_version=skill_version,
        )

    def to_dict(self) -> dict:
        payload = {
            "id": self.id,
            "source": self.source,
            "timestamp": self.timestamp,
            "context": self.context,
            "change": self.change.to_dict(),
            "applied": self.applied,
            "score": self.score,
        }
        if self.usage_stats is not None:
            payload["usage_stats"] = self.usage_stats.to_dict()
        if self.skill_version is not None:
            payload["skill_version"] = self.skill_version
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionRecord":
        usage_stats_data = data.get("usage_stats")
        usage_stats = UsageStats.from_dict(usage_stats_data) if usage_stats_data else UsageStats()
        return cls(
            id=data.get("id", f"ev_{uuid.uuid4().hex[:8]}"),
            source=data.get("source", "unknown"),
            timestamp=data.get("timestamp", ""),
            context=data.get("context", ""),
            change=EvolutionPatch.from_dict(data.get("change", {})),
            applied=data.get("applied", False),
            score=data.get("score", 0.6),
            usage_stats=usage_stats,
            skill_version=data.get("skill_version"),
        )

    @property
    def is_pending(self) -> bool:
        return not self.applied


@dataclass
class EvolutionLog:
    """Persisted container of evolution entries for one skill."""

    skill_id: str
    version: str = "1.0.0"
    updated_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    entries: List[EvolutionRecord] = field(default_factory=list)

    @property
    def pending_entries(self) -> List[EvolutionRecord]:
        return [entry for entry in self.entries if entry.is_pending]

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "updated_at": self.updated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionLog":
        return cls(
            skill_id=data.get("skill_id", ""),
            version=data.get("version", "1.0.0"),
            updated_at=data.get("updated_at", ""),
            entries=[EvolutionRecord.from_dict(item) for item in data.get("entries", [])],
        )

    @classmethod
    def empty(cls, skill_id: str) -> "EvolutionLog":
        return cls(skill_id=skill_id)


@dataclass
class EvolveCheckpoint:
    """Training checkpoint (for resume)."""

    version: str
    run_id: str
    step: Dict[str, int]
    best: Dict[str, Any]
    seed: Optional[int]
    operators_state: Dict[str, Dict[str, Any]]
    updater_state: Dict[str, Any]
    searcher_state: Dict[str, Any]
    last_metrics: Dict[str, Any]


@dataclass
class PendingChange:
    """Snapshot of staged evolution records awaiting user approval."""

    operator_id: str
    skill_name: str
    change_type: str
    payload: List[EvolutionRecord]
    created_at: str
    change_id: str = field(default_factory=lambda: f"skill_evolve_{uuid.uuid4().hex[:8]}")

    @classmethod
    def make(cls, skill_name: str, records: List[EvolutionRecord]) -> "PendingChange":
        return cls(
            operator_id=f"skill_call_{skill_name}",
            skill_name=skill_name,
            change_type="experience_entry",
            payload=list(records),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )


@dataclass
class PendingSkillCreation:
    """Snapshot of a new skill proposal awaiting user approval."""

    name: str
    description: str
    body: str
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    proposal_id: str = field(default_factory=lambda: f"skill_create_{uuid.uuid4().hex[:8]}")


@dataclass
class PendingTeamSkillCreation(PendingSkillCreation):
    """Snapshot of a team skill proposal awaiting user approval.

    Extends PendingSkillCreation with extra_files for multi-file team skill
    packages (roles/*.md, workflow.md, bind.md, etc.).
    """

    extra_files: Dict[str, str] = field(default_factory=dict)
    frontmatter: str = ""
    proposal_id: str = field(default_factory=lambda: f"team_skill_create_{uuid.uuid4().hex[:8]}")


@dataclass
class EvolutionContext:
    """All inputs required for LLM-based experience generation."""

    skill_name: str
    signals: List[EvolutionSignal]
    skill_content: str
    messages: List[dict]
    existing_desc_records: List[EvolutionRecord]
    existing_body_records: List[EvolutionRecord]
    user_query: str = ""


__all__ = [
    "VALID_SECTIONS",
    "UsageStats",
    "EvolutionPatch",
    "EvolutionRecord",
    "EvolutionLog",
    "EvolveCheckpoint",
    "PendingChange",
    "PendingSkillCreation",
    "PendingTeamSkillCreation",
    "EvolutionContext",
]
