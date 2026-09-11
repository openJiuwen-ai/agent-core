# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""BM25 over Markdown / RST / text chunks and definition bodies."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.query.lexical import (
    CORPUS_DEFINITION,
    CORPUS_TEXT,
    bm25_scores,
    tokenize,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path


def search_text(
    index: CodeGraphIndex,
    query: str,
    *,
    path_prefix: str | None = None,
    limit: int = 20,
    ban_tests: bool = True,
) -> list[dict[str, object]]:
    """Rank text chunks and definition bodies for a literal query.

    The text corpus is Markdown / RST. After an incremental refresh the same
    tokens often live only on definition documents, so a query that found a
    comment yesterday must still find the function body today.
    """
    needle = (query or "").strip()
    lexical = index.lexical
    if not needle or lexical is None:
        return []
    prefix = path_prefix.replace("\\", "/").lstrip("./") if path_prefix else None
    allowed: set[str] = set()
    for doc_id in (*lexical.text_ids, *lexical.definition_ids):
        document = lexical.documents.get(doc_id)
        if document is None:
            continue
        file_path = document.file.replace("\\", "/")
        if prefix and not file_path.startswith(prefix):
            continue
        if ban_tests and is_test_path(file_path):
            continue
        allowed.add(doc_id)
    if not allowed:
        return []
    hits: list[dict[str, object]] = []
    for doc_id, score in bm25_scores(lexical, needle, allowed_docs=allowed):
        document = lexical.documents.get(doc_id)
        if document is None:
            continue
        kind = "definition" if document.corpus == CORPUS_DEFINITION else "text_chunk"
        hit: dict[str, object] = {
            "doc_id": document.doc_id,
            "file": document.file,
            "start_line": document.start_line,
            "end_line": document.end_line,
            "score": score,
            "kind": kind,
        }
        if document.symbol_id:
            hit["symbol_id"] = document.symbol_id
            hit["name"] = document.name
        hits.append(hit)
        if len(hits) >= max(1, limit):
            break
    return hits


def corpus_query_stats(index: CodeGraphIndex, query: str) -> dict[str, object]:
    """Whether the lexical corpus even contains the query tokens.

    A NO_MATCH that does not say this looks like a miss when the corpus is
    empty, and like a real miss when the tokens are absent.
    """
    tokens = list(dict.fromkeys(tokenize(query)))
    lexical = index.lexical
    if lexical is None:
        return {
            "text_docs": 0,
            "definition_docs": 0,
            "tokens_present": [],
            "tokens_absent": tokens,
        }
    present = [token for token in tokens if lexical.df.get(token, 0) > 0]
    absent = [token for token in tokens if lexical.df.get(token, 0) <= 0]
    return {
        "text_docs": len(lexical.text_ids),
        "definition_docs": len(lexical.definition_ids),
        "tokens_present": present,
        "tokens_absent": absent,
    }


__all__ = ["CORPUS_DEFINITION", "CORPUS_TEXT", "corpus_query_stats", "search_text"]
