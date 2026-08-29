"""Deterministic lexical ranking for live Symphony catalog records."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Pattern, Sequence


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.#/-]*|[\u3400-\u9fff]+")


@dataclass(frozen=True)
class LexicalDocument:
    """One searchable record projected from a live Skill."""

    key: str
    name: str
    description: str
    body: str = ""


@dataclass(frozen=True)
class LexicalHit:
    """One stable ranked lexical match."""

    key: str
    score: float


@dataclass(frozen=True)
class _BM25Stats:
    average_length: float
    postings: dict[str, int]
    size: int


class LexicalIndex:
    """Reusable deterministic index for one immutable live inventory."""

    def __init__(self, documents: Sequence[LexicalDocument]) -> None:
        self._documents = {document.key: document for document in documents}
        self._search_text = {document.key: _search_text(document) for document in documents}
        self._tokens = {document.key: _tokens(_weighted_text(document)) for document in documents}
        self._field_tokens = {
            document.key: (
                set(_tokens(document.key)),
                set(_tokens(document.name)),
                set(_tokens(document.description)),
                set(_tokens(document.body)),
            )
            for document in documents
        }
        self._frequencies = {key: Counter(tokens) for key, tokens in self._tokens.items()}
        self._lengths = {key: len(tokens) for key, tokens in self._tokens.items()}
        postings: dict[str, int] = defaultdict(int)
        for tokens in self._tokens.values():
            for token in set(tokens):
                postings[token] += 1
        self._bm25 = _BM25Stats(
            average_length=sum(self._lengths.values()) / max(1, len(self._lengths)),
            postings=dict(postings),
            size=len(self._documents),
        )

    def search(
        self,
        query: str,
        *,
        keys: Iterable[str] | None = None,
        case_insensitive: bool = True,
        fixed_strings: bool = False,
    ) -> tuple[LexicalHit, ...]:
        """Search a catalog scope without rebuilding corpus statistics."""

        text = str(query or "").strip()
        if not text:
            raise ValueError("query must be non-empty")
        matcher = compile_matcher(text, case_insensitive=case_insensitive, fixed_strings=fixed_strings)
        scope = (
            self._documents if keys is None else {key: self._documents[key] for key in keys if key in self._documents}
        )
        matched = [document for document in scope.values() if matcher(self._search_text[document.key])]
        if not matched:
            return ()
        query_tokens = _tokens(_ranking_query(text))
        scored: list[LexicalHit] = []
        for document in matched:
            score = _bm25_score(
                query_tokens,
                self._frequencies[document.key],
                self._lengths[document.key],
                self._bm25,
            )
            score += _field_score(
                query_tokens,
                field_tokens=self._field_tokens[document.key],
            )
            score += _phrase_score(document, text, case_insensitive=case_insensitive)
            scored.append(LexicalHit(document.key, score))
        return tuple(
            sorted(
                scored,
                key=lambda hit: (
                    _identity_tier(
                        self._documents[hit.key],
                        text,
                        case_insensitive=case_insensitive,
                        fixed_strings=fixed_strings,
                    ),
                    -hit.score,
                    hit.key.casefold(),
                    hit.key,
                ),
            )
        )


def compile_matcher(query: str, *, case_insensitive: bool, fixed_strings: bool):
    """Build the bounded literal-or-regex matcher used by Skill search."""

    if fixed_strings:
        needle = query.casefold() if case_insensitive else query

        def contains(value: str) -> bool:
            candidate = value.casefold() if case_insensitive else value
            return needle in candidate

        return contains
    expression = compile_safe_pattern(query, case_insensitive=case_insensitive)
    return lambda value: expression.search(value) is not None


def compile_safe_pattern(pattern: str, *, case_insensitive: bool = False) -> Pattern[str]:
    """Compile the bounded regex subset accepted by deterministic Skill search."""

    text = str(pattern or "")
    if not text or len(text) > 512:
        raise ValueError("search expression must contain 1-512 characters")
    _reject_unsafe_repetition(text)
    try:
        return re.compile(text, re.IGNORECASE if case_insensitive else 0)
    except (OverflowError, re.error) as exc:
        raise ValueError(f"invalid search expression: {exc}") from exc


def _reject_unsafe_repetition(pattern: str) -> None:
    frames: list[dict[str, bool]] = [{"repeat": False, "alternate": False, "complex": False}]
    escaped = False
    in_class = False
    previous = ""
    closed_complex = False
    repetition_count = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if escaped:
            if char.isdigit() and char != "0":
                raise ValueError("search expression backreferences are not supported")
            escaped = False
            previous = "atom"
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if in_class:
            if char == "]":
                in_class = False
                previous = "atom"
            index += 1
            continue
        if char == "[":
            in_class = True
            index += 1
            continue
        if char == "(":
            if index + 1 < len(pattern) and pattern[index + 1] == "?":
                raise ValueError("search expression extensions are not supported")
            frames.append({"repeat": False, "alternate": False, "complex": False})
            previous = "open"
            index += 1
            continue
        if char == ")":
            if len(frames) == 1:
                break
            frame = frames.pop()
            closed_complex = frame["repeat"] or frame["alternate"] or frame["complex"]
            frames[-1]["complex"] = frames[-1]["complex"] or closed_complex
            previous = "group"
            index += 1
            continue
        if char == "|":
            frames[-1]["alternate"] = True
            previous = "alternate"
            index += 1
            continue
        is_repeat = char in "*+?"
        if char == "{":
            closing = pattern.find("}", index + 1)
            is_repeat = closing != -1
            if is_repeat:
                bounds = pattern[slice(index + 1, closing)].split(",", 1)
                if any(bound and (not bound.isdigit() or int(bound) > 10_000) for bound in bounds):
                    raise ValueError("search expression repetition bound is too large")
                index = closing
        if is_repeat:
            repetition_count += 1
            if previous in {"", "open", "alternate", "repeat"} or (previous == "group" and closed_complex):
                raise ValueError("search expression contains unsafe repetition")
            if repetition_count > 1:
                raise ValueError("search expression contains multiple repetitions")
            frames[-1]["repeat"] = True
            frames[-1]["complex"] = True
            previous = "repeat"
            index += 1
            continue
        previous = "atom"
        closed_complex = False
        index += 1
    if pattern.count(".*") > 1:
        raise ValueError("search expression contains unsafe wildcard repetition")


def _identity_tier(
    document: LexicalDocument,
    query: str,
    *,
    case_insensitive: bool,
    fixed_strings: bool,
) -> int:
    values = (document.key, document.name)
    terms = (
        (query,)
        if fixed_strings
        else tuple(part.strip() for part in query.split("|") if part.strip() and re.fullmatch(r"[\w .+/#-]+", part))
    )
    if case_insensitive:
        values = tuple(value.casefold() for value in values)
        terms = tuple(term.casefold() for term in terms)
    if any(value == term for value in values for term in terms):
        return 0
    if any(value.startswith(term) for value in values for term in terms):
        return 1
    return 2


def _search_text(document: LexicalDocument) -> str:
    return "\n".join((document.key, document.name, document.description, document.body))


def _weighted_text(document: LexicalDocument) -> str:
    return "\n".join(
        (
            *([document.key] * 5),
            *([document.name] * 5),
            *([document.description] * 4),
            document.body,
        )
    )


def _ranking_query(query: str) -> str:
    return re.sub(r"[|()\[\]{}^$*?\\]", " ", query)


def _tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in _WORD_RE.findall(str(value or "")):
        folded = raw.casefold().strip("._-/")
        if not folded:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", folded):
            tokens.append(folded)
            tokens.extend(folded[slice(index, index + 2)] for index in range(max(0, len(folded) - 1)))
            continue
        tokens.extend(part for part in re.split(r"[_+.#/-]+", folded) if len(part) > 1 or part.isdigit())
    return tokens


def _bm25_score(
    query_tokens: Iterable[str],
    frequencies: Counter[str],
    length: int,
    stats: _BM25Stats,
) -> float:
    score = 0.0
    for token in dict.fromkeys(query_tokens):
        frequency = frequencies.get(token, 0)
        if not frequency:
            continue
        document_frequency = stats.postings.get(token, 0)
        inverse_frequency = math.log(1 + (stats.size - document_frequency + 0.5) / (document_frequency + 0.5))
        denominator = frequency + 1.5 * (0.28 + 0.72 * length / max(stats.average_length, 1e-9))
        score += inverse_frequency * (frequency * 2.5) / denominator
    return score


def _field_score(
    query_tokens: Sequence[str],
    *,
    field_tokens: Sequence[set[str]],
) -> float:
    terms = set(query_tokens)
    if not terms:
        return 0.0
    fields = tuple(zip(field_tokens, (5.0, 5.0, 3.4, 0.35)))
    score = 0.0
    for tokens, weight in fields:
        overlap = terms & tokens
        if overlap:
            score += weight * len(overlap) / len(terms)
    identity_terms = field_tokens[0] | field_tokens[1]
    identity_overlap = terms & identity_terms
    if identity_overlap:
        score += 16.0 * len(identity_overlap) / max(1, len(identity_terms))
    return score


def _phrase_score(document: LexicalDocument, query: str, *, case_insensitive: bool) -> float:
    alternatives = [part.strip() for part in query.split("|") if part.strip()]
    if not alternatives:
        return 0.0
    fields = (document.key, document.name, document.description, document.body)
    if case_insensitive:
        alternatives = [part.casefold() for part in alternatives]
        fields = tuple(field.casefold() for field in fields)
    score = 0.0
    for alternative in alternatives:
        if alternative in fields[0] or alternative in fields[1]:
            score += 12.0
        elif alternative in fields[2]:
            score += 5.0
        elif alternative in fields[3]:
            score += 0.5
    return score


__all__ = ["LexicalDocument", "LexicalHit", "LexicalIndex", "compile_matcher", "compile_safe_pattern"]
