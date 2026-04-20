# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution signal types and fingerprint utilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EvolutionCategory(str, Enum):
    """Evolution type that determines which handler processes the signal."""

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
        evolution_type: Category of evolution (SKILL_EXPERIENCE or NEW_SKILL).
        section: Target section in SKILL.md (e.g., 'Troubleshooting', 'Examples').
        excerpt: Relevant excerpt from the conversation/trace.
        tool_name: Tool name if signal originates from tool execution.
        skill_name: Skill name for skill resolution.
        context: Additional context dict (offline: question/label/answer/reason/score).
    """

    signal_type: str
    evolution_type: EvolutionCategory
    section: str
    excerpt: str
    tool_name: Optional[str] = None
    skill_name: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = {
            "type": self.signal_type,
            "evolution_type": self.evolution_type.value,
            "section": self.section,
            "excerpt": self.excerpt,
            "tool_name": self.tool_name,
            "skill_name": self.skill_name,
        }
        if self.context is not None:
            d["context"] = self.context
        return d


def make_signal_fingerprint(signal: EvolutionSignal) -> Tuple[str, str, str, str]:
    """Build a dedup fingerprint for an evolution signal.

    Used by signal detection and SkillEvolutionRail to keep fingerprints consistent.

    Args:
        signal: EvolutionSignal to fingerprint.

    Returns:
        Tuple of (signal_type, tool_name, skill_name, excerpt[:200]).
    """
    return (
        signal.signal_type,
        signal.tool_name or "",
        signal.skill_name or "",
        signal.excerpt[:200],
    )


__all__ = [
    "EvolutionCategory",
    "EvolutionTarget",
    "EvolutionSignal",
    "make_signal_fingerprint",
]