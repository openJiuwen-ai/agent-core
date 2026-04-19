# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JSONL-backed experience archive for auto-harness."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from openjiuwen.auto_harness.schema import (
    Experience,
    ExperienceType,
)

logger = logging.getLogger(__name__)

_DEDUP_WINDOW_SECS = 86400  # 24 h


class ExperienceStore:
    """JSONL-backed experience archive with keyword search."""

    def __init__(self, experience_dir: str) -> None:
        self._dir = Path(experience_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "experiences.jsonl"

    async def record(
        self,
        experience: Experience,
    ) -> str:
        """Persist *experience* after admission check."""
        if self._is_duplicate(experience):
            logger.debug(
                "Experience rejected (dup): type=%s topic=%s",
                experience.type,
                experience.topic,
            )
            return ""
        self._append(experience)
        logger.info(
            "Experience recorded: id=%s type=%s topic=%s",
            experience.id,
            experience.type,
            experience.topic,
        )
        return experience.id

    async def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Experience]:
        """Keyword search across topic / summary / details."""
        keywords = _tokenize(query)
        if not keywords:
            return []

        now = time.time()
        scored: list[tuple[float, Experience]] = []
        for exp in self._load_all():
            hits = _count_hits(keywords, exp)
            if hits == 0:
                continue
            scored.append((hits + _recency_score(exp.timestamp, now), exp))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [exp for _, exp in scored[:top_k]]

    async def list_recent(
        self,
        limit: int = 20,
    ) -> List[Experience]:
        """Return recent experiences."""
        all_exp = self._load_all()
        all_exp.sort(key=lambda e: e.timestamp, reverse=True)
        return all_exp[:limit]

    async def get(
        self,
        experience_id: str,
    ) -> Optional[Experience]:
        """Fetch one experience by ID."""
        for exp in self._load_all():
            if exp.id == experience_id:
                return exp
        return None

    def _load_all(self) -> List[Experience]:
        """Read all JSONL rows."""
        experiences: list[Experience] = []
        if not self._path.exists():
            return experiences
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    data["type"] = ExperienceType(data["type"])
                    experiences.append(Experience(**data))
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping malformed experience line: %s",
                        line[:120],
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                ):
                    logger.warning(
                        "Skipping malformed experience line: %s",
                        line[:120],
                    )
        return experiences

    def _append(self, experience: Experience) -> None:
        """Append a single JSON line."""
        data = asdict(experience)
        data["type"] = experience.type.value
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _is_duplicate(
        self,
        experience: Experience,
    ) -> bool:
        """True if same topic+type exists within dedup window."""
        cutoff = time.time() - _DEDUP_WINDOW_SECS
        for existing in self._load_all():
            if (
                existing.topic == experience.topic
                and existing.type == experience.type
                and existing.timestamp >= cutoff
            ):
                return True
        return False


def _tokenize(text: str) -> list[str]:
    """Lowercase split; drop short tokens."""
    return [
        w for w in text.lower().split()
        if len(w) >= 2
    ]


def _count_hits(
    keywords: list[str],
    exp: Experience,
) -> int:
    """Count keyword hits in searchable fields."""
    blob = (
        f"{exp.topic} {exp.summary} {exp.details}"
    ).lower()
    return sum(1 for kw in keywords if kw in blob)


def _recency_score(ts: float, now: float) -> float:
    """0-1 bonus: 1.0 for <1 h old, decays over 30 days."""
    age = max(now - ts, 0.0)
    max_age = 30 * 86400
    if age >= max_age:
        return 0.0
    return 1.0 - (age / max_age)
