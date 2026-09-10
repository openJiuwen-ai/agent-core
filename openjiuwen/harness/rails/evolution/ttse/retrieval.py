# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-bank hybrid recall for ``ttse_consult``.

Reuses project retrieval policy without standing up a VectorStore:

* BM25 over rule texts (``rank_bm25``, same tokenizer as compression recall)
* cosine against the TTSE embedding cache when a provider is configured
* Reciprocal Rank Fusion with ``k=60`` (same formula as ``rrf_fusion``)
* degrade: no/failed embedding → BM25; BM25 all-zero + vectors → cosine;
  both empty → count-sorted dump of the class
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import (
    rank_bm25,
)

from .stores import _cosine

RetrieveMode = Literal["hybrid", "bm25", "embed", "dump"]

DEFAULT_TOP_K = 8
DEFAULT_RRF_K = 60

_MODE_RANK = {"hybrid": 3, "embed": 2, "bm25": 1, "dump": 0}


@dataclass(frozen=True)
class RetrieveRulesResult:
    """FACT/TIP hits for one category after in-bank recall."""

    facts: List[Dict[str, Any]]
    tips: List[Dict[str, Any]]
    fact_mode: RetrieveMode
    tip_mode: RetrieveMode

    @property
    def mode(self) -> RetrieveMode:
        if _MODE_RANK.get(self.fact_mode, 0) >= _MODE_RANK.get(self.tip_mode, 0):
            return self.fact_mode
        return self.tip_mode


def clamp_top_k(
    value: Any,
    *,
    default: int = DEFAULT_TOP_K,
    max_rules: int = 40,
) -> int:
    """Parse and clamp ``top_k`` from a tool argument."""
    fallback = default if isinstance(default, int) and default > 0 else DEFAULT_TOP_K
    cap = max_rules if isinstance(max_rules, int) and max_rules > 0 else fallback
    parsed: Optional[int]
    try:
        if value is None or value == "":
            parsed = fallback
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    if parsed <= 0:
        parsed = fallback
    return min(parsed, cap)


def rrf_fuse_indices(
    ranked_lists: Sequence[Sequence[int]],
    *,
    k: int = DEFAULT_RRF_K,
) -> List[Tuple[int, float]]:
    """RRF over record indices: ``score += 1 / (k + rank)``.

    Same formula as ``openjiuwen.core.retrieval.utils.fusion.rrf_fusion``,
    keyed by index so duplicate rule texts cannot collide.
    """
    rrf_k = k if isinstance(k, int) and k > 0 else DEFAULT_RRF_K
    scores: Dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, idx in enumerate(ranked, start=1):
            scores[int(idx)] += 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: -item[1])


def _texts(records: Sequence[Dict[str, Any]]) -> List[str]:
    return [str(record.get("text") or "") for record in records]


def _positive_ranking(scores: Sequence[float]) -> List[int]:
    order = sorted(range(len(scores)), key=lambda i: -float(scores[i]))
    return [i for i in order if float(scores[i]) > 0.0]


async def _query_vector(store: Any, query: str) -> Optional[List[float]]:
    if store is None or not callable(getattr(store, "has_embedding_provider", None)):
        return None
    if not store.has_embedding_provider():
        return None
    embed = getattr(store, "embedding_of", None)
    if not callable(embed):
        return None
    try:
        vec = await embed(query)
    except Exception as exc:  # noqa: BLE001 - same degrade as dedup
        logger.warning("[TTSERail] consult query embedding failed: %s", exc)
        return None
    if not vec:
        return None
    return list(vec)


async def _embed_scores(
    store: Any,
    query_vec: List[float],
    records: Sequence[Dict[str, Any]],
) -> List[float]:
    embed = getattr(store, "embedding_of", None)
    scores: List[float] = []
    for record in records:
        vec = None
        if callable(embed):
            try:
                vec = await embed(str(record.get("text") or ""))
            except Exception:  # noqa: BLE001
                vec = None
        scores.append(_cosine(query_vec, vec) if vec else 0.0)
    return scores


def _pick(records: Sequence[Dict[str, Any]], indices: Sequence[int], top_k: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for idx in indices:
        if idx in seen or idx < 0 or idx >= len(records):
            continue
        seen.add(idx)
        out.append(records[idx])
        if len(out) >= top_k:
            break
    return out


async def retrieve_track(
    store: Any,
    records: Sequence[Dict[str, Any]],
    query: str,
    *,
    top_k: int,
    rrf_k: int = DEFAULT_RRF_K,
) -> Tuple[List[Dict[str, Any]], RetrieveMode]:
    """Rank one FACT or TIP list. Empty / tiny classes dump without scoring."""
    pool = list(records or [])
    if not pool:
        return [], "dump"
    if len(pool) <= top_k:
        return pool, "dump"

    bm25_scores = rank_bm25(query, _texts(pool))
    bm25_rank = _positive_ranking(bm25_scores)
    query_vec = await _query_vector(store, query)
    embed_scores: Optional[List[float]] = None
    embed_rank: List[int] = []
    if query_vec is not None:
        embed_scores = await _embed_scores(store, query_vec, pool)
        embed_rank = _positive_ranking(embed_scores)

    if bm25_rank and embed_rank:
        fused = rrf_fuse_indices([bm25_rank, embed_rank], k=rrf_k)
        return _pick(pool, [idx for idx, _ in fused], top_k), "hybrid"
    if embed_rank and not bm25_rank:
        return _pick(pool, embed_rank, top_k), "embed"
    if bm25_rank:
        return _pick(pool, bm25_rank, top_k), "bm25"
    return pool[:top_k], "dump"


async def retrieve_rules(
    store: Any,
    *,
    category: str,
    query: str,
    top_k: int,
    rrf_k: int = DEFAULT_RRF_K,
) -> RetrieveRulesResult:
    """Hybrid/BM25 recall inside one closed-set category.

    FACT and TIP are ranked separately, each clipped to ``top_k``.
    """
    facts: List[Dict[str, Any]] = []
    tips: List[Dict[str, Any]] = []
    if store is not None and callable(getattr(store, "records_for_category", None)):
        raw_facts, raw_tips = store.records_for_category(category)
        facts = list(raw_facts or [])
        tips = list(raw_tips or [])
    cleaned = str(query or "").strip()
    if not cleaned:
        return RetrieveRulesResult(facts=facts, tips=tips, fact_mode="dump", tip_mode="dump")

    fact_hits, fact_mode = await retrieve_track(
        store, facts, cleaned, top_k=top_k, rrf_k=rrf_k
    )
    tip_hits, tip_mode = await retrieve_track(
        store, tips, cleaned, top_k=top_k, rrf_k=rrf_k
    )
    return RetrieveRulesResult(
        facts=fact_hits,
        tips=tip_hits,
        fact_mode=fact_mode,
        tip_mode=tip_mode,
    )


__all__ = [
    "DEFAULT_RRF_K",
    "DEFAULT_TOP_K",
    "RetrieveMode",
    "RetrieveRulesResult",
    "clamp_top_k",
    "retrieve_rules",
    "retrieve_track",
    "rrf_fuse_indices",
]
