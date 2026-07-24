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


def _coerce_root_cause(data: dict) -> Optional[str]:
    """Load root_cause string; migrate legacy root_causes list if needed."""
    raw = data.get("root_cause")
    if isinstance(raw, str):
        text = " ".join(raw.split())
        return text or None
    if raw is not None and not isinstance(raw, list):
        text = str(raw).strip()
        return text or None

    legacy = data.get("root_causes")
    if not isinstance(legacy, list):
        return None
    parts: List[str] = []
    for item in legacy:
        if isinstance(item, str):
            part = item.strip()
        elif isinstance(item, dict):
            failure_type = str(item.get("failure_type") or "").strip()
            evidence = item.get("evidence")
            if isinstance(evidence, list):
                ev = "；".join(str(e).strip() for e in evidence if str(e).strip())
            elif evidence is None:
                ev = ""
            else:
                ev = str(evidence).strip()
            part = "：".join(p for p in (failure_type, ev) if p)
        else:
            continue
        if part:
            parts.append(part)
    text = "；".join(parts)
    return text or None


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
    keywords: Optional[List[str]] = None
    summary: Optional[str] = None

    _OPTIONAL_FIELDS = (
        "skip_reason",
        "merge_target",
        "script_filename",
        "script_language",
        "script_purpose",
        "keywords",
        "summary",
    )

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
            keywords=data.get("keywords"),
            summary=data.get("summary"),
        )


@dataclass
class EvolutionRecordSpec:
    """Named inputs for creating an ``EvolutionRecord`` via ``make``."""

    source: str
    context: str
    change: EvolutionPatch
    score: float = 0.6
    skill_version: Optional[str] = None
    summary: Optional[str] = None
    root_cause: Optional[str] = None


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
    summary: Optional[str] = None
    # suggest-mode review lifecycle: "suggest" | "auto" | "accepted" | None
    review_status: Optional[str] = None
    # Why this experience was triggered (single sentence for evolutions.json).
    root_cause: Optional[str] = None

    @classmethod
    def make(cls, spec: EvolutionRecordSpec) -> "EvolutionRecord":
        return cls(
            id=f"ev_{uuid.uuid4().hex[:8]}",
            source=spec.source,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            context=spec.context,
            change=spec.change,
            score=spec.score,
            usage_stats=UsageStats(),
            skill_version=spec.skill_version,
            summary=spec.summary,
            root_cause=spec.root_cause,
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
            # Always persist for evolutions.json consumers.
            "summary": self.summary,
            "root_cause": self.root_cause,
        }
        if self.usage_stats is not None:
            payload["usage_stats"] = self.usage_stats.to_dict()
        if self.skill_version is not None:
            payload["skill_version"] = self.skill_version
        if self.review_status:
            payload["review_status"] = self.review_status
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
            summary=data.get("summary"),
            review_status=data.get("review_status"),
            root_cause=_coerce_root_cause(data),
        )

    @property
    def is_pending(self) -> bool:
        return not self.applied


@dataclass
class EvolutionLog:
    """Persisted container of evolution entries for one skill."""

    skill_id: str
    version: str = "1.0.0"
    updated_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    entries: List[EvolutionRecord] = field(default_factory=list)
    # One-line overview of all experiences for this skill (≤100 chars).
    summary: Optional[str] = None

    @property
    def pending_entries(self) -> List[EvolutionRecord]:
        return [entry for entry in self.entries if entry.is_pending]

    def refresh_summary(self, max_chars: int = 100) -> None:
        """Rebuild skill-level summary from all active experience summaries."""
        parts: List[str] = []
        for entry in self.entries:
            if entry.change.skip_reason:
                continue
            text = (
                (entry.summary or "").strip()
                or (entry.change.summary or "").strip()
            )
            if not text and entry.change.content:
                text = entry.change.content.splitlines()[0].strip()
            if text:
                parts.append(" ".join(text.split()))
        if not parts:
            self.summary = None
            return
        joined = "；".join(parts)
        joined = " ".join(joined.split())
        if max_chars > 0 and len(joined) > max_chars:
            joined = joined[:max_chars].rstrip()
        self.summary = joined or None

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionLog":
        raw_summary = data.get("summary")
        summary = None
        if isinstance(raw_summary, str):
            summary = " ".join(raw_summary.split()) or None
        log = cls(
            skill_id=data.get("skill_id", ""),
            version=data.get("version", "1.0.0"),
            updated_at=data.get("updated_at", ""),
            entries=[EvolutionRecord.from_dict(item) for item in data.get("entries", [])],
            summary=summary,
        )
        if log.summary is None and log.entries:
            log.refresh_summary()
        return log

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
    """Snapshot of a new skill proposal awaiting user approval.

    .. deprecated::
        ``body`` is no longer generated by the Rail.  Skill creation now
        uses the **skill-creator** skill in a follow-up invoke, so the body
        is produced by the LLM during that invoke rather than by the Rail.
        The field is kept for backward compatibility but defaults to empty
        string.
    """

    name: str
    description: str
    body: str = ""  # deprecated — body is now generated by skill-creator
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    proposal_id: str = field(default_factory=lambda: f"skill_create_{uuid.uuid4().hex[:8]}")


@dataclass
class EvolutionContext:
    """All inputs required for LLM-based experience generation."""

    skill_name: str
    signals: List[EvolutionSignal]
    skill_content: str
    messages: List[dict]
    existing_desc_records: List[EvolutionRecord]
    existing_body_records: List[EvolutionRecord]
    tool_call_chain: str = ""
    user_query: str = ""


__all__ = [
    "VALID_SECTIONS",
    "UsageStats",
    "EvolutionPatch",
    "EvolutionRecord",
    "EvolutionRecordSpec",
    "EvolutionLog",
    "EvolveCheckpoint",
    "PendingChange",
    "PendingSkillCreation",
    "EvolutionContext",
]