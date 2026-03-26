# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data model definitions for online skill evolution system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

VALID_SECTIONS = {"Instructions", "Examples", "Troubleshooting"}


class EvolutionCategory(str, Enum):
    """Evolution type that determines which handler processes the signal."""

    SKILL_EXPERIENCE = "skill_experience"
    NEW_SKILL = "new_skill"


class EvolutionTarget(str, Enum):
    """Which layer of the skill the experience targets."""

    DESCRIPTION = "description"
    BODY = "body"


@dataclass
class EvolutionPatch:
    """One generated evolution change."""

    section: str
    action: str
    content: str
    target: EvolutionTarget = EvolutionTarget.BODY
    skip_reason: Optional[str] = None
    merge_target: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "section": self.section,
            "action": self.action,
            "content": self.content,
            "target": self.target.value,
        }
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        if self.merge_target:
            payload["merge_target"] = self.merge_target
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

    @classmethod
    def make(
        cls,
        source: str,
        context: str,
        change: EvolutionPatch,
    ) -> "EvolutionRecord":
        return cls(
            id=f"ev_{uuid.uuid4().hex[:8]}",
            source=source,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            context=context,
            change=change,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "timestamp": self.timestamp,
            "context": self.context,
            "change": self.change.to_dict(),
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionRecord":
        return cls(
            id=data.get("id", f"ev_{uuid.uuid4().hex[:8]}"),
            source=data.get("source", "unknown"),
            timestamp=data.get("timestamp", ""),
            context=data.get("context", ""),
            change=EvolutionPatch.from_dict(data.get("change", {})),
            applied=data.get("applied", False),
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
class EvolutionContext:
    """All inputs required for LLM-based experience generation."""

    skill_name: str
    signals: List["EvolutionSignal"]
    skill_content: str
    messages: List[dict]
    existing_desc_records: List[EvolutionRecord]
    existing_body_records: List[EvolutionRecord]


@dataclass
class EvolutionSignal:
    """Detected evolution signal from dialogue/tool trace."""

    signal_type: str
    evolution_type: EvolutionCategory
    section: str
    excerpt: str
    tool_name: Optional[str] = None
    skill_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": self.signal_type,
            "evolution_type": self.evolution_type.value,
            "section": self.section,
            "excerpt": self.excerpt,
            "tool_name": self.tool_name,
            "skill_name": self.skill_name,
        }
