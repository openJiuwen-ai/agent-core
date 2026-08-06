# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic normalization for capability types and I/O declarations."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Literal, NoReturn, overload

import yaml  # type: ignore[import-untyped]
from pydantic import JsonValue, ValidationError

from openjiuwen.symphony.models import (
    CapabilityDescriptor,
    CapabilityIO,
    NormalizationDecision,
    NormalizationIssue,
)
from openjiuwen.symphony.models._redaction import is_sensitive_name, redact_sensitive_json, redact_sensitive_text

_RESOURCE_PACKAGE = "openjiuwen.symphony.shared.fingerprint.resources"
_RESOURCE_NAME = "data_types.yaml"
_IO_SPEC_KEYS = frozenset(
    {
        "name",
        "id",
        "type",
        "data_type",
        "datatype",
        "format",
        "mime_type",
        "description",
        "help",
        "required",
        "optional",
        "default",
        "metadata",
    }
)
_CAPABILITY_TYPE_ALIASES = {
    "skills": "skill",
    "subagent": "agent",
    "sub-agent": "agent",
    "sub_agent": "agent",
}
_IO_NAME_VOCAB_VERSION = "io-name-vocab-v1"


@dataclass(frozen=True)
class DataTypeResolution:
    """Result of resolving a raw data type against the packaged vocabulary."""

    raw_value: str
    normalized_value: str
    method: str
    known: bool


@dataclass(frozen=True)
class IONameResolution:
    """Resolution of a label against an observed-corpus I/O vocabulary."""

    raw_value: str
    token: str
    normalized_value: str | None
    known: bool
    method: str


