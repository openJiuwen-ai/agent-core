# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reusable BM25 postings over definition bodies and text chunks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from openjiuwen.core.retrieval.code_graph.models import LEXICAL_TOKENIZER_VERSION

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

BM25_K1 = 1.5
BM25_B = 0.75
TOKENIZER_VERSION = LEXICAL_TOKENIZER_VERSION

CORPUS_DEFINITION = "definition"
CORPUS_TEXT = "text"


@dataclass(frozen=True)
class LexicalDocument:
    """Index-internal retrieval document. Does not store source text."""

    doc_id: str
    corpus: str
    file: str
    start_line: int
    end_line: int
    symbol_id: str = ""
    name: str = ""
    kind: str = ""
    qualified_name: str = ""


@dataclass
class LexicalIndex:
    """Frozen BM25 statistics for one repository snapshot."""

    documents: dict[str, LexicalDocument] = field(default_factory=dict)
    doc_length: dict[str, int] = field(default_factory=dict)
    df: dict[str, int] = field(default_factory=dict)
    postings: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    avgdl: float = 0.0
    n_docs: int = 0
    definition_ids: tuple[str, ...] = ()
    text_ids: tuple[str, ...] = ()
    tokenizer: str = TOKENIZER_VERSION


class LexicalIndexBuilder:
    """Accumulate tokenized documents, then freeze postings once."""

    def __init__(self) -> None:
        self._documents: dict[str, LexicalDocument] = {}
        self._tokens: dict[str, list[str]] = {}

    def add(self, document: LexicalDocument, tokens: Sequence[str]) -> None:
        if not tokens:
            return
        self._documents[document.doc_id] = document
        self._tokens[document.doc_id] = list(tokens)

    def freeze(self) -> LexicalIndex:
        df: dict[str, int] = {}
        postings: dict[str, list[tuple[str, int]]] = {}
        doc_length: dict[str, int] = {}
        for doc_id, tokens in self._tokens.items():
            doc_length[doc_id] = len(tokens)
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
            for token, freq in tf.items():
                postings.setdefault(token, []).append((doc_id, freq))
        n_docs = len(self._documents)
        avgdl = (sum(doc_length.values()) / n_docs) if n_docs else 0.0
        definition_ids = tuple(
            doc_id for doc_id, doc in self._documents.items() if doc.corpus == CORPUS_DEFINITION
        )
        text_ids = tuple(doc_id for doc_id, doc in self._documents.items() if doc.corpus == CORPUS_TEXT)
        return LexicalIndex(
            documents=dict(self._documents),
            doc_length=doc_length,
            df=df,
            postings=postings,
            avgdl=avgdl,
            n_docs=n_docs,
            definition_ids=definition_ids,
            text_ids=text_ids,
            tokenizer=TOKENIZER_VERSION,
        )


def update_documents(
    index: LexicalIndex,
    *,
    dropped_files: Iterable[str],
    added: Sequence[tuple[LexicalDocument, Sequence[str]]],
) -> LexicalIndex:
    """Return a new index with ``dropped_files`` removed and ``added`` inserted.

    Functional on purpose: a repair session forks the base index by reference, so
    an in-place posting edit would rewrite what another session is scoring
    against. Copying the term dicts costs one pass over the vocabulary, which is
    far cheaper than re-tokenizing the repository.

    ``df[token]`` stays derivable as the length of its posting list, so it is
    recomputed for touched terms instead of being tracked separately.
    """
    dropped = set(dropped_files)
    documents = dict(index.documents)
    doc_length = dict(index.doc_length)
    stale_ids = {doc_id for doc_id, doc in documents.items() if doc.file in dropped}
    stale_ids.update(doc.doc_id for doc, _ in added if doc.doc_id in documents)
    postings = dict(index.postings)
    df = dict(index.df)
    if stale_ids:
        for doc_id in stale_ids:
            documents.pop(doc_id, None)
            doc_length.pop(doc_id, None)
        for token, posting in list(postings.items()):
            if not any(doc_id in stale_ids for doc_id, _ in posting):
                continue
            kept = [entry for entry in posting if entry[0] not in stale_ids]
            if kept:
                postings[token] = kept
                df[token] = len(kept)
            else:
                postings.pop(token, None)
                df.pop(token, None)
    for document, tokens in added:
        if not tokens:
            continue
        documents[document.doc_id] = document
        doc_length[document.doc_id] = len(tokens)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, freq in counts.items():
            posting = list(postings.get(token, ()))
            posting.append((document.doc_id, freq))
            postings[token] = posting
            df[token] = len(posting)
    n_docs = len(documents)
    return LexicalIndex(
        documents=documents,
        doc_length=doc_length,
        df=df,
        postings=postings,
        avgdl=(sum(doc_length.values()) / n_docs) if n_docs else 0.0,
        n_docs=n_docs,
        definition_ids=tuple(
            doc_id for doc_id, doc in documents.items() if doc.corpus == CORPUS_DEFINITION
        ),
        text_ids=tuple(doc_id for doc_id, doc in documents.items() if doc.corpus == CORPUS_TEXT),
        tokenizer=index.tokenizer,
    )


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _IDENT_RE.finditer(text or ""):
        ident = match.group(0)
        lower = ident.lower()
        tokens.append(lower)
        parts: list[str] = []
        for piece in ident.split("_"):
            camel = _CAMEL_RE.findall(piece) if piece else []
            parts.extend(camel or ([piece] if piece else []))
        if len(parts) > 1:
            tokens.extend(part.lower() for part in parts if part.lower() != lower)
    return tokens


def bm25_scores(
    index: LexicalIndex,
    query: str,
    *,
    corpus: str | None = None,
    allowed_docs: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """Score documents with frozen postings. Does not rescan source."""
    query_tokens = tokenize(query)
    if not query_tokens or index.n_docs <= 0:
        return []
    allowed = None if allowed_docs is None else set(allowed_docs)
    scores: dict[str, float] = {}
    n_docs = index.n_docs
    avgdl = index.avgdl or 1.0
    for token in query_tokens:
        posting = index.postings.get(token) or []
        n_qi = index.df.get(token, 0)
        if n_qi <= 0:
            continue
        idf = math.log((n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
        for doc_id, freq in posting:
            if allowed is not None and doc_id not in allowed:
                continue
            document = index.documents.get(doc_id)
            if document is None:
                continue
            if corpus is not None and document.corpus != corpus:
                continue
            doc_len = max(1, index.doc_length.get(doc_id, 1))
            denom = freq + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (freq * (BM25_K1 + 1.0) / denom)
    ranked = [(doc_id, score) for doc_id, score in scores.items() if score > 0]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked
