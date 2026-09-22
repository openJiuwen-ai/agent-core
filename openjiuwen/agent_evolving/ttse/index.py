# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persisted TTSE experience index (Okapi BM25 sidecars + Chroma HNSW).

All consult ranking and index maintenance live here. The bank store only
``bind``s this class; compressor ``bm25.py`` is left unchanged (we reuse
``tokenize``). Chroma is rebuilt in one ``add`` batch when an embedding
provider appears later or its fingerprint changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import tokenize

from .config import normalize_consult_retrieve_mode

TOKENIZER_VERSION = "ttse-bm25-v1"
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
DEFAULT_RRF_K = 60
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_COLLECTION = "ttse_rules"
_FINGERPRINT_FILE = "fingerprint.json"
_CHROMA_RETRY_SECS = 30.0

RetrieveMode = Literal["hybrid", "bm25", "embed", "dump"]
_MODE_RANK = {"hybrid": 3, "embed": 2, "bm25": 1, "dump": 0}


@dataclass(frozen=True)
class RetrieveRulesResult:
    """FACT/TIP hits after hybrid recall (one category or the full bank)."""

    facts: List[Dict[str, Any]]
    tips: List[Dict[str, Any]]
    fact_mode: RetrieveMode
    tip_mode: RetrieveMode

    @property
    def mode(self) -> RetrieveMode:
        if _MODE_RANK.get(self.fact_mode, 0) >= _MODE_RANK.get(self.tip_mode, 0):
            return self.fact_mode
        return self.tip_mode


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _clip(text: str, limit: int = 80) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 1)] + "…"


def _as_scored(rows: Sequence[Any]) -> Tuple[List[str], Dict[str, float]]:
    """Accept ANN ids or ``(id, score)`` pairs from tests / Chroma."""
    ordered: List[str] = []
    scores: Dict[str, float] = {}
    for row in rows or []:
        if isinstance(row, (list, tuple)) and row:
            doc_id = str(row[0])
            score = float(row[1]) if len(row) > 1 and row[1] is not None else 0.0
        else:
            doc_id = str(row)
            score = 0.0
        if not doc_id:
            continue
        ordered.append(doc_id)
        scores[doc_id] = score
    return ordered, scores


def _ranked_preview(
    ordered_ids: Sequence[str],
    scores: Optional[Dict[str, float]] = None,
    *,
    by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    limit: int = 8,
) -> str:
    parts: List[str] = []
    for rank, doc_id in enumerate(list(ordered_ids or [])[:limit], start=1):
        short = str(doc_id)[:8]
        piece = f"{rank}:{short}"
        if scores and doc_id in scores:
            piece += f"={scores[doc_id]:.4f}"
        record = (by_id or {}).get(doc_id) or {}
        text = _clip(str(record.get("text") or ""), 24)
        if text:
            piece += f":{text}"
        parts.append(piece)
    extra = len(ordered_ids or []) - limit
    if extra > 0:
        parts.append(f"+{extra}")
    return ",".join(parts) if parts else "-"


def _track(rtype: str) -> str:
    return "tip" if str(rtype).strip().lower() in {"tip", "tips"} else "fact"


def make_id(track: str, text: str) -> str:
    payload = f"{_track(track)}\0{_norm(text)}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def ensure_id(record: Dict[str, Any], track: str) -> str:
    existing = str(record.get("id") or "").strip()
    if existing:
        return existing
    record_id = make_id(track, str(record.get("text") or ""))
    record["id"] = record_id
    return record_id


def _fingerprint(embedding: Any) -> str:
    if embedding is None:
        return ""
    getter = getattr(embedding, "config_fingerprint", None)
    if callable(getter):
        return str(getter() or "")
    if isinstance(getter, str):
        return getter
    model = getattr(embedding, "model", None) or getattr(embedding, "id", "") or type(embedding).__name__
    return str(model)


