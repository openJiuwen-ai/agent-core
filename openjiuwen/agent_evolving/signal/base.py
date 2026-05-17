# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution signal types and fingerprint utilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EvolutionCategory(str, Enum):
    """Backward-compatible enum kept only for call-site compatibility."""

    SKILL_EXPERIENCE = "skill_experience"
    NEW_SKILL = "new_skill"


class EvolutionTarget(str, Enum):
    """Which layer of the skill the experience targets."""

    DESCRIPTION = "description"
    BODY = "body"
    SCRIPT = "script"


@dataclass
class EvolutionSignal:
    """Detected evolution signal from dialogue/tool trace.

    Attributes:
        signal_type: Type of signal (e.g., 'execution_failure', 'user_correction', 'low_score').
        section: Target section in SKILL.md (e.g., 'Troubleshooting', 'Examples').
        excerpt: Relevant excerpt from the conversation/trace.
        skill_name: Skill name for skill resolution.
        context: Additional context dict (offline: question/label/answer/reason/score/source/tool).
    """

    signal_type: str
    section: str
    excerpt: str
    skill_name: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = {
            "type": self.signal_type,
            "section": self.section,
            "excerpt": self.excerpt,
            "skill_name": self.skill_name,
        }
        if self.context is not None:
            d["context"] = self.context
        return d


def make_evolution_signal(
    *,
    signal_type: str,
    section: str,
    excerpt: str,
    tool_name: Optional[str] = None,
    skill_name: Optional[str] = None,
    source: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> EvolutionSignal:
    """Create a signal with normalized source metadata."""
    merged_context = dict(context or {})
    if source is not None:
        merged_context.setdefault("source", source)
    if tool_name is not None:
        merged_context.setdefault("tool_name", tool_name)
    return EvolutionSignal(
        signal_type=signal_type,
        section=section,
        excerpt=excerpt,
        skill_name=skill_name,
        context=merged_context or None,
    )


def get_signal_source(signal: EvolutionSignal) -> Optional[str]:
    """Read source metadata with backward-compatible fallback."""
    if not signal.context:
        return None
    source = signal.context.get("source")
    return str(source) if source is not None else None


def make_signal_fingerprint(signal: EvolutionSignal) -> Tuple[str, str, str, str]:
    """Build a dedup fingerprint for an evolution signal.

    Used by signal detection and SkillEvolutionRail to keep fingerprints consistent.

    Args:
        signal: EvolutionSignal to fingerprint.

    Returns:
        Tuple of (signal_type, context.tool_name, skill_name, excerpt[:200]).
    """
    context = signal.context or {}
    tool_name = context.get("tool_name")
    return (
        signal.signal_type,
        str(tool_name) if tool_name is not None else "",
        signal.skill_name or "",
        signal.excerpt[:200],
    )


__all__ = [
    "EvolutionCategory",
    "EvolutionTarget",
    "EvolutionSignal",
    "get_signal_source",
    "make_evolution_signal",
    "make_signal_fingerprint",
]
