# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Self-normalized BM25 pairwise similarity for TTSE dedup / dream merge.

Reuses ``rank_bm25`` (same tokenizer as consult recall). Scores are divided by
the query's self-score and clamped to ``[0, 1]`` so a fixed threshold (default
``0.5``) can gate induction dedup and soft clustering when no embedding
provider is configured.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import (
    rank_bm25,
)

DEFAULT_BM25_SIM_THRESHOLD = 0.5
_EPS = 1e-9


def bm25_one_way_scores(query: str, documents: Sequence[str]) -> List[float]:
    """Self-normalized one-way BM25: ``score(q,d) / score(q,q)``, clamped to ``[0, 1]``."""
    docs = list(documents)
    if not docs:
        return []
    self_scores = rank_bm25(query, [query])
    self_score = float(self_scores[0]) if self_scores else 0.0
    if self_score <= _EPS:
        return [0.0] * len(docs)
    raw = rank_bm25(query, docs)
    out: List[float] = []
    for score in raw:
        sim = float(score) / self_score
        if sim < 0.0:
            sim = 0.0
        elif sim > 1.0:
            sim = 1.0
        out.append(sim)
    return out


def bm25_best_match(
    query: str,
    documents: Sequence[str],
    *,
    threshold: float = DEFAULT_BM25_SIM_THRESHOLD,
) -> Optional[int]:
    """Index of the best document with one-way BM25 sim >= ``threshold``, else None."""
    scores = bm25_one_way_scores(query, documents)
    if not scores:
        return None
    best_i: Optional[int] = None
    best_sim = float(threshold)
    for i, sim in enumerate(scores):
        if sim >= best_sim:
            best_i, best_sim = i, sim
    return best_i


def pairwise_bm25_sims(texts: Sequence[str]) -> List[Tuple[int, int, float]]:
    """Symmetric pairwise BM25 sims for ``i < j``: ``0.5 * (one_way(i,j) + one_way(j,i))``.

    Each direction uses a single-document corpus so IDF matches the
    self-normalized ``bm25_one_way_scores`` definition.
    """
    pool = [str(t or "") for t in texts]
    n = len(pool)
    out: List[Tuple[int, int, float]] = []
    if n < 2:
        return out
    for i in range(n):
        for j in range(i + 1, n):
            ij = bm25_one_way_scores(pool[i], [pool[j]])
            ji = bm25_one_way_scores(pool[j], [pool[i]])
            sim = 0.5 * ((ij[0] if ij else 0.0) + (ji[0] if ji else 0.0))
            out.append((i, j, sim))
    return out


__all__ = [
    "DEFAULT_BM25_SIM_THRESHOLD",
    "bm25_best_match",
    "bm25_one_way_scores",
    "pairwise_bm25_sims",
]
