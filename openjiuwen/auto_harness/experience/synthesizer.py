# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""活跃上下文合成 — 将近期经验浓缩为 prompt 注入片段。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import List

from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
)
from openjiuwen.auto_harness.schema import (
    Experience,
    ExperienceType,
)

logger = logging.getLogger(__name__)

_1D = 86400
_7D = 7 * _1D

_WEIGHT_RECENT = 1.0
_WEIGHT_MEDIUM = 0.5
_WEIGHT_OLD = 0.2

_CHARS_PER_TOKEN = 3.3

_SECTION_HEADERS = {
    ExperienceType.OPTIMIZATION: "### 近期优化经验",
    ExperienceType.FAILURE: "### 失败教训",
    ExperienceType.INSIGHT: "### 关键洞察",
}


class ActiveContextSynthesizer:
    """Synthesize recent experiences into an active-context string."""

    def __init__(self, experience_dir: str) -> None:
        self._store = ExperienceStore(experience_dir)

    async def synthesize(
        self,
        experiences: List[Experience],
        max_tokens: int = 2000,
    ) -> str:
        """Build a markdown summary grouped by experience type."""
        if not experiences:
            return ""

        now = time.time()
        grouped: dict[ExperienceType, list[tuple[float, Experience]]] = (
            defaultdict(list)
        )
        for exp in experiences:
            weight = _time_weight(exp.timestamp, now)
            grouped[exp.type].append((weight, exp))

        for items in grouped.values():
            items.sort(key=lambda t: t[0], reverse=True)

        max_chars = int(max_tokens * _CHARS_PER_TOKEN)
        parts: list[str] = []
        budget = max_chars

        for experience_type in (
            ExperienceType.OPTIMIZATION,
            ExperienceType.FAILURE,
            ExperienceType.INSIGHT,
        ):
            items = grouped.get(experience_type)
            if not items:
                continue
            header = _SECTION_HEADERS[experience_type]
            section_lines = [header]
            for _weight, exp in items:
                section_lines.append(_format_line(exp))
            section = "\n".join(section_lines)
            if len(section) > budget:
                parts.append(section[:budget])
                break
            parts.append(section)
            budget -= len(section) + 1

        return "\n\n".join(parts)

    async def load_and_synthesize(
        self,
        top_k: int = 30,
    ) -> str:
        """Convenience: load recent experiences then synthesize."""
        experiences = await self._store.list_recent(limit=top_k)
        return await self.synthesize(experiences)


def _time_weight(ts: float, now: float) -> float:
    """Return decay weight based on age brackets."""
    age = now - ts
    if age <= _1D:
        return _WEIGHT_RECENT
    if age <= _7D:
        return _WEIGHT_MEDIUM
    return _WEIGHT_OLD


def _format_line(exp: Experience) -> str:
    """Format a single experience as a bullet point."""
    if exp.outcome:
        return f"- {exp.topic}: {exp.summary} ({exp.outcome})"
    return f"- {exp.topic}: {exp.summary}"
