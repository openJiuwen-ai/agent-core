# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE shared FACT/TIP bank.

Ports the reference ``bank.py`` schema (a single FACT list + single TIP list +
a retired pool, JSON-persisted) onto jiuwen primitives:

* I/O is async via :func:`asyncio.to_thread` with an atomic ``tmp`` + ``os.replace``.
* Dedup is **embedding-based** (cosine >= ``dedup_threshold``) when a provider
  is configured, falling back to the reference's substring dedup otherwise.
* Embeddings are cached by normalized text so retrieval (Slice 2) reuses them.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.lite.embeddings import EmbeddingProvider

from .config import TTSEConfig


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class TTSERecordStore:
    """In-memory FACT/TIP bank with JSON persistence and semantic dedup."""

    def __init__(
        self,
        config: TTSEConfig,
        *,
        embedding: Optional[EmbeddingProvider] = None,
    ) -> None:
        self._config = config
        self._embedding: Optional[EmbeddingProvider] = embedding or config.embedding
        self.facts: List[Dict[str, Any]] = []  # [{"text","count"}]
        self.tips: List[Dict[str, Any]] = []
        self.retired: List[Dict[str, Any]] = []  # [{"text","rtype","reason","retired_at_task"}]
        self._emb_cache: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()
        self._load_sync()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_sync(self) -> None:
        path = self._config.store_path
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.facts = list(data.get("facts", []))
            self.tips = list(data.get("tips", []))
            self.retired = list(data.get("retired", []))
        except (OSError, ValueError) as exc:
            logger.warning("[TTSERail] bank load failed at %s: %s", path, exc)

    async def save(self) -> None:
        await asyncio.to_thread(self._save_blocking)

    def _save_blocking(self) -> None:
        path = self._config.store_path
        if not path:
            return
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        data = {"facts": self.facts, "tips": self.tips, "retired": self.retired}
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("[TTSERail] bank save failed at %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Embeddings (cached)
    # ------------------------------------------------------------------

    async def _embedding_of(self, text: str) -> Optional[List[float]]:
        key = _norm(text)
        if not key:
            return None
        cached = self._emb_cache.get(key)
        if cached is not None:
            return cached
        if self._embedding is None:
            return None
        try:
            model = getattr(self._embedding, "model", None) or getattr(self._embedding, "id", "unknown")
            logger.debug("[TTSERail] embedding model=%s text=%s", model, text[:60])
            vec = await self._embedding.embed_query(text)
        except Exception as exc:  # noqa: BLE001 - degrade to substring dedup
            logger.warning("[TTSERail] embedding failed, falling back to substring dedup: %s", exc)
            return None
        if vec:
            self._emb_cache[key] = vec
            logger.debug(
                "[TTSERail] embedding ok model=%s dims=%s cache_size=%s",
                model,
                len(vec),
                len(self._emb_cache),
            )
        return vec or None

    async def embedding_of(self, text: str) -> Optional[List[float]]:
        """Public cached embedding accessor (reused by retrieval)."""
        return await self._embedding_of(text)

    def has_embedding_provider(self) -> bool:
        """Whether semantic retrieval/dedup is enabled (a provider is configured)."""
        return self._embedding is not None

    async def _find_duplicate(self, text: str, store: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the matching record if ``text`` duplicates an existing rule."""
        if self._embedding is not None:
            vec = await self._embedding_of(text)
            if vec is not None:
                best: Optional[Dict[str, Any]] = None
                best_sim = self._config.dedup_threshold
                for record in store:
                    other = await self._embedding_of(record["text"])
                    if other is None:
                        continue
                    sim = _cosine(vec, other)
                    if sim >= best_sim:
                        best, best_sim = record, sim
                return best
        # Substring dedup (reference behavior when no embedding provider).
        n = _norm(text)
        for record in store:
            rn = _norm(record["text"])
            if rn == n or n in rn or rn in n:
                return record
        return None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    async def _add(self, store: List[Dict[str, Any]], text: str, cap: int) -> Optional[str]:
        """Add or merge a rule.

        Returns ``"added"`` for a new rule, ``"merged"`` when an existing rule's
        count was bumped (substring/semantic duplicate), or ``None`` when the
        text was empty. Mirrors the reference: the bank mutates on both ``added``
        and ``merged``, but only ``added`` counts as a new rule.
        """
        n = _norm(text)
        if not n:
            return None
        async with self._lock:
            matched = await self._find_duplicate(text, store)
            if matched is not None:
                matched["count"] = matched.get("count", 0) + 1
                store.sort(key=lambda x: -x.get("count", 0))
                return "merged"
            store.append({"text": text, "count": 1})
            store.sort(key=lambda x: -x.get("count", 0))
            del store[cap:]
            return "added"

    async def add_fact(self, text: str) -> bool:
        result = await self._add(self.facts, text, self._config.max_facts)
        if result is not None:
            await self.save()
        if result == "added":
            logger.info("[TTSERail] wrote new fact: %s; bank stats=%s", text[:80], self.stats())
        elif result == "merged":
            logger.debug("[TTSERail] merged duplicate fact: %s", text[:80])
        return result == "added"

    async def add_tip(self, text: str) -> bool:
        result = await self._add(self.tips, text, self._config.max_tips)
        if result is not None:
            await self.save()
        if result == "added":
            logger.info("[TTSERail] wrote new tip: %s; bank stats=%s", text[:80], self.stats())
        elif result == "merged":
            logger.debug("[TTSERail] merged duplicate tip: %s", text[:80])
        return result == "added"

    async def retire(self, text: str, rtype: str, reason: str, task_id: str = "") -> int:
        store = self.facts if rtype == "fact" else self.tips
        n = _norm(text)
        async with self._lock:
            kept = [r for r in store if _norm(r["text"]) != n]
            removed = len(store) - len(kept)
            if rtype == "fact":
                self.facts = kept
            else:
                self.tips = kept
            if removed:
                self.retired.append({"text": text, "rtype": rtype, "reason": reason, "retired_at_task": task_id})
        if removed:
            await self.save()
        return removed

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sorted(store: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(store, key=lambda x: -x.get("count", 0))

    def facts_records(self) -> List[Dict[str, Any]]:
        return self._sorted(self.facts)

    def tips_records(self) -> List[Dict[str, Any]]:
        return self._sorted(self.tips)

    def facts_texts(self) -> List[str]:
        return [r["text"] for r in self.facts_records()]

    def tips_texts(self) -> List[str]:
        return [r["text"] for r in self.tips_records()]

    def snapshot_flat(self) -> List[Tuple[str, str]]:
        """Flat (text, rtype) list, facts first, count-desc. For blame numbering."""
        facts = [(r["text"], "fact") for r in self.facts_records()]
        tips = [(r["text"], "tip") for r in self.tips_records()]
        return facts + tips

    def stats(self) -> Dict[str, int]:
        return {"facts": len(self.facts), "tips": len(self.tips), "retired": len(self.retired)}


__all__ = ["TTSERecordStore"]
