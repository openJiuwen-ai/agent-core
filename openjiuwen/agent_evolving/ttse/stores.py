# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE shared FACT/TIP bank.

Ports the reference ``bank.py`` schema (a single FACT list + single TIP list +
a retired pool, JSON-persisted) onto jiuwen primitives:

* I/O is async via :func:`asyncio.to_thread` with an atomic ``tmp`` + ``os.replace``.
* Dedup is **embedding-based** (cosine >= ``dedup_threshold``) when a provider
  is configured, falling back to self-normalized BM25
  (>= ``bm25_sim_threshold``, default 0.5) otherwise. Exact normalized
  equality is always a hit. The scan is O(n) in bank size (capped by
  ``max_facts`` / ``max_tips``); a process-local embedding cache makes
  repeat adds CPU-only when a provider is set. Cold cache fills with
  batched ``embed_documents``, not one RPC per row. n<=400 is a linear
  scan; an ANN index is not used.
* Embeddings are cached by normalized text so dedup and Auto-dream reuse them.
  The cache is an LRU capped at ``max_facts + max_tips + 100`` and is pruned
  when records leave the bank (retire / delete / cap / reload).
* Records carry display/TTL metadata (``created_at``, ``updated_at``,
  ``last_injected_at``, ``inject_hits``) for Auto-dream prune. Consult
  hits update those clocks in memory; they flush on bank writes and on a
  debounce (``inject_persist_min_secs`` / ``inject_persist_min_hits``).
  Reloading a newer disk snapshot overlays in-memory inject clocks so a
  concurrent save cannot TTL-prune a rule that was just injected here.

Deployment: :func:`shared_store` is process-wide and keyed only by
``store_path``. Production AgentServer is one event loop per worker process;
tests / ``asyncio.run`` / ephemeral workers may replace that loop. Bank mutexes
are therefore :class:`threading.Lock`-backed so they are not bound to the loop
of first acquire. Distinct :class:`TTSEConfig` objects that resolve to the same
path share one instance; the first config wins (later callers only backfill a
missing embedding provider).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.lite.embeddings import EmbeddingProvider

from .bm25_sim import bm25_best_match, pairwise_bm25_sims
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


def _as_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _later_ts(left: Any, right: Any) -> Any:
    """Return the later of two timestamps; ``None`` loses to a real value."""
    left_ts, right_ts = _as_ts(left), _as_ts(right)
    if left_ts is None:
        return right if right_ts is not None else left
    if right_ts is None:
        return left
    return left if left_ts >= right_ts else right


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
_CROSS_LOOP_LOCK_POLL_S = 0.005
# Cold-cache fill uses provider.embed_documents in chunks (one RPC per chunk).
_EMBED_DOC_BATCH = 32
# Headroom above max_facts+max_tips for in-flight query texts (consult / dedup).
_EMBED_CACHE_SLACK = 100


class _CrossLoopLock:
    """Async mutex that is not bound to a single event loop.

    ``asyncio.Lock`` binds to the loop of the first ``acquire()``. This store is
    a process-wide singleton, so a later loop (tests, ``asyncio.run``, an
    ephemeral worker that then yields to AgentServer's main loop) would raise
    ``RuntimeError: ... is bound to a different event loop``. A ``threading.Lock``
    is loop-agnostic; we acquire it without blocking the event loop.
    """

    __slots__ = ("_lock",)

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def acquire(self) -> bool:
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(_CROSS_LOOP_LOCK_POLL_S)
        return True

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> "_CrossLoopLock":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()


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

    Empty ``store_path`` is not cached. Two configs that normalize to the same
    path share one object; knobs on the second config (caps, dedup, RPS, …)
    are ignored except that a missing embedding provider may be backfilled.
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
        elif embedding is not None:
            store.attach_embedding(embedding)
        return store


