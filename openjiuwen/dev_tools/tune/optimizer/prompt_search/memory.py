# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Prompt memory interface + a dependency-light JSONL default.

Stores optimization outcomes (prompt, reward, task characteristics, metrics,
observations, timestamp) and retrieves similar prior optimizations to warm-
start the policy. Callers who have a semantic/embedding store available
(e.g. an experience bank) can implement :class:`PromptMemory` directly instead
of using :class:`JsonlPromptMemory` — nothing here assumes a particular
backend.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import PromptRecord, PromptTaskSpec

_WORD = re.compile(r"[\w]+", re.UNICODE)


class PromptMemory(ABC):
    """Persistent store of prior optimizations with similarity retrieval."""

    @abstractmethod
    def add(self, record: PromptRecord) -> None:
        ...

    @abstractmethod
    def search_similar(self, task: PromptTaskSpec, top_k: int = 3) -> list:
        ...

    @abstractmethod
    def best_for_objective(self, objective: str) -> Optional[PromptRecord]:
        """The best-scoring record for the exact same objective, if any."""

    @abstractmethod
    def pending(self, threshold: float = 0.0) -> list:
        """Records not yet marked ``applied`` whose gain exceeds *threshold*."""

    @abstractmethod
    def mark_applied(self, record_id: str) -> bool:
        """Flag a record as applied so it stops showing up in ``pending``."""


class NullPromptMemory(PromptMemory):
    """No-op memory (used when memory is disabled)."""

    def add(self, record: PromptRecord) -> None:
        return None

    def search_similar(self, task: PromptTaskSpec, top_k: int = 3) -> list:
        return []

    def best_for_objective(self, objective: str) -> Optional[PromptRecord]:
        return None

    def pending(self, threshold: float = 0.0) -> list:
        return []

    def mark_applied(self, record_id: str) -> bool:
        return False


class JsonlPromptMemory(PromptMemory):
    """Append-only JSONL store with cheap lexical (token-overlap) retrieval.

    No embedding backend required. Good enough to warm-start the policy from
    past runs on similar objectives.
    """

    def __init__(self, directory: Union[str, Path]) -> None:
        self._dir = Path(directory)
        self._path = self._dir / "records.jsonl"
        self._lock = threading.Lock()
        self._records: list = []
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._records.append(PromptRecord.from_dict(json.loads(line)))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"JsonlPromptMemory: failed to load {self._path}: {exc}")

    def add(self, record: PromptRecord) -> None:
        with self._lock:
            self._records.append(record)
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def search_similar(self, task: PromptTaskSpec, top_k: int = 3) -> list:
        query_tokens = _tokens(task.characteristics)
        if not query_tokens or not self._records:
            return sorted(self._records, key=lambda r: r.reward, reverse=True)[:top_k]
        scored = []
        for record in self._records:
            overlap = _jaccard(query_tokens, _tokens(record.task_characteristics or record.objective))
            if overlap > 0:
                scored.append((overlap * (0.5 + 0.5 * record.reward), record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def best_for_objective(self, objective: str) -> Optional[PromptRecord]:
        matches = [r for r in self._records if r.objective == objective]
        return max(matches, key=lambda r: r.reward, default=None)

    def pending(self, threshold: float = 0.0) -> list:
        candidates = [r for r in self._records if not r.applied and r.gain > threshold]
        candidates.sort(key=lambda r: r.gain, reverse=True)
        return candidates

    def mark_applied(self, record_id: str) -> bool:
        with self._lock:
            found = False
            for record in self._records:
                if record.record_id == record_id:
                    record.applied = True
                    record.applied_at = time.time()
                    found = True
                    break
            if found:
                self._rewrite()
            return found

    def _rewrite(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for record in self._records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self._path)


def _tokens(text: str) -> set:
    return {t.lower() for t in _WORD.findall(text or "")}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


__all__ = ["PromptMemory", "NullPromptMemory", "JsonlPromptMemory"]
