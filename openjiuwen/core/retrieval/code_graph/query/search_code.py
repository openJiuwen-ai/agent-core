# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic ``search_code`` over a ``CodeGraphIndex``.

Default ranking is Okapi BM25 over definition bodies plus metadata
(Retropus-style), with test paths filtered unless the caller opts in.
When a frozen ``LexicalIndex`` is present, queries reuse postings and
do not rescan source. In-memory indexes without lexical data fall back
to metadata-only BM25 so unit fixtures keep working.
"""

from __future__ import annotations

import math
from typing import Sequence

from openjiuwen.core.retrieval.code_graph.models import (
    SEARCHABLE_SYMBOL_KINDS,
    CodeGraphIndex,
    CodeMatch,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.lexical import (
    CORPUS_DEFINITION,
    BM25_B,
    BM25_K1,
    bm25_scores,
    tokenize,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path


def search_code(
    index: CodeGraphIndex,
    query: str,
    *,
    symbol_kinds: Sequence[str] | None = None,
    path_prefix: str | None = None,
    limit: int = 20,
    ban_tests: bool = True,
    backend: str = "bm25",
) -> list[CodeMatch]:
    """Rank definition-like symbols for ``query`` without an LLM."""
    needle = (query or "").strip()
    if not needle:
        return []
    kinds = {item.lower() for item in symbol_kinds} if symbol_kinds else None
    prefix = path_prefix.replace("\\", "/").lstrip("./") if path_prefix else None
    candidates: list[Symbol] = []
    for symbol in index.symbols.values():
        if symbol.kind not in SEARCHABLE_SYMBOL_KINDS:
            continue
        if kinds is not None and symbol.kind.value not in kinds:
            continue
        file_path = symbol.file.replace("\\", "/")
        if prefix and not file_path.startswith(prefix):
            continue
        if ban_tests and is_test_path(file_path):
            continue
        candidates.append(symbol)
    if not candidates:
        return []
    use_overlap = (backend or "bm25").lower() == "token_overlap"
    if not use_overlap and index.lexical is not None:
        ranked = _rank_lexical(index, candidates, needle)
        if ranked:
            return _exact_name_first(ranked, needle)[: max(1, limit)]
        return _exact_name_first(_rank_token_overlap(candidates, needle), needle)[
            : max(1, limit)
        ]
    ranked = (
        _rank_metadata_bm25(candidates, needle)
        if not use_overlap
        else _rank_token_overlap(candidates, needle)
    )
    return _exact_name_first(ranked, needle)[: max(1, limit)]


def definition_doc_id(symbol_id: str) -> str:
    return f"def:{symbol_id}"


def _exact_name_first(matches: list[CodeMatch], query: str) -> list[CodeMatch]:
    """Keep BM25 order except exact name / qualified hits jump to the front."""
    needle = query.strip().lower()
    if not needle or not matches:
        return matches
    exact: list[CodeMatch] = []
    rest: list[CodeMatch] = []
    for match in matches:
        name = str(match.name or "").lower()
        qualified = str(match.qualified_name or "").lower()
        if name == needle or qualified == needle or qualified.endswith("." + needle):
            exact.append(match)
        else:
            rest.append(match)
    return exact + rest if exact else matches


def _rank_lexical(index: CodeGraphIndex, symbols: list[Symbol], query: str) -> list[CodeMatch]:
    lexical = index.lexical
    if lexical is None:
        return []
    allowed = {definition_doc_id(symbol.symbol_id) for symbol in symbols}
    by_id = {symbol.symbol_id: symbol for symbol in symbols}
    scored: list[CodeMatch] = []
    for doc_id, score in bm25_scores(
        lexical, query, corpus=CORPUS_DEFINITION, allowed_docs=allowed
    ):
        document = lexical.documents.get(doc_id)
        if document is None:
            continue
        symbol = by_id.get(document.symbol_id)
        if symbol is None:
            continue
        total = score + _exact_match_bonus(symbol, query)
        if total <= 0:
            continue
        scored.append(symbol.to_match(total))
    scored.sort(key=lambda item: (-item.score, item.file, item.start_line, item.name))
    if scored:
        return scored
    return []


def _rank_metadata_bm25(symbols: list[Symbol], query: str) -> list[CodeMatch]:
    docs = [_document_tokens(symbol) for symbol in symbols]
    query_tokens = tokenize(query)
    if not query_tokens:
        return _rank_token_overlap(symbols, query)
    df: dict[str, int] = {}
    for tokens in docs:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    n_docs = len(docs)
    avgdl = sum(len(tokens) for tokens in docs) / max(1, n_docs)
    scored: list[CodeMatch] = []
    for symbol, tokens in zip(symbols, docs):
        tf: dict[str, int] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        score = _exact_match_bonus(symbol, query)
        doc_len = max(1, len(tokens))
        for token in query_tokens:
            freq = tf.get(token, 0)
            if freq <= 0:
                continue
            n_qi = df.get(token, 0)
            idf = math.log((n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
            denom = freq + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / max(1.0, avgdl))
            score += idf * (freq * (BM25_K1 + 1.0) / denom)
        if score <= 0:
            continue
        scored.append(symbol.to_match(score))
    scored.sort(key=lambda item: (-item.score, item.file, item.start_line, item.name))
    if scored:
        return scored
    return _rank_token_overlap(symbols, query)


def _rank_token_overlap(symbols: list[Symbol], query: str) -> list[CodeMatch]:
    query_tokens = set(tokenize(query))
    scored: list[CodeMatch] = []
    seen: set[str] = set()
    for symbol in symbols:
        score = _score_symbol(symbol, query, query_tokens)
        if score <= 0 or symbol.symbol_id in seen:
            continue
        seen.add(symbol.symbol_id)
        scored.append(symbol.to_match(score))
    scored.sort(key=lambda item: (-item.score, item.file, item.start_line, item.name))
    return scored


def _document_tokens(symbol: Symbol) -> list[str]:
    text = " ".join(
        part
        for part in (symbol.qualified_name, symbol.name, symbol.kind.value, symbol.file, symbol.signature)
        if part
    )
    return tokenize(text)


def _exact_match_bonus(symbol: Symbol, query: str) -> float:
    name = symbol.name
    qualified = symbol.qualified_name or name
    lower_query = query.lower()
    if qualified == query or name == query:
        return 10.0
    if qualified.lower() == lower_query or name.lower() == lower_query:
        return 8.0
    if qualified.endswith("." + query) or qualified.lower().endswith("." + lower_query):
        return 6.0
    return 0.0


def _score_symbol(symbol: Symbol, query: str, query_tokens: set[str]) -> float:
    name = symbol.name
    qualified = symbol.qualified_name or name
    lower_query = query.lower()
    if qualified == query or name == query:
        return 1.0
    if qualified.lower() == lower_query or name.lower() == lower_query:
        return 0.95
    if qualified.endswith("." + query) or qualified.lower().endswith("." + lower_query):
        return 0.9
    if lower_query in qualified.lower() or lower_query in name.lower():
        return 0.7
    name_tokens = set(tokenize(qualified))
    if not query_tokens or not name_tokens:
        return 0.0
    overlap = len(query_tokens & name_tokens) / len(query_tokens)
    if overlap <= 0:
        return 0.0
    return 0.25 + 0.4 * overlap