@dataclass(frozen=True)
class IONameTerm:
    """Auditable corpus evidence for one fine-grained semantic I/O name."""

    name: str
    count: int
    directions: tuple[str, ...]
    data_types: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "directions": list(self.directions),
            "data_types": list(self.data_types),
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class IONameVocabulary:
    """Deterministic dynamic vocabulary derived from inventory and extraction."""

    version: str
    content_hash: str
    terms: tuple[IONameTerm, ...]

    @classmethod
    def empty(cls) -> IONameVocabulary:
        return cls.build(())

    @classmethod
    def build(cls, capabilities: Iterable[CapabilityDescriptor]) -> IONameVocabulary:
        observed: dict[str, dict[str, Any]] = {}
        for capability in capabilities:
            for direction, items in (("input", capability.inputs), ("output", capability.outputs)):
                for item in items:
                    name = normalize_io_name(item.name, fallback="")
                    if not name:
                        continue
                    evidence = observed.setdefault(
                        name,
                        {"count": 0, "directions": set(), "data_types": set()},
                    )
                    evidence["count"] += 1
                    evidence["directions"].add(direction)
                    evidence["data_types"].add(DataTypeVocabulary.default().normalize(item.type))

        terms = tuple(
            IONameTerm(
                name=name,
                count=int(evidence["count"]),
                directions=tuple(sorted(evidence["directions"])),
                data_types=tuple(sorted(evidence["data_types"])),
            )
            for name, evidence in sorted(observed.items())
        )
        return cls._from_terms(terms)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> IONameVocabulary:
        """Restore a private-cache vocabulary after validating its canonical hash."""

        if payload.get("version") != _IO_NAME_VOCAB_VERSION:
            raise ValueError("unsupported I/O-name vocabulary version")
        raw_terms = payload.get("terms")
        if not isinstance(raw_terms, Sequence) or isinstance(raw_terms, str | bytes | bytearray):
            raise TypeError("I/O-name vocabulary terms must be a sequence")
        if len(raw_terms) > 10_000:
            raise ValueError("I/O-name vocabulary is too large")

        terms: list[IONameTerm] = []
        for raw_term in raw_terms:
            if not isinstance(raw_term, Mapping):
                raise TypeError("I/O-name vocabulary term must be a mapping")
            name = normalize_io_name(raw_term.get("name"), fallback="")
            count = raw_term.get("count", 0)
            raw_directions = raw_term.get("directions", ())
            raw_data_types = raw_term.get("data_types", ())
            raw_aliases = raw_term.get("aliases", ())
            if not name:
                raise ValueError("invalid I/O-name vocabulary term")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("invalid I/O-name vocabulary term")
            for collection in (raw_directions, raw_data_types, raw_aliases):
                if not isinstance(collection, Sequence) or isinstance(collection, str | bytes | bytearray):
                    raise TypeError("invalid I/O-name vocabulary term collection")
                if len(collection) > 1_000:
                    raise ValueError("I/O-name vocabulary term collection is too large")
            directions = tuple(sorted({str(value) for value in raw_directions if str(value) in {"input", "output"}}))
            data_types = tuple(
                sorted(
                    {DataTypeVocabulary.default().normalize(value) for value in raw_data_types if str(value).strip()}
                )
            )
            normalized_aliases: set[str] = set()
            for value in raw_aliases:
                token = normalize_io_name(value, fallback="")
                if token and token != name:
                    normalized_aliases.add(token)
            aliases = tuple(sorted(normalized_aliases))
            terms.append(
                IONameTerm(
                    name=name,
                    count=count,
                    directions=directions,
                    data_types=data_types,
                    aliases=aliases,
                )
            )

        restored = cls._from_terms(terms)
        if payload.get("content_hash") != restored.content_hash:
            raise ValueError("I/O-name vocabulary hash does not match its terms")
        return restored

    @classmethod
    def _from_terms(cls, terms: Iterable[IONameTerm]) -> IONameVocabulary:
        canonical_terms = cls._canonical_terms(terms)
        canonical = {
            "version": _IO_NAME_VOCAB_VERSION,
            "terms": [term.to_dict() for term in canonical_terms],
        }
        encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return cls(
            version=_IO_NAME_VOCAB_VERSION,
            content_hash=hashlib.sha256(encoded).hexdigest(),
            terms=canonical_terms,
        )

    @staticmethod
    def _canonical_terms(terms: Iterable[IONameTerm]) -> tuple[IONameTerm, ...]:
        merged: dict[str, dict[str, Any]] = {}
        for term in terms:
            name = normalize_io_name(term.name, fallback="")
            if not name:
                continue
            evidence = merged.setdefault(
                name,
                {"count": 0, "directions": set(), "data_types": set(), "aliases": set()},
            )
            evidence["count"] = max(int(evidence["count"]), max(0, int(term.count)))
            evidence["directions"].update(value for value in term.directions if value in {"input", "output"})
            evidence["data_types"].update(
                DataTypeVocabulary.default().normalize(value) for value in term.data_types if str(value).strip()
            )
            for value in term.aliases:
                token = normalize_io_name(value, fallback="")
                if token and token != name:
                    evidence["aliases"].add(token)

        canonical_names = set(merged)
        alias_owners: dict[str, str] = {}
        for name in sorted(merged):
            for alias in sorted(merged[name]["aliases"]):
                if alias not in canonical_names:
                    alias_owners.setdefault(alias, name)
        return tuple(
            IONameTerm(
                name=name,
                count=int(evidence["count"]),
                directions=tuple(sorted(evidence["directions"])),
                data_types=tuple(sorted(evidence["data_types"])),
                aliases=tuple(sorted(alias for alias, owner in alias_owners.items() if owner == name)),
            )
            for name, evidence in sorted(merged.items())
        )

    def merge(self, *others: IONameVocabulary) -> IONameVocabulary:
        """Merge vocabulary evidence without build-to-build count inflation."""

        return self._from_terms(term for vocabulary in (self, *others) for term in vocabulary.terms)

    def with_observed_specs(self, inputs: object, outputs: object) -> IONameVocabulary:
        """Learn deterministic canonical terms and conservative aliases from raw extracted I/O."""

        observations: dict[str, dict[str, Any]] = {}
        for direction, raw_specs in (("input", inputs), ("output", outputs)):
            entries, _ = _coerce_io_entries(raw_specs)
            for entry in entries:
                if isinstance(entry, str):
                    payload: Mapping[str, object] = {"name": entry, "type": entry}
                elif isinstance(entry, Mapping):
                    payload = entry
                else:
                    continue
                raw_name = payload.get("name", payload.get("id"))
                safe_raw_name = redact_sensitive_text(str(raw_name or ""))
                token = normalize_io_name(safe_raw_name, fallback="")
                if not token:
                    continue
                raw_type = _first_present(payload, "type", "data_type", "datatype", "format", "mime_type")
                data_type = DataTypeVocabulary.default().normalize(raw_type if raw_type is not None else token)
                evidence = observations.setdefault(
                    token,
                    {"count": 0, "directions": set(), "data_types": set()},
                )
                evidence["count"] += 1
                evidence["directions"].add(direction)
                evidence["data_types"].add(data_type)

        additions: list[IONameTerm] = []
        canonical_names = set(self.observed_terms)
        for token, evidence in sorted(observations.items()):
            existing = self.lookup(token)
            target = existing or _conservative_io_name_alias(token, canonical_names) or token
            additions.append(
                IONameTerm(
                    name=target,
                    count=int(evidence["count"]),
                    directions=tuple(sorted(evidence["directions"])),
                    data_types=tuple(sorted(evidence["data_types"])),
                    aliases=(token,) if token != target else (),
                )
            )
        return self.merge(self._from_terms(additions))

    @property
    def hash(self) -> str:
        """Short compatibility alias for the auditable vocabulary hash."""

        return self.content_hash

    @property
    def observed_terms(self) -> tuple[str, ...]:
        return tuple(term.name for term in self.terms)

    @property
    def aliases(self) -> Mapping[str, str]:
        return MappingProxyType({alias: term.name for term in self.terms for alias in term.aliases})

    def term_names(self) -> list[str]:
        return list(self.observed_terms)

    def lookup(self, raw_value: object) -> str | None:
        return self.resolve(raw_value).normalized_value

    def resolve(self, raw_value: object) -> IONameResolution:
        raw = str(raw_value or "").strip()
        token = normalize_io_name(raw, fallback="")
        if token in self.observed_terms:
            normalized = token
            method = "vocab_exact"
        else:
            normalized = self.aliases.get(token)
            method = "vocab_alias" if normalized is not None else "unseen"
        return IONameResolution(
            raw_value=raw,
            token=token,
            normalized_value=normalized,
            known=normalized is not None,
            method=method,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_hash": self.content_hash,
            "terms": [term.to_dict() for term in self.terms],
        }

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-compatible payload without filesystem side effects."""

        return self.to_dict()


@dataclass(frozen=True)
class IONormalizationResult:
    """Normalized I/O plus the complete deterministic audit trail."""

    items: tuple[CapabilityIO, ...] = ()
    decisions: tuple[NormalizationDecision, ...] = ()
    issues: tuple[NormalizationIssue, ...] = ()


@dataclass(frozen=True)
class DataTypeVocabulary:
    """Immutable, reviewable vocabulary for deterministic data type handling."""

    version: str
    unknown_type: str
    aliases: Mapping[str, str]
    parents: Mapping[str, frozenset[str]]

    @classmethod
    def default(cls) -> DataTypeVocabulary:
        """Load the vocabulary shipped with ``openjiuwen.symphony``."""

        return _default_vocabulary()

    @property
    def types(self) -> frozenset[str]:
        """Return all canonical types."""

        return frozenset(self.parents)

    def resolve(self, raw_value: object) -> DataTypeResolution:
        """Resolve a value to a canonical type, falling back to ``unknown``."""

        raw = _raw_data_type(raw_value)
        if not raw:
            return DataTypeResolution("", self.unknown_type, "empty", False)

        lowered = unicodedata.normalize("NFKC", raw).strip().casefold()
        for candidate in (lowered, _normalize_type_token(lowered)):
            normalized = self.aliases.get(candidate)
            if normalized is not None:
                method = "exact" if candidate == normalized else "alias"
                return DataTypeResolution(raw, normalized, method, True)
        return DataTypeResolution(raw, self.unknown_type, "unknown", False)

    def normalize(self, raw_value: object) -> str:
        """Return only the canonical value for callers that need no evidence."""

        return self.resolve(raw_value).normalized_value

    def is_subtype(self, sub_type: object, super_type: object) -> bool:
        """Return whether ``sub_type`` transitively derives from ``super_type``."""

        child = self.normalize(sub_type)
        parent = self.normalize(super_type)
        if self.unknown_type in {child, parent}:
            return False
        if child == parent:
            return True

        pending = list(self.parents.get(child, ()))
        seen: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate == parent:
                return True
            if candidate in seen:
                continue
            seen.add(candidate)
            pending.extend(self.parents.get(candidate, ()))
        return False

    def can_feed(self, output_type: object, input_type: object) -> bool:
        """Conservatively check whether an output can satisfy an input type."""

        output = self.normalize(output_type)
        input_ = self.normalize(input_type)
        if self.unknown_type in {output, input_}:
            return False
        return output == input_ or self.is_subtype(output, input_)


def normalize_capability_type(raw_value: object, *, default: str = "skill") -> str:
    """Normalize known aliases while preserving unknown non-empty type strings."""

    if raw_value is None:
        return default
    value = str(raw_value).strip()
    if not value:
        return default
    lowered = value.casefold()
    if lowered in {"skill", "agent"}:
        return lowered
    return _CAPABILITY_TYPE_ALIASES.get(lowered, value)


def build_io_name_vocabulary(capabilities: Iterable[CapabilityDescriptor]) -> IONameVocabulary:
    """Build the dynamic I/O-name vocabulary for a descriptor corpus."""

    return IONameVocabulary.build(capabilities)


def normalize_io_name(raw_value: object, *, fallback: str) -> str:
    """Convert an I/O label to a deterministic snake-case semantic name."""

    safe_value = redact_sensitive_text(str(raw_value or ""))
    value = unicodedata.normalize("NFKC", safe_value).strip().casefold()
    value = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_")
    return value or fallback


def _conservative_io_name_alias(token: str, canonical_names: set[str]) -> str | None:
    """Resolve only an unambiguous plural spelling to an existing canonical term."""

    candidates: list[str] = []
    if len(token) > 4 and token.endswith("ies"):
        candidates.append(f"{token[:-3]}y")
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        candidates.append(token[:-1])
    matches = sorted(set(candidates) & canonical_names)
    return matches[0] if len(matches) == 1 else None


@overload
def normalize_io_specs(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
    io_name_vocabulary: IONameVocabulary | None = None,
    include_audit: Literal[False] = False,
) -> tuple[CapabilityIO, ...]:
    pass


@overload
def normalize_io_specs(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
    io_name_vocabulary: IONameVocabulary | None = None,
    include_audit: Literal[True],
) -> IONormalizationResult:
    pass


def normalize_io_specs(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
    io_name_vocabulary: IONameVocabulary | None = None,
    include_audit: bool = False,
) -> tuple[CapabilityIO, ...] | IONormalizationResult:
    """Normalize I/O declarations and optionally return their complete audit trail."""

    result = _normalize_io_specs(
        raw_value,
        direction=direction,
        vocabulary=vocabulary,
        io_name_vocabulary=io_name_vocabulary,
    )
    return result if include_audit else result.items


def normalize_io_specs_with_issues(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
) -> tuple[tuple[CapabilityIO, ...], tuple[NormalizationIssue, ...]]:
    """Normalize I/O declarations and retain non-fatal cleanup diagnostics."""

    result = _normalize_io_specs(raw_value, direction=direction, vocabulary=vocabulary)
    return result.items, result.issues


def normalize_io_specs_with_audit(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
    io_name_vocabulary: IONameVocabulary | None = None,
) -> IONormalizationResult:
    """Normalize I/O declarations and always return decisions and issues."""

    return _normalize_io_specs(
        raw_value,
        direction=direction,
        vocabulary=vocabulary,
        io_name_vocabulary=io_name_vocabulary,
    )


def sanitize_io_specs(raw_value: object) -> object:
    """Retain only safe JSON state needed to repeat I/O normalization.

    Concurrent extraction may learn a larger final name vocabulary after one
    capability has already been normalized. Keeping this bounded private state
    lets reconciliation repeat normalization without losing rejected entries or
    their original indexes.
    """

    entries, issues = _coerce_io_entries(raw_value)
    if any(issue.code == "invalid_io_collection" for issue in issues):
        return 0
    return [_sanitize_io_entry(entry) for entry in entries]


def _normalize_io_specs(
    raw_value: object,
    *,
    direction: str,
    vocabulary: DataTypeVocabulary | None = None,
    io_name_vocabulary: IONameVocabulary | None = None,
) -> IONormalizationResult:
    """Normalize I/O declarations without dropping decisions or issues."""

    vocab = vocabulary or DataTypeVocabulary.default()
    normalized_direction = "input" if direction.casefold() == "input" else "output"
    default_name = "input" if normalized_direction == "input" else "result"
    default_required: bool | None = True if normalized_direction == "input" else None
    entries, issues = _coerce_io_entries(raw_value)
    normalized: list[CapabilityIO] = []
    decisions: list[NormalizationDecision] = []
    seen_names: dict[str, int] = {}

    for index, entry in enumerate(entries):
        item, item_decisions, item_issues = _normalize_io_entry(
            entry,
            index=index,
            direction=normalized_direction,
            default_name=default_name,
            default_required=default_required,
            vocabulary=vocab,
            io_name_vocabulary=io_name_vocabulary,
        )
        decisions.extend(item_decisions)
        issues.extend(item_issues)
        if item is None:
            continue

        occurrence = seen_names.get(item.name, 0) + 1
        seen_names[item.name] = occurrence
        if occurrence > 1:
            unique_name = f"{item.name}_{occurrence}"
            issues.append(
                NormalizationIssue(
                    code="duplicate_io_name",
                    message=f"duplicate I/O name normalized to '{unique_name}'",
                    direction=normalized_direction,
                    index=index,
                )
            )
            decisions.append(
                NormalizationDecision(
                    direction=normalized_direction,
                    index=index,
                    field="name",
                    raw_value=item.name,
                    token=item.name,
                    normalized_value=unique_name,
                    method="deduplicate",
                    vocabulary="io_name_vocab",
                    vocabulary_version=(
                        io_name_vocabulary.version if io_name_vocabulary is not None else _IO_NAME_VOCAB_VERSION
                    ),
                )
            )
            item = item.model_copy(update={"name": unique_name})
        normalized.append(item)

    normalized_issues = tuple(
        issue if issue.direction else issue.model_copy(update={"direction": normalized_direction}) for issue in issues
    )
    return IONormalizationResult(
        items=tuple(normalized),
        decisions=tuple(decisions),
        issues=normalized_issues,
    )


def _is_nonempty_string(value: object) -> bool:
    """Return whether a value is a non-empty string after trimming."""

    return isinstance(value, str) and bool(value.strip())


def _normalize_io_entry(
    entry: object,
    *,
    index: int,
    direction: str,
    default_name: str,
    default_required: bool | None,
    vocabulary: DataTypeVocabulary,
    io_name_vocabulary: IONameVocabulary | None,
) -> tuple[CapabilityIO | None, list[NormalizationDecision], list[NormalizationIssue]]:
    decisions: list[NormalizationDecision] = []
    issues: list[NormalizationIssue] = []
    if isinstance(entry, str):
        payload: Mapping[str, object] = {"name": entry, "type": entry}
    elif isinstance(entry, Mapping):
        payload = entry
    else:
        return (
            None,
            decisions,
            [
                NormalizationIssue(
                    code="invalid_io_entry",
                    message="I/O entry must be a string or mapping",
                    direction=direction,
                    index=index,
                )
            ],
        )

    raw_name = payload.get("name", payload.get("id", default_name))
    raw_name_text = redact_sensitive_text(str(raw_name or "").strip())
    name_token = normalize_io_name(raw_name_text, fallback="")
    name_resolution = io_name_vocabulary.resolve(raw_name_text) if io_name_vocabulary is not None else None
    if name_resolution is not None and name_resolution.normalized_value is not None:
        name = name_resolution.normalized_value
        name_method = name_resolution.method
    else:
        name = name_token or default_name
        name_method = "vocab_fallback" if io_name_vocabulary is not None else "syntax"
        if io_name_vocabulary is not None and name_token:
            issues.append(
                NormalizationIssue(
                    code="io_name_not_in_vocabulary",
                    message=f"I/O name '{name_token}' was not observed in the corpus vocabulary",
                    direction=direction,
                    index=index,
                )
            )
        elif not name_token:
            issues.append(
                NormalizationIssue(
                    code="missing_io_name",
                    message=f"missing I/O name normalized to '{default_name}'",
                    direction=direction,
                    index=index,
                )
            )
    decisions.append(
        NormalizationDecision(
            direction=direction,
            index=index,
            field="name",
            raw_value=raw_name_text,
            token=name_token,
            normalized_value=name,
            method=name_method,
            vocabulary="io_name_vocab",
            vocabulary_version=(
                io_name_vocabulary.version if io_name_vocabulary is not None else _IO_NAME_VOCAB_VERSION
            ),
            details={
                "vocabulary_hash": io_name_vocabulary.content_hash,
                "observed": bool(name_resolution is not None and name_resolution.known),
            }
            if io_name_vocabulary is not None
            else {},
        )
    )
    metadata = _normalize_metadata(payload.get("metadata"))
    raw_type = _first_present(payload, "type", "data_type", "datatype", "format", "mime_type")
    if raw_type is None:
        raw_type = name
    resolution = vocabulary.resolve(raw_type)
    preserved_raw_type = metadata.get("raw_type")
    is_unknown_resolution = (
        resolution.normalized_value == vocabulary.unknown_type
        and resolution.raw_value.casefold() == vocabulary.unknown_type
    )
    if is_unknown_resolution and _is_nonempty_string(preserved_raw_type):
        resolution = vocabulary.resolve(preserved_raw_type)
    decisions.append(
        NormalizationDecision(
            direction=direction,
            index=index,
            field="type",
            raw_value=resolution.raw_value,
            token=_normalize_type_token(resolution.raw_value),
            normalized_value=resolution.normalized_value,
            method=resolution.method,
            vocabulary="data_type_vocab",
            vocabulary_version=vocabulary.version,
            details={"known": resolution.known},
        )
    )

    raw_description = payload.get("description", payload.get("help", ""))
    description = str(raw_description or "").strip()
    required = _normalize_required(payload, default_required)
    if resolution.raw_value and not resolution.known:
        metadata["raw_type"] = resolution.raw_value
        issues.append(
            NormalizationIssue(
                code="unknown_data_type",
                message=f"unsupported data type '{resolution.raw_value}' normalized to '{vocabulary.unknown_type}'",
                direction=direction,
                index=index,
            )
        )

    raw_default = payload.get("default")
    default: JsonValue
    default_was_redacted = metadata.get("default_redacted") is True
    if raw_default is not None and is_sensitive_name(name):
        default = None
        metadata["default_redacted"] = True
        issues.append(
            NormalizationIssue(
                code="sensitive_default_redacted",
                message="sensitive I/O default was not retained",
                direction=direction,
                index=index,
            )
        )
    else:
        default = _to_json_value(raw_default)
        if default_was_redacted:
            issues.append(
                NormalizationIssue(
                    code="sensitive_default_redacted",
                    message="sensitive I/O default was not retained",
                    direction=direction,
                    index=index,
                )
            )

    try:
        item = CapabilityIO(
            name=name,
            type=resolution.normalized_value,
            description=description,
            required=required,
            default=default,
            metadata=metadata,
        )
    except (TypeError, ValidationError):
        issues.append(
            NormalizationIssue(
                code="invalid_io_entry",
                message="I/O entry failed model validation",
                direction=direction,
                index=index,
            )
        )
        return None, decisions, issues
    return item, decisions, issues


def _coerce_io_entries(raw_value: object) -> tuple[list[object], list[NormalizationIssue]]:
    if raw_value is None:
        return [], []
    if isinstance(raw_value, str):
        return [raw_value], []
    if isinstance(raw_value, Mapping):
        if any(str(key).casefold() in _IO_SPEC_KEYS for key in raw_value):
            return [raw_value], []

        entries: list[object] = []
        for raw_name, raw_spec in raw_value.items():
            if isinstance(raw_spec, Mapping):
                spec = dict(raw_spec)
                spec.setdefault("name", str(raw_name))
            else:
                spec = {"name": str(raw_name), "type": raw_spec}
            entries.append(spec)
        return entries, []
    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (bytes, bytearray)):
        return list(raw_value), []
    return [], [
        NormalizationIssue(
            code="invalid_io_collection",
            message="I/O declarations must be a list, mapping, or string",
        )
    ]


def _sanitize_io_entry(entry: object) -> object:
    if isinstance(entry, str):
        return redact_sensitive_text(entry)
    if not isinstance(entry, Mapping):
        # A JSON scalar remains an invalid entry when normalization is replayed.
        return 0
    retained = {str(key): value for key, value in entry.items() if str(key).casefold() in _IO_SPEC_KEYS}
    return _json_compatible_without_opaque_repr(redact_sensitive_json(retained))


def _json_compatible_without_opaque_repr(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_without_opaque_repr(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_compatible_without_opaque_repr(item) for item in value]
    return "<unsupported>"


def _normalize_required(payload: Mapping[str, object], default: bool | None) -> bool | None:
    if "required" in payload:
        raw_required = payload.get("required")
        return None if raw_required is None else _to_bool(raw_required, default)
    if "optional" in payload:
        optional = _to_bool(payload.get("optional"), None)
        return None if optional is None else not optional
    if "default" in payload:
        return False
    return default


def _to_bool(raw_value: object, default: bool | None) -> bool | None:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        lowered = raw_value.strip().casefold()
        if lowered in {"true", "yes", "1", "required"}:
            return True
        if lowered in {"false", "no", "0", "optional"}:
            return False
    if isinstance(raw_value, int) and raw_value in {0, 1}:
        return bool(raw_value)
    return default


def _raw_data_type(raw_value: object) -> str:
    if isinstance(raw_value, Mapping):
        candidate = _first_present(raw_value, "format", "contentMediaType", "type")
        return redact_sensitive_text(str(candidate or "").strip())
    return redact_sensitive_text(str(raw_value or "").strip())


def _first_present(payload: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _normalize_metadata(raw_value: object) -> dict[str, Any]:
    if not isinstance(raw_value, Mapping):
        return {}
    normalized = _to_json_value(raw_value)
    return normalized if isinstance(normalized, dict) else {}


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_to_json_value(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _normalize_type_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@lru_cache(maxsize=1)
def _default_vocabulary() -> DataTypeVocabulary:
    try:
        resource = resources.files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME)
        payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        _raise_vocabulary_config_error(exc)

    if not isinstance(payload, Mapping) or not isinstance(payload.get("types"), Mapping):
        _raise_vocabulary_config_error(None)

    unknown_type = str(payload.get("unknown") or "unknown").strip().casefold()
    version = str(payload.get("version") or "data-type-v1").strip()
    raw_types = payload["types"]
    canonical_types = {str(name).strip().casefold() for name in raw_types if str(name).strip()}
    if unknown_type not in canonical_types:
        _raise_vocabulary_config_error(None)

    aliases: dict[str, str] = {}
    parents: dict[str, frozenset[str]] = {}
    for raw_name, raw_config in raw_types.items():
        canonical = str(raw_name).strip().casefold()
        config = raw_config if isinstance(raw_config, Mapping) else {}
        aliases[canonical] = canonical
        aliases[_normalize_type_token(canonical)] = canonical
        for raw_alias in config.get("aliases", ()):
            alias = str(raw_alias).strip().casefold()
            if alias:
                aliases[alias] = canonical
                aliases[_normalize_type_token(alias)] = canonical
        parent_values = {
            str(parent).strip().casefold()
            for parent in config.get("parents", ())
            if str(parent).strip().casefold() in canonical_types
        }
        parents[canonical] = frozenset(parent_values)

    return DataTypeVocabulary(
        version=version,
        unknown_type=unknown_type,
        aliases=MappingProxyType(aliases),
        parents=MappingProxyType(parents),
    )


def _raise_vocabulary_config_error(cause: BaseException | None) -> NoReturn:
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    raise build_error(
        StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
        cause=cause,
        reason=f"invalid packaged data type vocabulary '{_RESOURCE_NAME}'",
    )


__all__ = [
    "DataTypeResolution",
    "DataTypeVocabulary",
    "IONameResolution",
    "IONameTerm",
    "IONameVocabulary",
    "IONormalizationResult",
    "NormalizationDecision",
    "NormalizationIssue",
    "build_io_name_vocabulary",
    "normalize_capability_type",
    "normalize_io_name",
    "normalize_io_specs",
    "normalize_io_specs_with_audit",
    "normalize_io_specs_with_issues",
]
