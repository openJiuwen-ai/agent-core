# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retrieve the top-K most relevant FACTs/TIPs for a task query.

Ports the reference ``retriever.py`` logic onto the jiuwen embedding stack.
Embeddings come from (and are cached by) :class:`TTSERecordStore`, so retrieval
is free of redundant embedding work: induction already embedded every rule for
dedup, and the query is embedded once per invoke (cached by the rail).

When no embedding provider is configured, :func:`retrieve_top_k` returns
``None`` to signal the caller to fall back to whole-bank injection.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from openjiuwen.core.common.logging import logger

from .stores import TTSERecordStore, _cosine


async def _rank(
    records: List[dict],
    query_vec: List[float],
    store: TTSERecordStore,
    k: int,
) -> List[dict]:
    scored: List[Tuple[float, dict]] = []
    for record in records:
        vec = await store.embedding_of(record["text"])
        if vec is None:
            continue
        scored.append((_cosine(query_vec, vec), record))
    scored.sort(key=lambda pair: -pair[0])
    return [record for _, record in scored[: max(0, k)]]


async def retrieve_top_k(
    query: str,
    store: TTSERecordStore,
    *,
    k_facts: int,
    k_tips: int,
) -> Optional[Tuple[List[dict], List[dict]]]:
    """Return (top-K fact records, top-K tip records) for ``query``.

    Returns ``None`` when retrieval is unavailable (no embedding provider, empty
    query, or query embedding failure) so the caller falls back to whole-bank.
    """
    if not store.has_embedding_provider() or not (query or "").strip():
        return None
    try:
        query_vec = await store.embedding_of(query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTSE query embedding failed, falling back to whole bank: %s", exc)
        return None
    if query_vec is None:
        return None
    facts = await _rank(store.facts_records(), query_vec, store, k_facts)
    tips = await _rank(store.tips_records(), query_vec, store, k_tips)
    return facts, tips


__all__ = ["retrieve_top_k"]
