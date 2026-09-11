"""Deterministic lexical ranking for live Symphony catalog records."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, NamedTuple, Pattern, Sequence

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.#/-]*|[\u3400-\u9fff]+")


@dataclass(frozen=True)
class LexicalDocument:
    """One searchable record projected from a live Skill."""

    key: str
    name: str
    description: str
    body: str = ""
    aliases: tuple[str, ...] = ()
    category: str = ""


class _DocumentFields(NamedTuple):
    """Normalized text fields in the order used by lexical scoring."""

    key: str
    name: str
    aliases: str
    description: str
    body: str
    category: str


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
    cjk_size: int


class LexicalIndex:
    """Reusable deterministic index for one immutable live inventory."""

    def __init__(self, documents: Sequence[LexicalDocument]) -> None:
        self._documents = {document.key: document for document in documents}
        self._search_text: dict[str, str] = {}
        self._field_tokens: dict[str, tuple[set[str], ...]] = {}
        self._frequencies: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        postings: Counter[str] = Counter()
        for key, document in self._documents.items():
            fields = _document_fields(document)
            tokens = tuple(_tokens(value) for value in fields)
            self._search_text[key] = "\n".join((*fields[:2], *document.aliases, *fields[3:]))
            self._field_tokens[key] = tuple(set(values) for values in tokens)
            # Reuse field tokens with the existing weights; category is scored separately.
            frequencies: Counter[str] = Counter()
            for values, weight in zip(tokens, (5, 5, 5, 4, 1)):
                for token, count in Counter(values).items():
                    frequencies[token] += count * weight
            self._frequencies[key] = frequencies
            self._lengths[key] = sum(frequencies.values())
            postings.update(frequencies.keys())
        self._bm25 = _BM25Stats(
            average_length=sum(self._lengths.values()) / max(1, len(self._lengths)),
            postings=dict(postings),
            size=len(self._documents),
            cjk_size=sum(any(not token.isascii() for token in values) for values in self._frequencies.values()),
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

    def search_terms(
        self,
        query: str,
        *,
        keys: Iterable[str] | None = None,
    ) -> tuple[LexicalHit, ...]:
        """Rank metadata that covers the natural-language query terms."""

        text = str(query or "").strip()
        if not text:
            raise ValueError("query must be non-empty")
        scope = (
            self._documents if keys is None else {key: self._documents[key] for key in keys if key in self._documents}
        )
        query_families = _query_term_families(text)
        query_tokens = tuple(dict.fromkeys(token for _, family in query_families for token in family))
        query_terms = set(query_tokens)
        matched = []
        for document in scope.values():
            fields = self._field_tokens[document.key]
            # A folder's description does not make every descendant Skill a content match.
            if any(not query_terms.isdisjoint(field) for field in fields[:-1]):
                matched.append(document)
            elif _identity_contains_query(document, text, query_tokens):
                matched.append(document)
        if not matched:
            return ()
        scored: list[LexicalHit] = []
        for document in matched:
            score = _bm25_family_score(
                query_families,
                self._frequencies[document.key],
                self._lengths[document.key],
                self._bm25,
            )
            score += _field_family_score(
                query_families,
                document=document,
                field_tokens=self._field_tokens[document.key],
            )
            score += _partial_identity_score(document, tuple(raw for raw, _ in query_families))
            score += _phrase_score(document, text, case_insensitive=True)
            scored.append(LexicalHit(document.key, score))
        return tuple(
            sorted(
                scored,
                key=lambda hit: (
                    _term_identity_tier(self._documents[hit.key], text),
                    -hit.score,
                    hit.key.casefold(),
                    hit.key,
                ),
            )
        )

    def missing_terms(self, query: str) -> tuple[str, ...]:
        """Return query terms absent from all indexed Skill content."""

        families = _query_term_families(str(query or "").strip())
        return tuple(raw for raw, family in families if not any(self._bm25.postings.get(token) for token in family))

    def match_snippet(
        self,
        key: str,
        queries: Sequence[str],
        *,
        visible_description_chars: int = 180,
        max_chars: int = 96,
    ) -> str:
        """Return evidence when the matching field is otherwise hidden."""

        document = self._documents.get(key)
        if document is None or max_chars < 1:
            return ""
        matched: set[str] = set()
        for query in queries:
            for label, values in (("name", (document.name,)), ("alias", document.aliases)):
                for value in values:
                    if value and _identity_value_matches_query(value, document.key, query):
                        return f"{label}: {_excerpt(value, 0, len(value), max_chars)}"
            present = tuple(dict.fromkeys(_tokens(query)))
            matched.update(_informative_terms(present, self._bm25))
        if not matched:
            return ""
        description = " ".join(document.description.split())
        hidden_description_terms = matched & self._field_tokens[key][3] - set(
            _tokens(description[:visible_description_chars])
        )
        if hidden_description_terms:
            return f"description: {_matched_excerpt(description, hidden_description_terms, max_chars)}"
        metadata_tokens = set().union(*self._field_tokens[key][:4])
        body_tokens = self._field_tokens[key][4]
        body_matches = matched & body_tokens - metadata_tokens
        if not body_matches:
            return ""
        body = _without_front_matter(document.body)
        return f"body: {_matched_excerpt(body, body_matches, max_chars)}"


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
    key = document.key
    name = document.name
    aliases = document.aliases
    terms = (
        (query,)
        if fixed_strings
        else tuple(part.strip() for part in query.split("|") if part.strip() and re.fullmatch(r"[\w .+/#-]+", part))
    )
    if case_insensitive:
        key = key.casefold()
        name = name.casefold()
        aliases = tuple(value.casefold() for value in aliases)
        terms = tuple(term.casefold() for term in terms)
    if any(key == term for term in terms):
        return 0
    if any(name == term for term in terms):
        return 1
    if any(alias == term for alias in aliases for term in terms):
        return 2
    if any(value.startswith(term) for value in (key, name, *aliases) for term in terms):
        return 3
    return 4


def _term_identity_tier(document: LexicalDocument, query: str) -> int:
    key = _normalize_identity(document.key)
    name = _normalize_identity(document.name)
    aliases = tuple(_normalize_identity(value) for value in document.aliases)
    term = _normalize_identity(query)
    if term == key:
        return 0
    if term == name:
        return 1
    if term in aliases:
        return 2
    identities = (key, name, *aliases)
    if len(term) >= 2 and any(term in value for value in identities):
        return 3
    query_words = set(term.split())
    # A name among task keywords is only a soft match, except single-letter
    # identifiers (e.g. R) which the lexical tokenizer does not retain.
    if any(len(identity) == 1 and identity in query_words for identity in identities):
        return 3
    return 4


def _normalize_identity(value: str) -> str:
    return " ".join(part for part in re.split(r"[\s_./-]+", value.casefold()) if part)


def _identity_value_matches_query(value: str, key: str, query: str) -> bool:
    identity = _normalize_identity(value)
    canonical = _normalize_identity(key)
    requested = _normalize_identity(query)
    if not identity or identity == canonical or not requested:
        return False
    if identity == requested or (len(identity) >= 2 and identity in requested):
        return True
    return len(requested) >= 2 and requested in identity


def _identity_contains_query(
    document: LexicalDocument,
    query: str,
    query_tokens: Sequence[str],
) -> bool:
    identities = tuple(_normalize_identity(value) for value in (document.key, document.name, *document.aliases))
    normalized = _normalize_identity(query)
    if normalized in identities:
        return True
    if len(normalized) >= 2 and any(normalized in value for value in identities):
        return True
    query_words = set(normalized.split())
    if any(len(identity) == 1 and identity in query_words for identity in identities):
        return True
    identity_tokens = {token for value in (document.key, document.name, *document.aliases) for token in _tokens(value)}
    return any(len(term) >= 4 and any(term in token for token in identity_tokens) for term in query_tokens)


def _partial_identity_score(document: LexicalDocument, query_tokens: Sequence[str]) -> float:
    identity_tokens = {token for value in (document.key, document.name, *document.aliases) for token in _tokens(value)}
    partial = {
        term
        for term in query_tokens
        if len(term) >= 4 and any(term != token and term in token for token in identity_tokens)
    }
    return 6.0 * len(partial) / max(1, len(set(query_tokens)))


def _informative_terms(terms: Sequence[str], stats: _BM25Stats) -> tuple[str, ...]:
    if not terms:
        return ()
    ordered = tuple(dict.fromkeys(terms))
    threshold = max(1, int(stats.size * 0.6))
    informative = tuple(term for term in ordered if stats.postings.get(term, 0) <= threshold)
    candidates = informative or (min(ordered, key=lambda term: stats.postings.get(term, stats.size + 1)),)
    return tuple(sorted(candidates, key=lambda term: (stats.postings.get(term, 0), ordered.index(term))))


def _without_front_matter(body: str) -> str:
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return body
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            content_start = index + 1
            return "".join(lines[content_start:])
    return body


def _excerpt(body: str, start: int, end: int, max_chars: int) -> str:
    half = max(1, (max_chars - (end - start)) // 2)
    left = max(0, start - half)
    right = min(len(body), end + half)
    text = " ".join(body[left:right].split())
    if left:
        text = f"…{text}"
    if right < len(body):
        text = f"{text}…"
    return text[:max_chars]


def _matched_excerpt(value: str, terms: set[str], max_chars: int) -> str:
    best, best_coverage = "", -1
    for occurrence in _WORD_RE.finditer(value):
        if terms.intersection(_tokens(occurrence.group())):
            candidate = _excerpt(value, occurrence.start(), occurrence.end(), max_chars)
            coverage = len(terms.intersection(_tokens(candidate)))
            if coverage > best_coverage:
                best, best_coverage = candidate, coverage
            if coverage == len(terms):
                break
    return best or _excerpt(value, 0, min(len(value), max_chars), max_chars)


def _document_fields(document: LexicalDocument) -> _DocumentFields:
    return _DocumentFields(
        key=document.key,
        name=document.name,
        aliases="\n".join(document.aliases),
        description=document.description,
        body=_without_front_matter(document.body),
        category=document.category,
    )


def _ranking_query(query: str) -> str:
    return re.sub(r"[|()\[\]{}^$*?\\]", " ", query)


def _tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for part in _surface_tokens(value):
        tokens.append(part)
        if not part.isascii():
            continue
        singular = _singular(part)
        if singular != part:
            tokens.append(singular)
        tokens.extend(form for form in _verb_forms(part) if form != part and form != singular)
    return tokens


def _surface_terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_surface_tokens(value)))


def _surface_tokens(value: str) -> list[str]:
    """Split text once without dropping repetitions needed by BM25."""

    terms: list[str] = []
    for raw in _WORD_RE.findall(str(value or "")):
        folded = raw.casefold().strip("._-/")
        if not folded:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", folded):
            terms.append(folded)
            terms.extend(folded[slice(index, index + 2)] for index in range(max(0, len(folded) - 1)))
            continue
        if re.fullmatch(r"[a-z0-9]+(?:\+\+|#)", folded):
            terms.append(folded)
            continue
        terms.extend(part for part in re.split(r"[_+.#/-]+", folded) if len(part) > 1 or part.isdigit())
    return terms


def _query_term_families(value: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    families: list[tuple[str, tuple[str, ...]]] = []
    for raw in _WORD_RE.findall(str(value or "")):
        terms = _surface_terms(raw)
        if not raw.isascii():
            # Overlapping CJK bigrams are parts of one query, not independent votes.
            families.append((raw, terms))
        else:
            families.extend((term, tuple(dict.fromkeys(_tokens(term)))) for term in terms)
    return tuple(dict.fromkeys(families))


def _singular(token: str) -> str:
    if len(token) <= 3 or token.endswith(("ss", "us", "is", "series", "species")):
        return token
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith(("ches", "shes", "xes", "zes", "sses")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


def _verb_forms(token: str) -> tuple[str, ...]:
    if len(token) <= 5:
        return ()
    if token.endswith("ing"):
        stem = token[:-3]
    elif token.endswith("ied"):
        return (f"{token[:-3]}y",)
    elif token.endswith("ed"):
        stem = token[:-2]
    else:
        return ()
    stems = [stem]
    if len(stem) > 2 and stem[-1] == stem[-2] and not stem.endswith("ss"):
        stems.append(stem[:-1])
    return tuple(dict.fromkeys((*stems, *(f"{value}e" for value in stems))))


def _bm25_score(
    query_tokens: Iterable[str],
    frequencies: Counter[str],
    length: int,
    stats: _BM25Stats,
) -> float:
    score = 0.0
    strongest = 0.0
    for token in dict.fromkeys(query_tokens):
        if not frequencies.get(token):
            continue
        contribution, information = _bm25_contribution(token, frequencies, length, stats)
        score += contribution
        strongest = max(strongest, information)
    return score + strongest


def _bm25_family_score(
    query_families: Sequence[tuple[str, Sequence[str]]],
    frequencies: Counter[str],
    length: int,
    stats: _BM25Stats,
) -> float:
    score = 0.0
    strongest = 0.0
    for raw, family in query_families:
        candidates = (raw,) if frequencies.get(raw) else family
        contributions = [
            _bm25_contribution(
                token,
                frequencies,
                length,
                stats,
                document_count=stats.size if token.isascii() else stats.cjk_size,
            )
            for token in candidates
            if frequencies.get(token)
        ]
        if not contributions:
            continue
        contribution, information = max(contributions)
        if not raw.isascii() and not frequencies.get(raw):
            coverage = sum(bool(frequencies.get(token)) for token in family[1:]) / max(1, len(family) - 1)
            contribution *= coverage
            information *= coverage
        score += contribution
        strongest = max(strongest, information)
    return score + strongest


def _bm25_contribution(
    token: str,
    frequencies: Counter[str],
    length: int,
    stats: _BM25Stats,
    *,
    document_count: int | None = None,
) -> tuple[float, float]:
    frequency = frequencies[token]
    document_frequency = stats.postings.get(token, 0)
    # English-only Skills cannot establish how distinctive a Chinese term is.
    population = stats.size if document_count is None else document_count
    inverse_frequency = math.log(1 + (population - document_frequency + 0.5) / (document_frequency + 0.5))
    denominator = frequency + 1.5 * (0.28 + 0.72 * length / max(stats.average_length, 1e-9))
    information = inverse_frequency * inverse_frequency
    return information * (frequency * 2.5) / denominator, information


def _field_score(
    query_tokens: Sequence[str],
    *,
    field_tokens: Sequence[set[str]],
) -> float:
    terms = set(query_tokens)
    if not terms:
        return 0.0
    fields = tuple(zip(field_tokens, (5.0, 5.0, 5.0, 3.4, 0.35, 0.1)))
    score = 0.0
    for tokens, weight in fields:
        overlap = terms & tokens
        if overlap:
            score += weight * len(overlap) / len(terms)
    identity_terms = field_tokens[0] | field_tokens[1] | field_tokens[2]
    identity_overlap = terms & identity_terms
    if identity_overlap:
        score += 16.0 * len(identity_overlap) / max(1, len(identity_terms))
    return score


def _field_family_score(
    query_families: Sequence[tuple[str, Sequence[str]]],
    *,
    document: LexicalDocument,
    field_tokens: Sequence[set[str]],
) -> float:
    if not query_families:
        return 0.0
    score = 0.0
    for tokens, weight in zip(field_tokens, (5.0, 5.0, 5.0, 3.4, 0.35, 0.1)):
        matched = sum(any(term in tokens for term in family) for _, family in query_families)
        score += weight * matched / len(query_families)
    identity_terms = field_tokens[0] | field_tokens[1] | field_tokens[2]
    identity_matches = sum(any(term in identity_terms for term in family) for _, family in query_families)
    identity_surface = _surface_terms("\n".join((document.key, document.name, *document.aliases)))
    score += 16.0 * identity_matches / max(1, len(identity_surface), len(query_families))
    return score


def _phrase_score(document: LexicalDocument, query: str, *, case_insensitive: bool) -> float:
    alternatives = [part.strip() for part in query.split("|") if part.strip()]
    if not alternatives:
        return 0.0
    fields: tuple[str, ...] = _document_fields(document)
    if case_insensitive:
        alternatives = [part.casefold() for part in alternatives]
        fields = tuple(field.casefold() for field in fields)
    score = 0.0
    for alternative in alternatives:
        if alternative in fields[0] or alternative in fields[1] or alternative in fields[2]:
            score += 12.0
        elif alternative in fields[3]:
            score += 5.0
        elif alternative in fields[4]:
            score += 0.5
        elif alternative in fields[5]:
            score += 0.1
    return score


__all__ = ["LexicalDocument", "LexicalHit", "LexicalIndex", "compile_matcher", "compile_safe_pattern"]
