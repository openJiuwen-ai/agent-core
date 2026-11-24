"""Rank live capabilities using the descriptions in their published fingerprints."""

from __future__ import annotations

import logging
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from itertools import islice, zip_longest
from pathlib import Path
from typing import Any, TypeVar

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.discovery.models import sanitize_model_text
from openjiuwen.symphony.models import CapabilityDescriptor, CapabilityFingerprint
from openjiuwen.symphony.models._redaction import redact_sensitive_text
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex
from openjiuwen.symphony.shared.fingerprint import FingerprintArtifactStore, FingerprintService

_LOGGER = logging.getLogger(__name__)
_Match = TypeVar("_Match")
_ACTION_PREVIEW_SIZE = 3


@dataclass(frozen=True)
class CapabilityAction:
    """A reference to a currently available action owned by the host."""

    type: str
    id: str
    description: str = ""
    server_id: str = ""
    group: str | None = None


@dataclass(frozen=True)
class CapabilityCandidate:
    """One visible capability, optionally enriched by a fingerprint from its source."""

    descriptor: CapabilityDescriptor
    fingerprint: CapabilityFingerprint | None = None
    aliases: tuple[str, ...] = ()
    actions: tuple[CapabilityAction, ...] = ()
    manifest_path: Path | None = None


def read_fingerprints(
    reader: FingerprintArtifactStore | FingerprintService | None,
) -> dict[tuple[str, str], CapabilityFingerprint]:
    if reader is None:
        return {}
    try:
        artifact = reader.read()
    except (BaseError, OSError, ValueError) as exc:
        _LOGGER.debug("Symphony discovery is using live descriptions (%s)", type(exc).__name__)
        return {}
    return {(item.capability_type, item.capability_id): item for item in artifact.fingerprints}


def search_capabilities(
    candidates: Sequence[CapabilityCandidate],
    query: str,
    *,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Rank an authorization-filtered view and fold action matches into their services."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty")
    if type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError("top_k must be an integer from 1 to 50")
    return rank_capabilities(candidates, (query,))[:top_k]


def interleave_rankings(rankings: Iterable[Iterable[_Match]]) -> Iterator[_Match]:
    """Take one match from each ranking in order until all of them are exhausted."""
    for peers in zip_longest(*rankings):
        for match in peers:
            if match is not None:
                yield match


def _merge_rankings(rankings: Iterable[Sequence[_Match]], identity: Callable[[_Match], Hashable]) -> Iterator[_Match]:
    """Interleave independent rankings and yield each identity at its first turn."""
    seen: set[Hashable] = set()
    for match in interleave_rankings(rankings):
        key = identity(match)
        if key not in seen:
            seen.add(key)
            yield match


def rank_queries(
    queries: Sequence[str], rank: Callable[[str], Sequence[_Match]], identity: Callable[[_Match], Hashable],
) -> Iterator[_Match]:
    """Use the same round-robin search order for Skill, expert and MCP matches."""
    return _merge_rankings((rank(query) for query in queries), identity)


def rank_capabilities(candidates: Sequence[CapabilityCandidate], queries: str | Sequence[str]) -> list[dict[str, Any]]:
    """Rank each query against the same visible capabilities and merge their matched actions."""
    documents: list[LexicalDocument] = []
    by_key: dict[str, CapabilityCandidate] = {}
    names: dict[str, set[str]] = {}
    actions: dict[str, tuple[str, CapabilityAction]] = {}
    for candidate in candidates:
        descriptor = candidate.descriptor
        kind = descriptor.capability_type
        if not descriptor.available:
            continue
        key = f"{kind}:{descriptor.capability_id}"
        fingerprint = candidate.fingerprint
        if fingerprint is not None and (fingerprint.capability_type, fingerprint.capability_id) != (
            kind, descriptor.capability_id,
        ):
            fingerprint = None
        _, description = fingerprint_text(fingerprint, descriptor.content_hash, descriptor.description)
        by_key[key] = candidate
        names[key] = {name.casefold() for name in (descriptor.name, descriptor.capability_id, key, *candidate.aliases)}
        documents.append(LexicalDocument(
            key, descriptor.name, "\n".join((description, *descriptor.tags)), descriptor.semantic_content,
            (descriptor.capability_id, *candidate.aliases), descriptor.classification,
        ))
        for action_index, action in enumerate(candidate.actions):
            action_key = f"action:{key}:{action_index}"
            actions[action_key] = (key, action)
            documents.append(LexicalDocument(action_key, action.id, action.description))

    index = LexicalIndex(documents)
    action_rankings: dict[str, list[list[CapabilityAction]]] = {}

    def rank(query: str) -> tuple[tuple[str, str], ...]:
        exact = query.strip().casefold()
        matches: dict[str, str] = {}
        matched_actions: dict[str, list[CapabilityAction]] = {}
        for hit in index.search_terms(query):
            if hit.key in actions:
                key, action = actions[hit.key]
                matches.setdefault(key, "action")
                selected = matched_actions.setdefault(key, [])
                if len(selected) < _ACTION_PREVIEW_SIZE:
                    selected.append(action)
            else:
                matches.setdefault(hit.key, "name" if exact in names[hit.key] else "catalog")
        for key, group in matched_actions.items():
            action_rankings.setdefault(key, []).append(group)
        return tuple(matches.items())

    rows = []
    terms = (queries,) if isinstance(queries, str) else queries
    for key, reason in rank_queries(terms, rank, lambda item: item[0]):
        row = capability_row(by_key[key], matched_on=reason)
        if key in action_rankings:
            ranked_actions = _merge_rankings(action_rankings[key], lambda a: (a.type, a.id, a.server_id, a.group))
            limit = max(_ACTION_PREVIEW_SIZE, len(action_rankings[key]))
            row["actions"] = [_action_result(action) for action in islice(ranked_actions, limit)]
        rows.append(row)
    return rows


def capability_row(candidate: CapabilityCandidate, *, matched_on: str = "catalog") -> dict[str, Any]:
    descriptor = candidate.descriptor
    fingerprint = candidate.fingerprint
    if fingerprint is not None and (fingerprint.capability_type, fingerprint.capability_id) != (
        descriptor.capability_type, descriptor.capability_id,
    ):
        fingerprint = None
    summary, _ = fingerprint_text(fingerprint, descriptor.content_hash, descriptor.description)
    return {
        "type": descriptor.capability_type,
        "id": descriptor.capability_id,
        "name": sanitize_model_text(descriptor.name),
        "description": sanitize_model_text(summary)[:512],
        "matched_on": matched_on,
        "actions": [_action_result(action) for action in candidate.actions[:_ACTION_PREVIEW_SIZE]],
    }


def fingerprint_text(
    fingerprint: CapabilityFingerprint | None, content_hash: str, description: str,
) -> tuple[str, str]:
    """Return the display summary and searchable text of a matching source record."""
    if fingerprint is None or not content_hash or fingerprint.content_hash != content_hash:
        return description, description
    profile = fingerprint.semantic_profile
    summary = profile.summary or fingerprint.description or description
    terms = (
        summary, fingerprint.description, description, *profile.capabilities, *profile.use_cases, *profile.keywords,
    )
    return summary, "\n".join(dict.fromkeys(filter(None, terms)))


def _action_result(action: CapabilityAction) -> dict[str, Any]:
    description = redact_sensitive_text(action.description or "")
    return {**asdict(action), "description": sanitize_model_text(description)[:300]}
