# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE shared FACT/TIP bank.

Ports the reference ``bank.py`` schema (a single FACT list + single TIP list +
a retired pool, JSON-persisted) onto jiuwen primitives:

* I/O is async via :func:`asyncio.to_thread` with an atomic ``tmp`` + ``os.replace``.
* Dedup is **embedding-based** (cosine >= ``dedup_threshold``) when a provider
  is configured, falling back to the reference's substring dedup otherwise.
* Embeddings are cached by normalized text so dedup and Auto-dream reuse them.
* Records carry display/TTL metadata (``created_at``, ``updated_at``,
  ``last_injected_at``, ``inject_hits``) for Auto-dream prune.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.lite.embeddings import EmbeddingProvider

from .categories import OTHER_CATEGORY, normalize_category
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


def _now() -> float:
    return time.time()


def _new_record(text: str, *, count: int = 1, now: Optional[float] = None) -> Dict[str, Any]:
    ts = now if now is not None else _now()
    return {
        "text": text,
        "count": count,
        "created_at": ts,
        "updated_at": ts,
        "last_injected_at": None,
        "inject_hits": 0,
    }


def _migrate_record(record: Dict[str, Any], default_ts: float) -> Dict[str, Any]:
    """Fill missing TTL/display fields for legacy bank entries.

    Conservative migration: treat missing ``last_injected_at`` as ``default_ts``
    (file mtime or now) so an upgrade does not mass-prune overnight.
    """
    if "count" not in record:
        record["count"] = 1
    if "created_at" not in record:
        record["created_at"] = default_ts
    if "updated_at" not in record:
        record["updated_at"] = record.get("created_at", default_ts)
    if "last_injected_at" not in record:
        # Legacy banks: assume recently shown to avoid one-shot wipe.
        record["last_injected_at"] = record.get("created_at", default_ts)
    if "inject_hits" not in record:
        record["inject_hits"] = 0
    return record


def _store_key(store_path: str) -> str:
    """Normalize a bank path so every session hits the same registry slot."""
    path = str(store_path or "").strip()
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


_SHARED_STORES: Dict[str, "TTSERecordStore"] = {}
_SHARED_STORES_GUARD = threading.Lock()


def shared_store(
    config: TTSEConfig,
    *,
    embedding: Optional[EmbeddingProvider] = None,
) -> "TTSERecordStore":
    """Return the process-wide bank for ``config.store_path``.

    Each web session used to construct its own ``TTSERecordStore`` and only
    load disk at init. ``save()`` then dumped that stale snapshot and wiped
    rules induced by other sessions. One store per path keeps induce/consult
    on the same lists.
    """
    key = _store_key(config.store_path)
    if not key:
        return TTSERecordStore(config, embedding=embedding)
    with _SHARED_STORES_GUARD:
        store = _SHARED_STORES.get(key)
        if store is None:
            store = TTSERecordStore(config, embedding=embedding)
            _SHARED_STORES[key] = store
            logger.info("[TTSERail] shared bank attached path=%s", key)
        elif embedding is not None and store._embedding is None:
            store._embedding = embedding
        return store


