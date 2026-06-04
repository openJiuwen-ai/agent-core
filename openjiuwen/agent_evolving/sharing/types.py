# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data structures for the experience sharing module.

These wrappers are only used on the *sharing* path. The local
``EvolutionRecord`` schema is unchanged: any cross-user metadata lives
on the wrapper objects defined here, never inside ``evolutions.json``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_bundle_id() -> str:
    return f"sb_{uuid.uuid4().hex[:10]}"


@dataclass
class SharingMeta:
    """Per-experience sharing-side metadata."""

    skill_name: str
    skill_version: str = ""
    upload_trigger: str = "user_approval"
    upload_at: str = field(default_factory=_now_iso)
    feedback_excerpt: Optional[str] = None
    source_user_id: Optional[str] = None
    confidence: float = 0.7
    origin_bundle_id: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "upload_trigger": self.upload_trigger,
            "upload_at": self.upload_at,
            "confidence": self.confidence,
        }
        if self.feedback_excerpt is not None:
            payload["feedback_excerpt"] = self.feedback_excerpt
        if self.source_user_id is not None:
            payload["source_user_id"] = self.source_user_id
        if self.origin_bundle_id is not None:
            payload["origin_bundle_id"] = self.origin_bundle_id
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "SharingMeta":
        return cls(
            skill_name=data.get("skill_name", ""),
            skill_version=data.get("skill_version", ""),
            upload_trigger=data.get("upload_trigger", "user_approval"),
            upload_at=data.get("upload_at", _now_iso()),
            feedback_excerpt=data.get("feedback_excerpt"),
            source_user_id=data.get("source_user_id"),
            confidence=float(data.get("confidence", 0.7)),
            origin_bundle_id=data.get("origin_bundle_id"),
        )


@dataclass
class SharedExperience:
    """Sharing wrapper around a single ``EvolutionRecord``.

    The underlying ``EvolutionRecord`` schema is untouched; all sharing-only
    metadata (keywords / summary / SharingMeta) live on this wrapper.
    """

    record: EvolutionRecord
    keywords: List[str] = field(default_factory=list)
    summary: str = ""
    sharing_meta: Optional[SharingMeta] = None

    def to_dict(self) -> dict:
        return {
            "record": self.record.to_dict(),
            "keywords": list(self.keywords),
            "summary": self.summary,
            "sharing_meta": self.sharing_meta.to_dict() if self.sharing_meta is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SharedExperience":
        sm_data = data.get("sharing_meta")
        sharing_meta = SharingMeta.from_dict(sm_data) if isinstance(sm_data, dict) else None
        return cls(
            record=EvolutionRecord.from_dict(data.get("record", {})),
            keywords=list(data.get("keywords", []) or []),
            summary=data.get("summary", "") or "",
            sharing_meta=sharing_meta,
        )


@dataclass
class SkillPackageMeta:
    """Hub metadata for the single immutable skill package under one ``skill_id``."""

    skill_id: str
    skill_name: str = ""
    description: str = ""
    uploaded_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "description": self.description,
            "uploaded_at": self.uploaded_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillPackageMeta":
        return cls(
            skill_id=data.get("skill_id", "") or "",
            skill_name=data.get("skill_name", "") or "",
            description=data.get("description", "") or "",
            uploaded_at=data.get("uploaded_at", _now_iso()),
        )


@dataclass
class SkillSearchResult:
    """One row returned by hub keyword search."""

    skill_id: str
    skill_name: str = ""
    description: str = ""
    experience_count: int = 0
    keywords: List[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "description": self.description,
            "experience_count": self.experience_count,
            "keywords": list(self.keywords),
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillSearchResult":
        return cls(
            skill_id=data.get("skill_id", "") or "",
            skill_name=data.get("skill_name", "") or "",
            description=data.get("description", "") or "",
            experience_count=int(data.get("experience_count", 0) or 0),
            keywords=list(data.get("keywords", []) or []),
            score=float(data.get("score", 0.0) or 0.0),
        )


@dataclass
class SharedSkillBundle:
    """Bundle is the smallest unit a backend stores: experiences for one skill."""

    bundle_id: str = field(default_factory=_new_bundle_id)
    skill_id: str = ""
    skill_name: str = ""
    skill_version: str = ""
    keywords_aggregate: List[str] = field(default_factory=list)
    summary_aggregate: str = ""
    experiences: List[SharedExperience] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "keywords_aggregate": list(self.keywords_aggregate),
            "summary_aggregate": self.summary_aggregate,
            "experiences": [exp.to_dict() for exp in self.experiences],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SharedSkillBundle":
        skill_id = data.get("skill_id", "") or ""
        if not skill_id:
            skill_id = data.get("skill_content_hash", "") or ""
        return cls(
            bundle_id=data.get("bundle_id", _new_bundle_id()),
            skill_id=skill_id,
            skill_name=data.get("skill_name", ""),
            skill_version=data.get("skill_version", ""),
            keywords_aggregate=list(data.get("keywords_aggregate", []) or []),
            summary_aggregate=data.get("summary_aggregate", "") or "",
            experiences=[
                SharedExperience.from_dict(item)
                for item in (data.get("experiences", []) or [])
                if isinstance(item, dict)
            ],
            created_at=data.get("created_at", _now_iso()),
        )

    @classmethod
    def make(
        cls,
        skill_name: str,
        experiences: List[SharedExperience],
        *,
        skill_version: str = "",
        summary_aggregate: str = "",
    ) -> "SharedSkillBundle":
        seen: List[str] = []
        for exp in experiences:
            for kw in exp.keywords:
                if kw and kw not in seen:
                    seen.append(kw)
        return cls(
            skill_name=skill_name,
            skill_version=skill_version,
            keywords_aggregate=seen,
            summary_aggregate=summary_aggregate or "; ".join(exp.summary for exp in experiences if exp.summary),
            experiences=list(experiences),
        )


@dataclass
class QueryKeywords:
    """Query-side keyword set used to retrieve relevant bundles."""

    keywords: List[str] = field(default_factory=list)
    intent: str = ""
    raw_excerpt: str = ""

    def to_dict(self) -> dict:
        return {
            "keywords": list(self.keywords),
            "intent": self.intent,
            "raw_excerpt": self.raw_excerpt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueryKeywords":
        return cls(
            keywords=list(data.get("keywords", []) or []),
            intent=data.get("intent", "") or "",
            raw_excerpt=data.get("raw_excerpt", "") or "",
        )


@dataclass
class UploadResult:
    """Outcome of a backend bundle upload."""

    ok: bool
    bundle_id: str = ""
    reason: str = ""
    retryable: bool = False


@dataclass
class StagingResult:
    """Result of ``ShareStager.screen_and_stage()``.

    Contains only the sharing-side outcomes.  Local persistence is the
    caller's responsibility (``SkillEvolutionRail``) — records passed
    into ``screen_and_stage()`` are never lost regardless of QC outcome.
    """

    staged_for_share: List[SharedExperience] = field(default_factory=list)
    dropped_for_share: List[Tuple[EvolutionRecord, str]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "StagingResult":
        return cls(staged_for_share=[], dropped_for_share=[])

    @property
    def has_shareable(self) -> bool:
        return bool(self.staged_for_share)


__all__ = [
    "SharingMeta",
    "SharedExperience",
    "SharedSkillBundle",
    "SkillPackageMeta",
    "SkillSearchResult",
    "QueryKeywords",
    "UploadResult",
    "StagingResult",
]
