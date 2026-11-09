"""Rank live capabilities using the descriptions in their published fingerprints."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.discovery.models import sanitize_model_text
from openjiuwen.symphony.models import CapabilityDescriptor, CapabilityFingerprint
from openjiuwen.symphony.models._redaction import redact_sensitive_text
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex
from openjiuwen.symphony.shared.fingerprint import FingerprintArtifactStore, FingerprintService

_LOGGER = logging.getLogger(__name__)


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
    documents: list[LexicalDocument] = []
    rows: dict[str, dict[str, Any]] = {}
    actions: dict[str, tuple[str, CapabilityAction]] = {}
    exact_query = query.strip().casefold()
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
        summary, description = fingerprint_text(fingerprint, descriptor.content_hash, descriptor.description)
        names = (descriptor.name, descriptor.capability_id, key, *candidate.aliases)
        documents.append(LexicalDocument(
            key, descriptor.name, "\n".join((description, *descriptor.tags)), descriptor.semantic_content,
            (descriptor.capability_id, *candidate.aliases), descriptor.classification,
        ))
        rows[key] = {
            "type": kind,
            "id": descriptor.capability_id,
            "name": sanitize_model_text(descriptor.name),
            "description": sanitize_model_text(summary)[:512],
            "matched_on": "name" if any(exact_query == name.casefold() for name in names) else "catalog",
            "actions": [_action_result(action) for action in candidate.actions[:3]],
        }
        for index, action in enumerate(candidate.actions):
            action_key = f"action:{key}:{index}"
            actions[action_key] = (key, action)
            documents.append(LexicalDocument(action_key, action.id, action.description))

    matches: dict[str, dict[str, Any]] = {}
    matched_actions: dict[str, list[dict[str, Any]]] = {}
    for hit in LexicalIndex(documents).search_terms(query):
        if hit.key in actions:
            key, action = actions[hit.key]
            selected = matched_actions.setdefault(key, [])
            if len(selected) < 3:
                selected.append(_action_result(action))
            if key not in matches:
                matches[key] = {**rows[key], "matched_on": "action"}
        else:
            matches.setdefault(hit.key, rows[hit.key])
    return [
        {**row, "actions": matched_actions.get(key, row["actions"])}
        for key, row in list(matches.items())[:top_k]
    ]


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
