"""Small dependency-free BM25 implementation for compression recall."""

from __future__ import annotations

import math
import re
from collections import Counter

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+", flags=re.IGNORECASE)
_CJK_SEQUENCE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_YAML_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", flags=re.DOTALL)
_MARKDOWN_FENCE_RE = re.compile(r"^\s*```[^\n]*$", flags=re.MULTILINE)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", flags=re.MULTILINE)
_MARKDOWN_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+", flags=re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")


def normalize_markdown(text: str) -> str:
    """Remove Markdown-only syntax while retaining visible and code content."""
    normalized = _YAML_FRONTMATTER_RE.sub("", text or "")
    normalized = _MARKDOWN_FENCE_RE.sub("", normalized)
    normalized = _MARKDOWN_LINK_RE.sub(r"\1 \2", normalized)
    normalized = _MARKDOWN_HEADING_RE.sub("", normalized)
    normalized = _MARKDOWN_LIST_RE.sub("", normalized)
    return " ".join(normalized.split())


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese, English, paths, and code identifiers."""
    lowered = (text or "").lower()
    tokens = _ASCII_TOKEN_RE.findall(lowered)
    expanded: list[str] = list(tokens)
    for token in tokens:
        if "_" in token:
            expanded.extend(part for part in token.split("_") if part)

    for sequence in _CJK_SEQUENCE_RE.findall(lowered):
        expanded.extend(sequence)
        expanded.extend("".join(pair) for pair in zip(sequence, sequence[1:]))
    return expanded


class BM25Index:
    """Immutable in-memory BM25 index over a fixed document collection."""

    def __init__(
        self,
        tokenized_documents: list[list[str]],
        document_frequencies: Counter[str],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._tokenized_documents = tuple(tuple(document) for document in tokenized_documents)
        self._document_frequencies = Counter(document_frequencies)
        self._document_count = len(self._tokenized_documents)
        self._average_length = (
            sum(len(document) for document in self._tokenized_documents) / self._document_count
            if self._document_count
            else 0.0
        )
        self._k1 = k1
        self._b = b

    @classmethod
    def build(
        cls,
        documents: list[str],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25Index":
        tokenized_documents = [tokenize(document) for document in documents]
        document_frequencies: Counter[str] = Counter()
        for document in tokenized_documents:
            document_frequencies.update(set(document))
        return cls(
            tokenized_documents,
            document_frequencies,
            k1=k1,
            b=b,
        )

    @property
    def document_count(self) -> int:
        return self._document_count

    def scores(self, query: str) -> list[float]:
        """Return one BM25 score for each indexed document."""
        if not self._document_count:
            return []
        query_tokens = tokenize(query)
        if not query_tokens or self._average_length <= 0:
            return [0.0] * self._document_count

        scores: list[float] = []
        for document in self._tokenized_documents:
            frequencies = Counter(document)
            document_length = len(document)
            score = 0.0
            for token in set(query_tokens):
                frequency = frequencies.get(token, 0)
                if frequency <= 0:
                    continue
                document_frequency = self._document_frequencies[token]
                inverse_document_frequency = math.log(
                    1.0
                    + (self._document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + self._k1 * (
                    1.0 - self._b + self._b * document_length / self._average_length
                )
                score += inverse_document_frequency * frequency * (self._k1 + 1.0) / denominator
            scores.append(score)
        return scores


def rank_bm25(
    query: str, documents: list[str], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Return one positive-IDF BM25 score for each document."""
    return BM25Index.build(documents, k1=k1, b=b).scores(query)