def reset_shared_stores() -> None:
    """Drop the process-wide bank cache (tests)."""
    with _SHARED_STORES_GUARD:
        stores = list(_SHARED_STORES.values())
        _SHARED_STORES.clear()
    for store in stores:
        store.cancel_inject_persist()


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
        self._emb_cache: OrderedDict[str, List[float]] = OrderedDict()
        # threading.Lock-backed: shared_store outlives any one event loop.
        self._lock = _CrossLoopLock()
        # Cross-session induce/dream must serialize on the same bank object.
        self.evolution_lock = _CrossLoopLock()
        self._loaded_mtime: float = 0.0
        self._emb_rate_lock = _CrossLoopLock()
        self._emb_last_call_at: float = 0.0
        self._persist_lock = _CrossLoopLock()
        self._inject_dirty: bool = False
        self._inject_unsaved_hits: int = 0
        self._last_persist_mono: float = time.monotonic()
        self._inject_save_task: Optional[asyncio.Task] = None
        self._inject_save_handle: Optional[asyncio.TimerHandle] = None
        self._load_sync()

    def attach_embedding(self, embedding: EmbeddingProvider) -> None:
        """Bind an embedding provider if this bank was created without one."""
        if self._embedding is None:
            self._embedding = embedding

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
            self._drop_stale_embeddings()
        except (OSError, ValueError) as exc:
            logger.warning("[TTSERail] bank load failed at %s: %s", path, exc)

    def reload(self) -> None:
        """Reload ``bank.json`` from the current ``store_path``."""
        self._load_sync()

    def reload_if_disk_newer(self) -> bool:
        """Reload when another process wrote ``bank.json`` (mtime moved).

        Overlays this process's in-memory inject clocks onto the loaded
        snapshot so a concurrent save cannot drop ``last_injected_at`` /
        ``inject_hits`` used by TTL prune. Does not persist; the caller
        (induce / dream) typically ``save()`` afterwards.
        """
        mtime = self._disk_mtime()
        if mtime <= 0 or mtime <= float(self._loaded_mtime or 0.0):
            return False
        logger.info("[TTSERail] reloading bank from disk mtime=%s path=%s", mtime, self._config.store_path)
        clocks = self._capture_inject_clocks()
        self.reload()
        if self._overlay_inject_clocks(clocks):
            self._inject_dirty = True
        return True

    def _bank_snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        """Copy bank lists before ``json.dump`` runs in a worker thread.

        ``save`` holds ``_persist_lock``, but mutations (``add_fact`` / prune /
        retire) take ``_lock``. ``asyncio.to_thread`` yields the event loop, so
        another coroutine can append/sort/delete the live lists while CPython
        iterates them in ``json.dump``. Snapshot on the event loop first.
        """
        return {
            "facts": [dict(record) for record in self.facts],
            "tips": [dict(record) for record in self.tips],
            "retired": [dict(record) for record in self.retired],
        }

    async def save(self) -> None:
        async with self._persist_lock:
            self._cancel_inject_timer()
            payload = self._bank_snapshot()
            await asyncio.to_thread(self._save_blocking, payload)

    async def flush_inject_metadata(self) -> bool:
        """Persist dirty consult-inject clocks if any.

        Reloads a newer disk snapshot first and overlays this process's
        clocks so a stale in-memory bank cannot clobber concurrent writes.
        Returns whether a save ran.
        """
        if not self._inject_dirty or not self._config.store_path:
            return False
        async with self._persist_lock:
            if not self._inject_dirty:
                return False
            self._cancel_inject_timer()
            clocks = self._capture_inject_clocks()
            mtime = self._disk_mtime()
            if mtime > 0 and mtime > float(self._loaded_mtime or 0.0):
                logger.info(
                    "[TTSERail] reloading bank from disk mtime=%s path=%s",
                    mtime,
                    self._config.store_path,
                )
                self.reload()
                self._overlay_inject_clocks(clocks)
            payload = self._bank_snapshot()
            await asyncio.to_thread(self._save_blocking, payload)
            return True

    def cancel_inject_persist(self) -> None:
        """Cancel a pending consult-inject debounce timer or flush task.

        Used by tests when dropping the process-wide bank cache so a timer
        from a closed event loop cannot fire after teardown.
        """
        self._cancel_inject_timer()
        task = self._inject_save_task
        if task is None or task.done():
            return
        try:
            task.cancel()
        except RuntimeError:
            pass

    def _save_blocking(self, data: Optional[Dict[str, Any]] = None) -> None:
        path = self._config.store_path
        if not path:
            return
        if data is None:
            data = self._bank_snapshot()
        tmp = f"{path}.tmp"
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            self._loaded_mtime = self._disk_mtime()
            self._inject_dirty = False
            self._inject_unsaved_hits = 0
            self._last_persist_mono = time.monotonic()
        except (OSError, TypeError, ValueError) as exc:
            # json.dump raises TypeError/ValueError for non-JSON values; persist
            # is best-effort so induction/dream must not crash on a bad record.
            logger.warning("[TTSERail] bank save failed at %s: %s", path, exc)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _capture_inject_clocks(self) -> Dict[Tuple[str, str], Tuple[Any, int]]:
        """Map ``(rtype, normalized text)`` to ``(last_injected_at, inject_hits)``."""
        clocks: Dict[Tuple[str, str], Tuple[Any, int]] = {}
        for rtype, records in (("fact", self.facts), ("tip", self.tips)):
            for record in records:
                text = record.get("text", "") if isinstance(record, dict) else ""
                if not text:
                    continue
                clocks[(rtype, _norm(text))] = (
                    record.get("last_injected_at"),
                    int(record.get("inject_hits", 0) or 0),
                )
        return clocks

    def _overlay_inject_clocks(self, clocks: Dict[Tuple[str, str], Tuple[Any, int]]) -> bool:
        """Merge captured inject clocks onto the current lists. Returns if changed."""
        if not clocks:
            return False
        changed = False
        for rtype, records in (("fact", self.facts), ("tip", self.tips)):
            for record in records:
                if not isinstance(record, dict):
                    continue
                prior = clocks.get((rtype, _norm(record.get("text", ""))))
                if prior is None:
                    continue
                mem_ts, mem_hits = prior
                merged_ts = _later_ts(record.get("last_injected_at"), mem_ts)
                merged_hits = max(int(record.get("inject_hits", 0) or 0), int(mem_hits or 0))
                if record.get("last_injected_at") != merged_ts:
                    record["last_injected_at"] = merged_ts
                    changed = True
                if int(record.get("inject_hits", 0) or 0) != merged_hits:
                    record["inject_hits"] = merged_hits
                    changed = True
        return changed

    def _cancel_inject_timer(self) -> None:
        handle = self._inject_save_handle
        if handle is not None:
            handle.cancel()
            self._inject_save_handle = None

    def _should_flush_inject_now(self) -> bool:
        if not self._inject_dirty:
            return False
        min_hits = int(self._config.inject_persist_min_hits)
        min_secs = float(self._config.inject_persist_min_secs)
        if min_hits > 0 and self._inject_unsaved_hits >= min_hits:
            return True
        elapsed = time.monotonic() - self._last_persist_mono
        return min_secs <= 0 or elapsed >= min_secs

    def _inject_task_on(self, loop: asyncio.AbstractEventLoop) -> bool:
        task = self._inject_save_task
        if task is None or task.done():
            return False
        try:
            return task.get_loop() is loop and not loop.is_closed()
        except RuntimeError:
            return False

    def _inject_handle_on(self, loop: asyncio.AbstractEventLoop) -> bool:
        handle = self._inject_save_handle
        if handle is None or handle.cancelled():
            return False
        return getattr(handle, "_loop", None) is loop and not loop.is_closed()

    def _spawn_inject_flush(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._inject_task_on(loop):
            return
        self._inject_save_task = loop.create_task(self._scheduled_inject_flush())

    def _on_inject_timer(self, loop: asyncio.AbstractEventLoop) -> None:
        self._inject_save_handle = None
        if not self._inject_dirty or loop.is_closed():
            return
        self._spawn_inject_flush(loop)

    async def _scheduled_inject_flush(self) -> None:
        try:
            await self.flush_inject_metadata()
        except Exception as exc:  # noqa: BLE001 - persist is best-effort
            logger.warning("[TTSERail] inject metadata flush failed: %s", exc)

    def _maybe_schedule_inject_save(self) -> None:
        if not self._inject_dirty or not self._config.store_path:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._should_flush_inject_now():
            self._cancel_inject_timer()
            self._spawn_inject_flush(loop)
            return
        if self._inject_handle_on(loop):
            return
        # Drop a handle/task left on a closed or foreign loop (loop.close()
        # clears the scheduler without cancelling TimerHandle).
        self._cancel_inject_timer()
        delay = max(0.0, float(self._config.inject_persist_min_secs) - (time.monotonic() - self._last_persist_mono))
        if delay <= 0:
            self._spawn_inject_flush(loop)
            return
        self._inject_save_handle = loop.call_later(delay, self._on_inject_timer, loop)

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

    def _embedding_cache_limit(self) -> int:
        return max(int(self._config.max_facts) + int(self._config.max_tips) + _EMBED_CACHE_SLACK, 1)

    def _cached_embedding(self, key: str) -> Optional[List[float]]:
        vec = self._emb_cache.get(key)
        if vec is not None:
            self._emb_cache.move_to_end(key)
        return vec

    def _store_embedding(self, key: str, vec: List[float]) -> None:
        if not key or not vec:
            return
        self._emb_cache[key] = vec
        self._emb_cache.move_to_end(key)
        limit = self._embedding_cache_limit()
        while len(self._emb_cache) > limit:
            self._emb_cache.popitem(last=False)

    def _drop_stale_embeddings(self) -> None:
        """Drop vectors whose text is no longer in the active FACT/TIP bank."""
        live = {_norm(record.get("text", "")) for record in (*self.facts, *self.tips)}
        live.discard("")
        stale = [key for key in self._emb_cache if key not in live]
        for key in stale:
            del self._emb_cache[key]

    async def _embedding_of(self, text: str) -> Optional[List[float]]:
        key = _norm(text)
        if not key:
            return None
        cached = self._cached_embedding(key)
        if cached is not None:
            return cached
        if self._embedding is None:
            return None
        try:
            model = getattr(self._embedding, "model", None) or getattr(self._embedding, "id", "unknown")
            logger.debug("[TTSERail] embedding model=%s text=%s", model, text[:60])
            await self._wait_embedding_slot()
            vec = await self._embedding.embed_query(text)
        except Exception as exc:  # noqa: BLE001 - degrade to BM25 dedup
            logger.warning("[TTSERail] embedding failed, falling back to BM25 dedup: %s", exc)
            return None
        if vec:
            self._store_embedding(key, vec)
            logger.debug(
                "[TTSERail] embedding ok model=%s dims=%s cache_size=%s",
                model,
                len(vec),
                len(self._emb_cache),
            )
        return vec or None

    async def _fill_embedding_cache(self, texts: Sequence[str]) -> None:
        """Embed cache misses in batches; skip texts already in ``_emb_cache``.

        Prefers ``embed_documents`` (one RPC per chunk of ``_EMBED_DOC_BATCH``).
        Providers without it, or a failed batch, fall back to per-text
        ``embed_query``. Repeat scans after a warm cache do no I/O.
        """
        if self._embedding is None:
            return
        missing: List[str] = []
        seen: set[str] = set()
        for text in texts:
            key = _norm(text)
            if not key or key in self._emb_cache or key in seen:
                continue
            seen.add(key)
            missing.append(str(text))
        if not missing:
            return
        batch_fn = getattr(self._embedding, "embed_documents", None)
        if callable(batch_fn):
            try:
                for i in range(0, len(missing), _EMBED_DOC_BATCH):
                    end = i + _EMBED_DOC_BATCH
                    chunk = missing[i:end]
                    await self._wait_embedding_slot()
                    vectors = await batch_fn(chunk)
                    for text, vec in zip(chunk, vectors or []):
                        if vec:
                            self._store_embedding(_norm(text), list(vec))
            except Exception as exc:  # noqa: BLE001 - degrade to per-text
                logger.warning("[TTSERail] embedding batch failed, falling back to per-text: %s", exc)
        for text in missing:
            if _norm(text) not in self._emb_cache:
                await self._embedding_of(text)

    async def embedding_of(self, text: str) -> Optional[List[float]]:
        """Public cached embedding accessor (reused by Auto-dream)."""
        return await self._embedding_of(text)

    def has_embedding_provider(self) -> bool:
        """Whether cosine semantic similarity is enabled (a provider is configured)."""
        return self._embedding is not None

    async def _find_duplicate(self, text: str, store: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the matching record if ``text`` duplicates an existing rule.

        Cosine match when an embedding provider is available; otherwise
        self-normalized BM25 (>= ``bm25_sim_threshold``). Exact normalized
        equality is always treated as a duplicate. Scans are O(n) in
        ``store`` (n capped by max_facts/max_tips).
        """
        n = _norm(text)
        if not n:
            return None
        for record in store:
            if _norm(record.get("text", "")) == n:
                return record

        if self._embedding is not None:
            await self._fill_embedding_cache(
                [text, *(record.get("text", "") for record in store)]
            )
            vec = self._cached_embedding(n)
            if vec is not None:
                best: Optional[Dict[str, Any]] = None
                best_sim = self._config.dedup_threshold
                for record in store:
                    other = self._cached_embedding(_norm(record.get("text", "")))
                    if other is None:
                        continue
                    sim = _cosine(vec, other)
                    if sim >= best_sim:
                        best, best_sim = record, sim
                return best

        # BM25 fallback (no provider, or embedding produced no query vector).
        docs = [str(record.get("text") or "") for record in store]
        idx = bm25_best_match(
            text,
            docs,
            threshold=float(self._config.bm25_sim_threshold),
        )
        if idx is None:
            return None
        return store[idx]

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
        """Union-find clusters by pairwise similarity >= threshold.

        With an embedding provider, edges use cosine >= ``soft_lo``.
        Without one, edges use self-normalized BM25 >= ``bm25_sim_threshold``
        (``soft_lo`` is ignored on that path). Returns only components with
        ``len >= min_size``.
        """
        if len(records) < min_size:
            return []
        n = len(records)
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

        if self.has_embedding_provider():
            vectors: List[Optional[List[float]]] = []
            for record in records:
                vectors.append(await self._embedding_of(record["text"]))
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
        else:
            threshold = float(self._config.bm25_sim_threshold)
            texts = [str(record.get("text") or "") for record in records]
            for i, j, sim in pairwise_bm25_sims(texts):
                if sim >= threshold:
                    union(i, j)
            buckets = {}
            for i, record in enumerate(records):
                buckets.setdefault(find(i), []).append(record)

        clusters = [members for members in buckets.values() if len(members) >= min_size]
        clusters.sort(key=lambda c: -len(c))
        return clusters

    async def pairwise_sims(self, records: Sequence[Dict[str, Any]]) -> List[Tuple[int, int, float]]:
        """Pairwise similarities for LLM merge context (i < j).

        Cosine when an embedding provider is set; otherwise self-normalized
        BM25.
        """
        if not self.has_embedding_provider():
            texts = [str(record.get("text") or "") for record in records]
            return pairwise_bm25_sims(texts)
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
        count was bumped (BM25/semantic duplicate), or ``None`` when the
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
            if len(store) > cap:
                del store[cap:]
                self._drop_stale_embeddings()
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
        category: Optional[str] = None,
        save: bool = True,
    ) -> Dict[str, Any]:
        """Insert a record without online dedup (used by dream MERGE/REWRITE)."""
        store = self.facts if rtype == "fact" else self.tips
        cap = self._config.max_facts if rtype == "fact" else self._config.max_tips
        record = _new_record(text, count=count)
        if category is not None:
            record["category"] = normalize_category(category) or OTHER_CATEGORY
        async with self._lock:
            store.append(record)
            store.sort(key=lambda x: -x.get("count", 0))
            if len(store) > cap:
                del store[cap:]
                self._drop_stale_embeddings()
        if save:
            await self.save()
        return record

    def mark_injected(self, records: Sequence[Dict[str, Any]], *, now: Optional[float] = None) -> int:
        """Refresh display clock on records that actually entered the prompt.

        Mutates the shared dict objects in the bank. Returns how many records
        were updated. Persistence is debounced; call
        :meth:`flush_inject_metadata` to force a write.
        """
        ts = now if now is not None else _now()
        updated = 0
        for record in records:
            if not isinstance(record, dict) or "text" not in record:
                continue
            record["last_injected_at"] = ts
            record["inject_hits"] = int(record.get("inject_hits", 0) or 0) + 1
            updated += 1
        if updated:
            self._inject_dirty = True
            self._inject_unsaved_hits += updated
            self._maybe_schedule_inject_save()
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
                self._drop_stale_embeddings()
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
            if removed:
                self._drop_stale_embeddings()
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