def reset_shared_stores() -> None:
    """Drop the process-wide bank cache (tests)."""
    with _SHARED_STORES_GUARD:
        _SHARED_STORES.clear()


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
        self.facts: List[Dict[str, Any]] = []  # [{"text","count","category"?,...meta}]
        self.tips: List[Dict[str, Any]] = []
        self.retired: List[Dict[str, Any]] = []  # [{"text","rtype","reason","retired_at_task"}]
        self._emb_cache: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()
        # Cross-session induce/dream must serialize on the same bank object.
        self.evolution_lock = asyncio.Lock()
        self._loaded_mtime: float = 0.0
        self._emb_rate_lock = asyncio.Lock()
        self._emb_last_call_at: float = 0.0
        self._load_sync()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _disk_mtime(self) -> float:
        path = self._config.store_path
        if not path or not os.path.exists(path):
            return 0.0
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _load_sync(self) -> None:
        path = self._config.store_path
        if not path or not os.path.exists(path):
            return
        try:
            default_ts = os.path.getmtime(path)
        except OSError:
            default_ts = _now()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.facts = [_migrate_record(dict(r), default_ts) for r in data.get("facts", [])]
            self.tips = [_migrate_record(dict(r), default_ts) for r in data.get("tips", [])]
            self.retired = list(data.get("retired", []))
            self._loaded_mtime = default_ts
        except (OSError, ValueError) as exc:
            logger.warning("[TTSERail] bank load failed at %s: %s", path, exc)

    def reload_if_disk_newer(self) -> bool:
        """Reload when another process wrote ``bank.json`` (mtime moved)."""
        mtime = self._disk_mtime()
        if mtime <= 0 or mtime <= float(self._loaded_mtime or 0.0):
            return False
        logger.info("[TTSERail] reloading bank from disk mtime=%s path=%s", mtime, self._config.store_path)
        self._load_sync()
        return True

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
            self._loaded_mtime = self._disk_mtime()
        except OSError as exc:
            logger.warning("[TTSERail] bank save failed at %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Embeddings (cached)
    # ------------------------------------------------------------------

    async def _wait_embedding_slot(self) -> None:
        """Space cache-miss embedding API starts to respect ``embedding_max_rps``."""
        max_rps = self._config.embedding_max_rps
        if max_rps is None or max_rps <= 0:
            return
        min_interval = 1.0 / max_rps
        async with self._emb_rate_lock:
            now = time.monotonic()
            wait = self._emb_last_call_at + min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._emb_last_call_at = time.monotonic()

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
            await self._wait_embedding_slot()
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
        """Public cached embedding accessor (reused by Auto-dream)."""
        return await self._embedding_of(text)

    def has_embedding_provider(self) -> bool:
        """Whether semantic dedup is enabled (a provider is configured)."""
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
    # Soft clustering (Auto-dream)
    # ------------------------------------------------------------------

    async def soft_cluster(
        self,
        records: Sequence[Dict[str, Any]],
        *,
        soft_lo: float,
        min_size: int = 2,
    ) -> List[List[Dict[str, Any]]]:
        """Union-find clusters by pairwise cosine >= ``soft_lo``.

        Returns only components with ``len >= min_size``. Empty when no
        embedding provider or fewer than ``min_size`` embeddable records.
        """
        if not self.has_embedding_provider() or len(records) < min_size:
            return []
        n = len(records)
        vectors: List[Optional[List[float]]] = []
        for record in records:
            vectors.append(await self._embedding_of(record["text"]))
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(n):
            if vectors[i] is None:
                continue
            for j in range(i + 1, n):
                if vectors[j] is None:
                    continue
                if _cosine(vectors[i], vectors[j]) >= soft_lo:
                    union(i, j)

        buckets: Dict[int, List[Dict[str, Any]]] = {}
        for i, record in enumerate(records):
            if vectors[i] is None:
                continue
            buckets.setdefault(find(i), []).append(record)
        clusters = [members for members in buckets.values() if len(members) >= min_size]
        clusters.sort(key=lambda c: -len(c))
        return clusters

    async def pairwise_sims(self, records: Sequence[Dict[str, Any]]) -> List[Tuple[int, int, float]]:
        """Pairwise cosine similarities for LLM merge context (i < j)."""
        out: List[Tuple[int, int, float]] = []
        vectors: List[Optional[List[float]]] = []
        for record in records:
            vectors.append(await self._embedding_of(record["text"]))
        for i in range(len(records)):
            if vectors[i] is None:
                continue
            for j in range(i + 1, len(records)):
                if vectors[j] is None:
                    continue
                out.append((i, j, _cosine(vectors[i], vectors[j])))
        return out

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
                # Keep the surviving record's category; do not reclassify on merge.
                matched["updated_at"] = _now()
                store.sort(key=lambda x: -x.get("count", 0))
                return "merged"
            store.append(_new_record(text))
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

    async def add_record_direct(
        self,
        rtype: str,
        text: str,
        *,
        count: int = 1,
        save: bool = True,
    ) -> Dict[str, Any]:
        """Insert a record without online dedup (used by dream MERGE/REWRITE)."""
        store = self.facts if rtype == "fact" else self.tips
        cap = self._config.max_facts if rtype == "fact" else self._config.max_tips
        record = _new_record(text, count=count)
        async with self._lock:
            store.append(record)
            store.sort(key=lambda x: -x.get("count", 0))
            del store[cap:]
        if save:
            await self.save()
        return record

    def mark_injected(self, records: Sequence[Dict[str, Any]], *, now: Optional[float] = None) -> int:
        """Refresh display clock on records that actually entered the prompt.

        Mutates the shared dict objects in the bank. Returns how many records
        were updated.
        """
        ts = now if now is not None else _now()
        updated = 0
        for record in records:
            if not isinstance(record, dict) or "text" not in record:
                continue
            record["last_injected_at"] = ts
            record["inject_hits"] = int(record.get("inject_hits", 0)) + 1
            updated += 1
        return updated

    async def retire(self, text: str, rtype: str, reason: str, task_id: str = "", *, save: bool = True) -> int:
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
        if removed and save:
            await self.save()
        return removed

    async def delete_record(self, text: str, rtype: str, *, save: bool = True) -> int:
        """Remove from active bank without appending to retired."""
        store = self.facts if rtype == "fact" else self.tips
        n = _norm(text)
        async with self._lock:
            kept = [r for r in store if _norm(r["text"]) != n]
            removed = len(store) - len(kept)
            if rtype == "fact":
                self.facts = kept
            else:
                self.tips = kept
        if removed and save:
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

    @staticmethod
    def record_category(record: Dict[str, Any]) -> str:
        """Closed-set category for a bank record; missing/illegal → other."""
        return normalize_category(record.get("category") if isinstance(record, dict) else None)

    def catalog_counts(self) -> Dict[str, int]:
        """FACT+TIP counts keyed by normalized category (includes other)."""
        counts: Dict[str, int] = {}
        for record in (*self.facts, *self.tips):
            cid = self.record_category(record)
            counts[cid] = counts.get(cid, 0) + 1
        return counts

    def records_for_category(self, category: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Facts and tips whose category matches ``category`` (normalized)."""
        cid = normalize_category(category)
        facts = [r for r in self.facts_records() if self.record_category(r) == cid]
        tips = [r for r in self.tips_records() if self.record_category(r) == cid]
        return facts, tips

    async def set_categories(self, assignments: List[Tuple[str, str, str]]) -> int:
        """Patch ``category`` on matching records. Returns how many were updated."""
        if not assignments:
            return 0
        updated = 0
        async with self._lock:
            for text, rtype, category in assignments:
                store = self.facts if rtype == "fact" else self.tips
                n = _norm(text)
                cid = normalize_category(category) or OTHER_CATEGORY
                for record in store:
                    if _norm(record.get("text", "")) == n:
                        record["category"] = cid
                        updated += 1
                        break
        if updated:
            await self.save()
        return updated

    def stats(self) -> Dict[str, int]:
        return {"facts": len(self.facts), "tips": len(self.tips), "retired": len(self.retired)}


__all__ = [
    "TTSERecordStore",
    "shared_store",
    "reset_shared_stores",
    "_cosine",
    "_norm",
    "_new_record",
    "_migrate_record",
]