class _BM25Index:
    """Incremental Okapi BM25: persist raw stats, compute IDF at query time."""

    def __init__(self, *, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        self.doc_count = 0
        self.sum_dl = 0.0
        self.df: Dict[str, int] = {}
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.postings: Dict[str, List[List[Any]]] = {}
        self.k1 = k1
        self.b = b
        self.tokenizer = TOKENIZER_VERSION

    def add_document(self, doc_id: str, text: str) -> None:
        if doc_id in self.docs:
            self.remove_document(doc_id)
        tokens = tokenize(text)
        tf = dict(Counter(tokens))
        dl = len(tokens)
        self.docs[str(doc_id)] = {"dl": dl, "tf": tf}
        self.doc_count += 1
        self.sum_dl += dl
        for term, freq in tf.items():
            self.df[term] = int(self.df.get(term, 0)) + 1
            self.postings.setdefault(term, []).append([str(doc_id), int(freq)])

    def remove_document(self, doc_id: str) -> bool:
        key = str(doc_id)
        doc = self.docs.pop(key, None)
        if not doc:
            return False
        dl = int(doc.get("dl") or 0)
        tf = doc.get("tf") or {}
        self.doc_count = max(0, self.doc_count - 1)
        self.sum_dl = max(0.0, float(self.sum_dl) - dl)
        for term in tf:
            remaining = int(self.df.get(term, 1)) - 1
            if remaining <= 0:
                self.df.pop(term, None)
            else:
                self.df[term] = remaining
            plist = [pair for pair in self.postings.get(term, []) if str(pair[0]) != key]
            if plist:
                self.postings[term] = plist
            else:
                self.postings.pop(term, None)
        return True

    def score(self, query: str) -> Dict[str, float]:
        if self.doc_count <= 0 or self.sum_dl <= 0:
            return {}
        query_tokens = tokenize(query)
        if not query_tokens:
            return {}
        avgdl = float(self.sum_dl) / float(self.doc_count)
        if avgdl <= 0:
            return {}
        scores: Dict[str, float] = defaultdict(float)
        k1, b = float(self.k1), float(self.b)
        for token in set(query_tokens):
            document_frequency = int(self.df.get(token, 0))
            if document_frequency <= 0:
                continue
            numerator = self.doc_count - document_frequency + 0.5
            idf = math.log(1.0 + numerator / (document_frequency + 0.5))
            for pair in self.postings.get(token, []):
                doc_id = str(pair[0])
                frequency = float(pair[1])
                if frequency <= 0:
                    continue
                document_length = float((self.docs.get(doc_id) or {}).get("dl") or 0)
                denominator = frequency + k1 * (1.0 - b + b * document_length / avgdl)
                if denominator <= 0:
                    continue
                scores[doc_id] += idf * frequency * (k1 + 1.0) / denominator
        return {doc_id: score for doc_id, score in scores.items() if score > 0.0}

    def to_dict(self) -> Dict[str, Any]:
        docs = {
            key: {"dl": value.get("dl", 0), "tf": dict(value.get("tf") or {})}
            for key, value in self.docs.items()
        }
        return {
            "N": self.doc_count,
            "sum_dl": self.sum_dl,
            "df": dict(self.df),
            "docs": docs,
            "postings": {term: [list(pair) for pair in pairs] for term, pairs in self.postings.items()},
            "k1": self.k1,
            "b": self.b,
            "tokenizer": self.tokenizer,
        }

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "_BM25Index":
        data = dict(payload or {})
        index = cls(k1=float(data.get("k1") or DEFAULT_K1), b=float(data.get("b") or DEFAULT_B))
        index.doc_count = int(data.get("N") or data.get("doc_count") or 0)
        index.sum_dl = float(data.get("sum_dl") or 0.0)
        index.df = {str(term): int(count) for term, count in (data.get("df") or {}).items()}
        index.docs = {
            str(doc_id): {
                "dl": int(body.get("dl") or 0),
                "tf": {str(term): int(freq) for term, freq in (body.get("tf") or {}).items()},
            }
            for doc_id, body in (data.get("docs") or {}).items()
            if isinstance(body, dict)
        }
        postings: Dict[str, List[List[Any]]] = {}
        for term, pairs in (data.get("postings") or {}).items():
            kept: List[List[Any]] = []
            for pair in pairs:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    kept.append([str(pair[0]), int(pair[1])])
            postings[str(term)] = kept
        index.postings = postings
        stored = data.get("tokenizer")
        if stored is not None and str(stored) != TOKENIZER_VERSION:
            return cls()
        index.tokenizer = TOKENIZER_VERSION
        return index


class TTSEIndex:
    """Bank-attached retrieval index: category/global BM25 + optional Chroma ANN."""

    def __init__(self, root_dir: str) -> None:
        self.root_dir = root_dir or ".ttse"
        self.bm25_dir = os.path.join(self.root_dir, "bm25")
        self.chroma_path = os.path.join(self.root_dir, "chroma")
        self._bm25: Dict[str, _BM25Index] = {}
        self._dirty: set[str] = set()
        self._chroma: Any = None
        self._chroma_failed = False
        self._chroma_retry_at = 0.0
        self._chroma_stale = False
        self._chroma_lock = asyncio.Lock()
        self._chroma_fingerprint = self._read_fingerprint()

    @classmethod
    def bind(cls, store: Any) -> "TTSEIndex":
        """Attach to a ``TTSERecordStore`` and intercept write/save/reload."""
        path = str(getattr(getattr(store, "_config", None), "store_path", "") or "")
        root = os.path.dirname(path) or ".ttse"
        index = cls(root)
        store.index = index
        index._install(store)
        index.align(store.facts, store.tips, store.record_category)
        index.note_embedding(getattr(store, "_embedding", None))
        logger.info(
            "[TTSERail] index bound root=%s chroma_fp=%s facts=%s tips=%s",
            root,
            index._chroma_fingerprint or "(none)",
            len(store.facts),
            len(store.tips),
        )
        return index

    def _install(self, store: Any) -> None:
        orig_direct = store.add_record_direct
        orig_retire = store.retire
        orig_delete = store.delete_record
        orig_set = store.set_categories
        orig_save = store.save
        orig_reload = store.reload
        orig_add_fn: List[Any] = []

        async def _add(bucket: List[Dict[str, Any]], text: str, cap: int):
            track = "tip" if bucket is store.tips else "fact"
            before = list(bucket)
            result = await orig_add_fn[0](bucket, text, cap)
            if result == "added":
                await self._diff(store, track, before, bucket)
            return result

        async def add_record_direct(rtype: str, text: str, *, count: int = 1, category=None, save: bool = True):
            bucket = store.facts if rtype == "fact" else store.tips
            before = list(bucket)
            record = await orig_direct(rtype, text, count=count, category=category, save=save)
            await self._diff(store, _track(rtype), before, bucket)
            self.persist()
            return record

        async def retire(text: str, rtype: str, reason: str, task_id: str = "", *, save: bool = True):
            victims = self._match(store, text, rtype)
            removed = await orig_retire(text, rtype, reason, task_id, save=save)
            for record in victims:
                await self.delete(record, rtype, store.record_category(record), store)
            self.persist()
            return removed

        async def delete_record(text: str, rtype: str, *, save: bool = True):
            victims = self._match(store, text, rtype)
            removed = await orig_delete(text, rtype, save=save)
            for record in victims:
                await self.delete(record, rtype, store.record_category(record), store)
            self.persist()
            return removed

        async def set_categories(assignments: List[Tuple[str, str, str]]):
            pending = []
            for text, rtype, _category in assignments:
                for record in self._match(store, text, rtype):
                    pending.append((record, rtype, store.record_category(record)))
            updated = await orig_set(assignments)
            for record, rtype, old_category in pending:
                new_category = store.record_category(record)
                if old_category != new_category:
                    await self.move(record, rtype, old_category, new_category, store)
            self.persist()
            return updated

        async def save():
            await orig_save()
            self.persist()

        def reload():
            orig_reload()
            self.align(store.facts, store.tips, store.record_category)
            self.note_embedding(getattr(store, "_embedding", None))

        orig_add_fn.append(store.replace_mutator("_add", _add))
        store.add_record_direct = add_record_direct
        store.retire = retire
        store.delete_record = delete_record
        store.set_categories = set_categories
        store.save = save
        store.reload = reload

    @staticmethod
    def _match(store: Any, text: str, rtype: str) -> List[Dict[str, Any]]:
        needle = _norm(text)
        bucket = store.facts if _track(rtype) == "fact" else store.tips
        return [record for record in bucket if _norm(record.get("text", "")) == needle]

    async def _diff(
        self,
        store: Any,
        track: str,
        before: Sequence[Dict[str, Any]],
        after: Sequence[Dict[str, Any]],
    ) -> None:
        before_ids = {id(record) for record in before}
        after_ids = {id(record) for record in after}
        for record in after:
            if id(record) not in before_ids:
                await self.upsert(record, track, store.record_category(record), store)
        for record in before:
            if id(record) not in after_ids:
                await self.delete(record, track, store.record_category(record), store)

    @staticmethod
    def _key(category: Optional[str], track: str) -> str:
        track_name = _track(track)
        if not category:
            return f"all_{track_name}"
        cleaned = _SAFE_NAME.sub("_", str(category).strip()) or "other"
        return f"{cleaned}_{track_name}"

    def _load_bm25(self, category: Optional[str], track: str) -> _BM25Index:
        return self._load_bm25_key(self._key(category, track))

    def _load_bm25_key(self, key: str) -> _BM25Index:
        cached = self._bm25.get(key)
        if cached is not None:
            return cached
        path = os.path.join(self.bm25_dir, f"{key}.json")
        payload = None
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning("[TTSERail] bm25 load failed at %s: %s", path, exc)
        index = _BM25Index.from_dict(payload)
        self._bm25[key] = index
        return index

    def _known_bm25_keys(self) -> set[str]:
        keys = set(self._bm25)
        try:
            for name in os.listdir(self.bm25_dir):
                if name.endswith(".json"):
                    keys.add(name[:-5])
        except OSError:
            pass
        return keys

    def _expected_bm25_docs(
        self,
        facts: Sequence[Dict[str, Any]],
        tips: Sequence[Dict[str, Any]],
        category_of,
    ) -> Dict[str, set[str]]:
        expected: Dict[str, set[str]] = defaultdict(set)
        for record in facts:
            record_id = ensure_id(record, "fact")
            category = str(category_of(record) or "other")
            expected[self._key(category, "fact")].add(record_id)
            expected[self._key(None, "fact")].add(record_id)
        for record in tips:
            record_id = ensure_id(record, "tip")
            category = str(category_of(record) or "other")
            expected[self._key(category, "tip")].add(record_id)
            expected[self._key(None, "tip")].add(record_id)
        return expected

    def _bm25_matches_bank(self, expected: Dict[str, set[str]]) -> bool:
        for key in self._known_bm25_keys() | set(expected):
            index = self._load_bm25_key(key)
            if index.tokenizer != TOKENIZER_VERSION:
                return False
            if set(index.docs.keys()) != expected.get(key, set()):
                return False
        return True

    def _purge_stale_sidecars(self, keep: set[str]) -> None:
        try:
            names = os.listdir(self.bm25_dir)
        except OSError:
            return
        for name in names:
            if not name.endswith(".json"):
                continue
            key = name[:-5]
            if key in keep:
                continue
            path = os.path.join(self.bm25_dir, name)
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("[TTSERail] bm25 sidecar cleanup failed at %s: %s", path, exc)

    def upsert_bm25(self, record: Dict[str, Any], track: str, category: str) -> None:
        record_id = ensure_id(record, track)
        text = str(record.get("text") or "")
        self._load_bm25(category, track).add_document(record_id, text)
        self._load_bm25(None, track).add_document(record_id, text)
        self._dirty.add(self._key(category, track))
        self._dirty.add(self._key(None, track))
        self.persist()

    def delete_bm25(self, record: Dict[str, Any], track: str, category: str) -> None:
        record_id = ensure_id(record, track)
        if self._load_bm25(category, track).remove_document(record_id):
            self._dirty.add(self._key(category, track))
        if self._load_bm25(None, track).remove_document(record_id):
            self._dirty.add(self._key(None, track))
        self.persist()

    def persist(self) -> None:
        if not self._dirty:
            return
        os.makedirs(self.bm25_dir, exist_ok=True)
        saved: List[str] = []
        for key in list(self._dirty):
            index = self._bm25.get(key)
            if index is None:
                continue
            path = os.path.join(self.bm25_dir, f"{key}.json")
            tmp = f"{path}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(index.to_dict(), handle, ensure_ascii=False)
                os.replace(tmp, path)
                self._dirty.discard(key)
                saved.append(f"{key}:{index.doc_count}")
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("[TTSERail] bm25 save failed at %s: %s", path, exc)
        if saved:
            logger.info("[TTSERail] bm25 persisted dir=%s files=%s", self.bm25_dir, ",".join(saved))

    def align(self, facts: Sequence[Dict[str, Any]], tips: Sequence[Dict[str, Any]], category_of) -> None:
        expected = self._expected_bm25_docs(facts, tips, category_of)
        if self._bm25_matches_bank(expected):
            logger.info(
                "[TTSERail] bm25 already aligned facts=%s tips=%s dir=%s",
                len(facts),
                len(tips),
                self.bm25_dir,
            )
            return
        self._chroma_stale = True
        grouped: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
        all_facts: List[Tuple[str, str]] = []
        all_tips: List[Tuple[str, str]] = []
        for record in facts:
            pair = (ensure_id(record, "fact"), str(record.get("text") or ""))
            grouped[(str(category_of(record)), "fact")].append(pair)
            all_facts.append(pair)
        for record in tips:
            pair = (ensure_id(record, "tip"), str(record.get("text") or ""))
            grouped[(str(category_of(record)), "tip")].append(pair)
            all_tips.append(pair)
        self._bm25.clear()
        self._dirty.clear()
        for (category, track), pairs in grouped.items():
            index = _BM25Index()
            for doc_id, text in pairs:
                index.add_document(doc_id, text)
            key = self._key(category, track)
            self._bm25[key] = index
            self._dirty.add(key)
        for track, pairs in (("fact", all_facts), ("tip", all_tips)):
            index = _BM25Index()
            for doc_id, text in pairs:
                index.add_document(doc_id, text)
            key = self._key(None, track)
            self._bm25[key] = index
            self._dirty.add(key)
        self.persist()
        self._purge_stale_sidecars(set(self._bm25))
        logger.info(
            "[TTSERail] bm25 rebuilt facts=%s tips=%s keys=%s",
            len(facts),
            len(tips),
            ",".join(sorted(self._bm25)),
        )

    def _read_fingerprint(self) -> str:
        path = os.path.join(self.chroma_path, _FINGERPRINT_FILE)
        try:
            with open(path, encoding="utf-8") as handle:
                return str((json.load(handle) or {}).get("fingerprint") or "")
        except (OSError, ValueError):
            return ""

    def _write_fingerprint(self, fingerprint: str) -> None:
        os.makedirs(self.chroma_path, exist_ok=True)
        path = os.path.join(self.chroma_path, _FINGERPRINT_FILE)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"fingerprint": fingerprint}, handle)
            os.replace(tmp, path)
            self._chroma_fingerprint = fingerprint
        except OSError as exc:
            logger.warning("[TTSERail] chroma fingerprint save failed: %s", exc)

    def note_embedding(self, embedding: Any) -> None:
        """Mark ANN stale when a provider appears or its fingerprint changes."""
        expected = _fingerprint(embedding)
        if expected and expected != self._chroma_fingerprint:
            logger.info(
                "[TTSERail] chroma marked stale have=%s expected=%s",
                self._chroma_fingerprint or "(none)",
                expected,
            )
            self._chroma_stale = True

    async def ensure_chroma(self, store: Any) -> None:
        """Rebuild ANN when embedding was attached late or the model changed."""
        expected = _fingerprint(getattr(store, "_embedding", None))
        if not expected:
            return
        if not self._chroma_stale and expected == self._chroma_fingerprint:
            return
        async with self._chroma_lock:
            expected = _fingerprint(getattr(store, "_embedding", None))
            if not expected:
                return
            if not self._chroma_stale and expected == self._chroma_fingerprint:
                return
            await self._rebuild_chroma(store)

    async def rebuild_chroma(self, store: Any) -> None:
        """Wipe the Chroma collection and batch-add the current bank."""
        async with self._chroma_lock:
            await self._rebuild_chroma(store)

    @staticmethod
    def _iter_bank(store: Any) -> List[Tuple[Dict[str, Any], str, str]]:
        category_of = getattr(store, "record_category", lambda _record: "other")
        items: List[Tuple[Dict[str, Any], str, str]] = []
        for record in list(getattr(store, "facts", None) or []):
            items.append((record, "fact", str(category_of(record) or "other")))
        for record in list(getattr(store, "tips", None) or []):
            items.append((record, "tip", str(category_of(record) or "other")))
        return items

    async def _rebuild_chroma(self, store: Any) -> None:
        embedding = getattr(store, "_embedding", None)
        expected = _fingerprint(embedding)
        if not expected:
            self._chroma_stale = False
            return
        chroma = self._chroma_store()
        if chroma is None:
            return
        try:
            await chroma.delete_table(_COLLECTION)
        except Exception:  # noqa: BLE001
            pass
        self._chroma = None
        chroma = self._chroma_store()
        if chroma is None:
            return
        items = self._iter_bank(store)
        texts = [str(record.get("text") or "") for record, _track_name, _category in items]
        fill = getattr(store, "_fill_embedding_cache", None)
        if callable(fill):
            try:
                await fill(texts)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TTSERail] chroma rebuild embed failed: %s", exc)
        cached = getattr(store, "_cached_embedding", None)
        embed = getattr(store, "embedding_of", None)
        payload: List[Dict[str, Any]] = []
        for record, track, category in items:
            text = str(record.get("text") or "")
            vector = cached(_norm(text)) if callable(cached) else None
            if vector is None and callable(embed):
                try:
                    vector = await embed(text)
                except Exception:  # noqa: BLE001
                    vector = None
            if not vector:
                continue
            record_id = ensure_id(record, track)
            payload.append(
                {
                    "id": record_id,
                    "content": text,
                    "embedding": list(vector),
                    "document_id": record_id,
                    "metadata": {"category": str(category or "other"), "track": _track(track)},
                }
            )
        complete = len(payload) == len(items)
        try:
            if payload:
                await chroma.add(payload)
            if complete:
                self._write_fingerprint(expected)
                self._chroma_stale = False
            else:
                self._chroma_stale = True
                logger.warning(
                    "[TTSERail] chroma rebuilt incomplete records=%s/%s; fingerprint not written",
                    len(payload),
                    len(items),
                )
            logger.info(
                "[TTSERail] chroma rebuilt fingerprint=%s records=%s/%s complete=%s",
                expected if complete else "(skipped)",
                len(payload),
                len(items),
                complete,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] chroma rebuild add failed: %s", exc)
            self._chroma_stale = True
            self._mark_chroma_transient(exc)

    def _mark_chroma_unavailable(self, exc: BaseException) -> None:
        self._chroma = None
        self._chroma_failed = True
        logger.warning("[TTSERail] chroma unavailable: %s", exc)

    def _mark_chroma_transient(self, exc: BaseException) -> None:
        self._chroma = None
        self._chroma_retry_at = time.monotonic() + _CHROMA_RETRY_SECS
        logger.warning(
            "[TTSERail] chroma transient failure; retry in %.0fs: %s",
            _CHROMA_RETRY_SECS,
            exc,
        )

    def _chroma_store(self) -> Any:
        if self._chroma_failed:
            return None
        if time.monotonic() < self._chroma_retry_at:
            return None
        if self._chroma is not None:
            return self._chroma
        try:
            import chromadb  # noqa: F401
            from openjiuwen.core.retrieval.common.config import StoreType, VectorStoreConfig
            from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
        except ImportError as exc:
            self._mark_chroma_unavailable(exc)
            return None
        except Exception as exc:  # noqa: BLE001
            self._mark_chroma_transient(exc)
            return None
        try:
            os.makedirs(self.chroma_path, exist_ok=True)
            config = VectorStoreConfig(
                store_provider=StoreType.Chroma,
                collection_name=_COLLECTION,
                distance_metric="cosine",
            )
            self._chroma = ChromaVectorStore(
                config=config,
                chroma_path=self.chroma_path,
                text_field="content",
                doc_id_field="document_id",
            )
            return self._chroma
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] chroma open failed: %s", exc)
            self._mark_chroma_transient(exc)
            return None

    async def _upsert_vector(
        self,
        record: Dict[str, Any],
        track: str,
        category: str,
        vector: Optional[List[float]],
        embedding: Any,
    ) -> None:
        store = self._chroma_store()
        if store is None or not vector:
            return
        expected = _fingerprint(embedding)
        fingerprint_changed = bool(
            expected and self._chroma_fingerprint and self._chroma_fingerprint != expected
        )
        if self._chroma_stale or fingerprint_changed:
            self._chroma_stale = True
            return
        record_id = ensure_id(record, track)
        try:
            try:
                await store.delete(ids=[record_id])
            except Exception:  # noqa: BLE001
                pass
            await store.add(
                [
                    {
                        "id": record_id,
                        "content": str(record.get("text") or ""),
                        "embedding": list(vector),
                        "document_id": record_id,
                        "metadata": {"category": str(category or "other"), "track": _track(track)},
                    }
                ]
            )
            if expected and not self._chroma_fingerprint:
                self._write_fingerprint(expected)
            logger.info(
                "[TTSERail] chroma upsert track=%s category=%s id=%s",
                _track(track),
                category or "other",
                record_id[:12],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] chroma upsert failed: %s", exc)
            self._mark_chroma_transient(exc)

    async def _delete_vector(self, record: Dict[str, Any], track: str) -> None:
        store = self._chroma_store()
        if store is None:
            return
        record_id = ensure_id(record, track)
        try:
            await store.delete(ids=[record_id])
            logger.info(
                "[TTSERail] chroma deleted track=%s id=%s",
                _track(track),
                record_id[:12],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] chroma delete failed: %s", exc)

    async def _search_ann(
        self,
        query_vector: List[float],
        *,
        track: str,
        category: Optional[str],
        top_k: int,
        embedding: Any,
    ) -> List[Tuple[str, float]]:
        store = self._chroma_store()
        if store is None:
            return []
        expected = _fingerprint(embedding)
        if expected and self._chroma_fingerprint and self._chroma_fingerprint != expected:
            return []
        filters: Dict[str, str] = {"track": _track(track)}
        if category:
            filters["category"] = str(category)
        try:
            results = await store.search(query_vector=query_vector, top_k=max(int(top_k), 1), filters=filters)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] chroma search failed: %s", exc)
            self._mark_chroma_transient(exc)
            return []
        scored: List[Tuple[str, float]] = []
        for item in results:
            doc_id = getattr(item, "id", None)
            if not doc_id:
                continue
            scored.append((str(doc_id), float(getattr(item, "score", 0.0) or 0.0)))
        return scored

    async def _vector_for(self, record: Dict[str, Any], store: Any) -> Optional[List[float]]:
        text = str(record.get("text") or "")
        cached = getattr(store, "_cached_embedding", None)
        vector = cached(_norm(text)) if callable(cached) else None
        if vector is None:
            embed = getattr(store, "embedding_of", None)
            if callable(embed):
                try:
                    vector = await embed(text)
                except Exception:  # noqa: BLE001
                    vector = None
        return vector

    async def upsert(self, record: Dict[str, Any], track: str, category: str, store: Any) -> None:
        self.upsert_bm25(record, track, category)
        await self.ensure_chroma(store)
        vector = await self._vector_for(record, store)
        await self._upsert_vector(record, track, category, vector, getattr(store, "_embedding", None))

    async def delete(self, record: Dict[str, Any], track: str, category: str, store: Any) -> None:
        self.delete_bm25(record, track, category)
        await self._delete_vector(record, track)

    async def move(self, record: Dict[str, Any], track: str, old_category: str, new_category: str, store: Any) -> None:
        record_id = ensure_id(record, track)
        text = str(record.get("text") or "")
        if self._load_bm25(old_category, track).remove_document(record_id):
            self._dirty.add(self._key(old_category, track))
        self._load_bm25(new_category, track).add_document(record_id, text)
        self._dirty.add(self._key(new_category, track))
        self.persist()
        await self.ensure_chroma(store)
        vector = await self._vector_for(record, store)
        if not vector:
            self._chroma_stale = True
            return
        await self._upsert_vector(
            record,
            track,
            new_category,
            vector,
            getattr(store, "_embedding", None),
        )

    def score_bm25(self, query: str, *, track: str, category: Optional[str] = None) -> Dict[str, float]:
        return self._load_bm25(category, track).score(query)

    async def rank_track(
        self,
        store: Any,
        records: Sequence[Dict[str, Any]],
        query: str,
        *,
        top_k: int,
        rrf_k: int = DEFAULT_RRF_K,
        category: Optional[str] = None,
        track: str = "fact",
        retrieve_mode: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        pool = list(records or [])
        scope = category or "all"
        track_name = _track(track)
        prefer = normalize_consult_retrieve_mode(retrieve_mode)

        def _log(
            mode: str,
            reason: str,
            hits: Sequence[Dict[str, Any]],
            *,
            bm25_n: int = 0,
            embed_n: int = 0,
            bm25_top: str = "-",
            embed_top: str = "-",
            rrf_top: str = "-",
        ) -> None:
            logger.info(
                "[TTSERail] consult rank category=%s track=%s want=%s mode=%s reason=%s "
                "pool=%s hits=%s bm25=%s embed=%s query=%s",
                scope,
                track_name,
                prefer,
                mode,
                reason,
                len(pool),
                len(hits),
                bm25_n,
                embed_n,
                _clip(query),
            )
            if reason in {"empty_pool", "pool_le_topk"}:
                return
            logger.info(
                "[TTSERail] consult scores category=%s track=%s mode=%s bm25=[%s] embed=[%s] rrf=[%s]",
                scope,
                track_name,
                mode,
                bm25_top,
                embed_top,
                rrf_top,
            )

        if not pool:
            _log("dump", "empty_pool", [])
            return [], "dump"
        if len(pool) <= top_k:
            _log("dump", "pool_le_topk", pool)
            return pool, "dump"
        by_id = {ensure_id(record, track): record for record in pool}
        want_embed = prefer in {"hybrid", "embed"}
        bm25_scores: Dict[str, float] = {}
        ranked_bm25 = sorted(
            self.score_bm25(query, track=track, category=category).items(),
            key=lambda item: -item[1],
        )
        for doc_id, score in ranked_bm25:
            if score > 0 and doc_id in by_id:
                bm25_scores[str(doc_id)] = float(score)
        bm25_rank = list(bm25_scores)
        query_vec = None
        embed = getattr(store, "embedding_of", None)
        has_provider = getattr(store, "has_embedding_provider", lambda: False)
        can_embed = store is not None and callable(embed) and bool(has_provider())
        if want_embed and can_embed:
            try:
                query_vec = await embed(query)
            except Exception as exc:  # noqa: BLE001
                logger.warning('[TTSERail] consult query embedding failed: %s', exc)
                query_vec = None
        embed_rank: List[str] = []
        embed_scores: Dict[str, float] = {}
        if query_vec:
            raw_ann = await self._search_ann(
                list(query_vec),
                track=track,
                category=category,
                top_k=max(top_k * 4, 16),
                embedding=getattr(store, '_embedding', None),
            )
            embed_rank, embed_scores = _as_scored(raw_ann)
            embed_rank = [doc_id for doc_id in embed_rank if doc_id in by_id]
            embed_scores = {doc_id: embed_scores[doc_id] for doc_id in embed_rank}
        preview_kw = {'by_id': by_id, 'limit': max(int(top_k), 8)}
        bm25_top = _ranked_preview(bm25_rank, bm25_scores, **preview_kw)
        embed_top = _ranked_preview(embed_rank, embed_scores, **preview_kw)
        if prefer == "embed":
            if embed_rank:
                hits = _pick_ids(by_id, embed_rank, top_k)
                _log(
                    'embed',
                    'config',
                    hits,
                    embed_n=len(embed_rank),
                    bm25_n=len(bm25_rank),
                    embed_top=embed_top,
                    bm25_top=bm25_top,
                )
                return hits, 'embed'
            if bm25_rank:
                hits = _pick_ids(by_id, bm25_rank, top_k)
                _log(
                    'bm25',
                    'embed_empty',
                    hits,
                    bm25_n=len(bm25_rank),
                    embed_n=len(embed_rank),
                    bm25_top=bm25_top,
                    embed_top=embed_top,
                )
                return hits, 'bm25'
            hits = pool[:top_k]
            _log('dump', 'no_scores', hits, embed_top=embed_top, bm25_top=bm25_top)
            return hits, 'dump'
        if prefer == "bm25":
            if bm25_rank:
                hits = _pick_ids(by_id, bm25_rank, top_k)
                _log(
                    'bm25',
                    'config',
                    hits,
                    bm25_n=len(bm25_rank),
                    bm25_top=bm25_top,
                )
                return hits, 'bm25'
            hits = pool[:top_k]
            _log('dump', 'no_scores', hits, bm25_top=bm25_top)
            return hits, 'dump'
        if bm25_rank and embed_rank:
            rrf_order, rrf_scores = _rrf_scored([bm25_rank, embed_rank], rrf_k)
            hits = _pick_ids(by_id, rrf_order, top_k)
            _log(
                'hybrid',
                'rrf',
                hits,
                bm25_n=len(bm25_rank),
                embed_n=len(embed_rank),
                bm25_top=bm25_top,
                embed_top=embed_top,
                rrf_top=_ranked_preview(rrf_order, rrf_scores, **preview_kw),
            )
            return hits, 'hybrid'
        if embed_rank and not bm25_rank:
            hits = _pick_ids(by_id, embed_rank, top_k)
            _log(
                'embed',
                'bm25_empty',
                hits,
                embed_n=len(embed_rank),
                embed_top=embed_top,
            )
            return hits, 'embed'
        if bm25_rank:
            hits = _pick_ids(by_id, bm25_rank, top_k)
            _log(
                'bm25',
                'embed_empty',
                hits,
                bm25_n=len(bm25_rank),
                embed_n=len(embed_rank),
                bm25_top=bm25_top,
                embed_top=embed_top,
            )
            return hits, 'bm25'
        hits = pool[:top_k]
        _log('dump', 'no_scores', hits, bm25_top=bm25_top, embed_top=embed_top)
        return hits, 'dump'

    async def retrieve(
        self,
        store: Any,
        *,
        query: str,
        category: Optional[str] = None,
        top_k: int,
        rrf_k: int = DEFAULT_RRF_K,
        retrieve_mode: Optional[str] = None,
    ) -> RetrieveRulesResult:
        facts, tips = await store.consult_pool(category)
        cleaned = str(query or "").strip()
        scope = category or "all"
        if not cleaned:
            logger.info(
                "[TTSERail] consult retrieve category=%s fact_mode=dump tip_mode=dump "
                "reason=empty_query fact=%s/%s tip=%s/%s",
                scope,
                len(facts),
                len(facts),
                len(tips),
                len(tips),
            )
            return RetrieveRulesResult(facts=facts, tips=tips, fact_mode="dump", tip_mode="dump")
        prefer = normalize_consult_retrieve_mode(
            retrieve_mode
            if retrieve_mode is not None
            else getattr(getattr(store, "_config", None), "consult_retrieve_mode", None)
        )
        if prefer != "bm25":
            await self.ensure_chroma(store)
        fact_hits, fact_mode = await self.rank_track(
            store,
            facts,
            cleaned,
            top_k=top_k,
            rrf_k=rrf_k,
            category=category,
            track="fact",
            retrieve_mode=prefer,
        )
        tip_hits, tip_mode = await self.rank_track(
            store,
            tips,
            cleaned,
            top_k=top_k,
            rrf_k=rrf_k,
            category=category,
            track="tip",
            retrieve_mode=prefer,
        )
        logger.info(
            "[TTSERail] consult retrieve category=%s fact_mode=%s fact=%s/%s "
            "tip_mode=%s tip=%s/%s query=%s",
            scope,
            fact_mode,
            len(fact_hits),
            len(facts),
            tip_mode,
            len(tip_hits),
            len(tips),
            _clip(cleaned),
        )
        return RetrieveRulesResult(
            facts=fact_hits, tips=tip_hits, fact_mode=fact_mode, tip_mode=tip_mode
        )


def _rrf_scored(ranked_lists: Sequence[Sequence[str]], k: int) -> Tuple[List[str], Dict[str, float]]:
    rrf_k = k if isinstance(k, int) and k > 0 else DEFAULT_RRF_K
    scores: Dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked, start=1):
            scores[key] += 1.0 / (rrf_k + rank)
    ordered = [key for key, _ in sorted(scores.items(), key=lambda item: -item[1])]
    return ordered, dict(scores)


def _rrf(ranked_lists: Sequence[Sequence[str]], k: int) -> List[str]:
    ordered, _scores = _rrf_scored(ranked_lists, k)
    return ordered


def _pick_ids(by_id: Dict[str, Dict[str, Any]], ordered: Sequence[str], top_k: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for record_id in ordered:
        if record_id in seen or record_id not in by_id:
            continue
        seen.add(record_id)
        out.append(by_id[record_id])
        if len(out) >= top_k:
            break
    return out


__all__ = ["TTSEIndex", "RetrieveRulesResult", "ensure_id", "make_id"]
