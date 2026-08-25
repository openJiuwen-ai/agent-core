# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable, versioned policies for improving the Harness improver."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


POLICY_SCHEMA_VERSION = 1
STATIC_RANKING_POLICY = "static_priority_v1"
RANKING_FEATURES = frozenset({"executable", "coverage", "atomicity", "duplicate"})


class FrozenDict(Mapping[str, Any]):
    """Small recursively immutable mapping used by frozen policy objects."""

    __slots__ = ("_items", "_lookup")

    def __init__(self, value: Mapping[str, Any] | None = None) -> None:
        source = value or {}
        if not isinstance(source, Mapping):
            raise TypeError("policy mapping fields must be mappings")
        items: list[tuple[str, Any]] = []
        for key, item in source.items():
            if not isinstance(key, str) or not key:
                raise ValueError("policy mapping keys must be non-empty strings")
            items.append((key, _freeze_json_value(item)))
        items.sort(key=lambda pair: pair[0])
        self._items = tuple(items)
        self._lookup = dict(items)

    def __getitem__(self, key: str) -> Any:
        return self._lookup[key]

    def __iter__(self) -> Iterator[str]:
        return iter(key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"FrozenDict({_thaw_json_value(self)!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict:
        del memo
        return self


@dataclass(frozen=True, slots=True)
class VersionedImproverPolicy:
    """A serializable snapshot of the policy used by the Harness improver."""

    version_id: str
    parent_version_id: str | None
    training_ledger_digest: str
    ranking_policy: str = STATIC_RANKING_POLICY
    ranking_weights: Mapping[str, float] = field(default_factory=lambda: _DEFAULT_RANKING_WEIGHTS)
    generation_directives: Mapping[str, Any] = field(default_factory=lambda: _DEFAULT_GENERATION_DIRECTIVES)
    budget_policy: Mapping[str, Any] = field(default_factory=lambda: _DEFAULT_BUDGET_POLICY)
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.version_id, str) or not self.version_id.strip():
            raise ValueError("version_id must be a non-empty string")
        if self.parent_version_id is not None and (
            not isinstance(self.parent_version_id, str) or not self.parent_version_id.strip()
        ):
            raise ValueError("parent_version_id must be None or a non-empty string")
        if not isinstance(self.training_ledger_digest, str) or not self.training_ledger_digest.strip():
            raise ValueError("training_ledger_digest must be a non-empty string")
        if not isinstance(self.ranking_policy, str) or not self.ranking_policy.strip():
            raise ValueError("ranking_policy must be a non-empty string")

        ranking_weights = FrozenDict(self.ranking_weights)
        _validate_ranking_weights(ranking_weights)
        object.__setattr__(self, "ranking_weights", ranking_weights)
        object.__setattr__(self, "generation_directives", FrozenDict(self.generation_directives))
        budget_policy = FrozenDict(self.budget_policy)
        _validate_budget_policy(budget_policy)
        object.__setattr__(self, "budget_policy", budget_policy)

        if isinstance(self.evidence_refs, str):
            raise TypeError("evidence_refs must be a sequence of strings")
        refs = tuple(sorted({ref.strip() for ref in self.evidence_refs if isinstance(ref, str) and ref.strip()}))
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self) -> dict[str, Any]:
        """Return the plain-data representation written to YAML."""
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "version_id": self.version_id,
            "parent_version_id": self.parent_version_id,
            "training_ledger_digest": self.training_ledger_digest,
            "ranking_policy": self.ranking_policy,
            "ranking_weights": _thaw_json_value(self.ranking_weights),
            "generation_directives": _thaw_json_value(self.generation_directives),
            "budget_policy": _thaw_json_value(self.budget_policy),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VersionedImproverPolicy:
        """Validate and construct a policy from plain parsed data."""
        if not isinstance(value, Mapping):
            raise TypeError("improver policy document must be a mapping")
        schema_version = value.get("schema_version", POLICY_SCHEMA_VERSION)
        if schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError(f"unsupported improver policy schema_version: {schema_version!r}")
        refs = value.get("evidence_refs", [])
        if not isinstance(refs, (list, tuple)):
            raise TypeError("evidence_refs must be a sequence")
        return cls(
            version_id=value.get("version_id", ""),
            parent_version_id=value.get("parent_version_id"),
            training_ledger_digest=value.get("training_ledger_digest", ""),
            ranking_policy=value.get("ranking_policy", ""),
            ranking_weights=value.get("ranking_weights", {}),
            generation_directives=value.get("generation_directives", {}),
            budget_policy=value.get("budget_policy", {}),
            evidence_refs=tuple(refs),
        )

    @property
    def canonical_digest(self) -> str:
        """Stable digest of the complete serialized policy."""
        return canonical_policy_digest(self)


def default_improver_policy() -> VersionedImproverPolicy:
    """Return the immutable I0 policy equivalent to ``static_priority_v1``."""
    return VersionedImproverPolicy(
        version_id="I0",
        parent_version_id=None,
        training_ledger_digest=_EMPTY_LEDGER_DIGEST,
    )


def canonical_policy_digest(policy: VersionedImproverPolicy | Mapping[str, Any]) -> str:
    """Return a stable SHA-256 digest independent of mapping insertion order."""
    payload = policy.to_dict() if isinstance(policy, VersionedImproverPolicy) else _plain_json_value(policy)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_improver_policy(path: str | Path) -> VersionedImproverPolicy:
    """Load and validate a versioned improver policy from YAML."""
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("improver policy YAML must contain a mapping")
    return VersionedImproverPolicy.from_dict(payload)


def write_improver_policy(path: str | Path, policy: VersionedImproverPolicy) -> Path:
    """Atomically write a policy as deterministic YAML."""
    if not isinstance(policy, VersionedImproverPolicy):
        raise TypeError("policy must be a VersionedImproverPolicy")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            yaml.safe_dump(
                policy.to_dict(),
                temporary,
                allow_unicode=True,
                sort_keys=True,
            )
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return destination


def score_static_priority(
    policy: VersionedImproverPolicy,
    features: Mapping[str, float | bool],
) -> float:
    """Score the four ``static_priority_v1`` features with policy weights."""
    if policy.ranking_policy != STATIC_RANKING_POLICY:
        raise ValueError(f"unsupported ranking policy: {policy.ranking_policy!r}")
    _validate_ranking_weights(policy.ranking_weights)
    unknown = set(features) - RANKING_FEATURES
    missing = RANKING_FEATURES - set(features)
    if unknown:
        raise ValueError(f"unknown ranking features: {sorted(unknown)!r}")
    if missing:
        raise ValueError(f"missing ranking features: {sorted(missing)!r}")

    normalized: dict[str, float] = {}
    for name, value in features.items():
        if isinstance(value, bool):
            normalized[name] = float(value)
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"ranking feature {name!r} must be finite")
        normalized[name] = float(value)
    return sum(float(policy.ranking_weights[name]) * normalized[name] for name in sorted(RANKING_FEATURES))


def propose_policy_candidates(
    parent: VersionedImproverPolicy,
    feedback_analysis: Mapping[str, Any],
    *,
    min_support: int | None = None,
) -> tuple[VersionedImproverPolicy, ...]:
    """Create one immutable child candidate per supported, concrete recommendation."""
    if not isinstance(parent, VersionedImproverPolicy):
        raise TypeError("parent must be a VersionedImproverPolicy")
    if not isinstance(feedback_analysis, Mapping):
        raise TypeError("feedback_analysis must be a mapping")
    threshold = _minimum_support(parent, min_support)
    patterns = feedback_analysis.get("stable_patterns", [])
    if not isinstance(patterns, list):
        raise ValueError("feedback_analysis.stable_patterns must be a list")
    ledger_digest = _feedback_ledger_digest(feedback_analysis)

    supported: list[tuple[tuple[Any, ...], Mapping[str, Any], Mapping[str, Any]]] = []
    for pattern in patterns:
        if not isinstance(pattern, Mapping):
            continue
        support = _pattern_metric(pattern, "support_cohorts", "support")
        if support is None or support < threshold:
            continue
        recommendation = pattern.get("recommended_policy_change")
        if not isinstance(recommendation, Mapping) or not _is_concrete_change(recommendation):
            continue
        ordering = (
            -support,
            -(_finite_number(pattern.get("rate")) or 0.0),
            -(_pattern_metric(pattern, "opportunity_cohorts", "opportunity") or 0.0),
            str(pattern.get("pattern_id", "")),
            _canonical_json(recommendation),
        )
        supported.append((ordering, pattern, recommendation))

    candidates: list[VersionedImproverPolicy] = []
    seen_changes: set[str] = set()
    for _, pattern, recommendation in sorted(supported, key=lambda item: item[0]):
        change_key = _canonical_json(recommendation)
        if change_key in seen_changes:
            continue
        seen_changes.add(change_key)
        candidates.append(
            _apply_recommended_change(
                parent,
                recommendation,
                pattern=pattern,
                feedback_analysis=feedback_analysis,
                ledger_digest=ledger_digest,
            )
        )
    return tuple(candidates)


def propose_policy_update(
    parent: VersionedImproverPolicy,
    feedback_analysis: Mapping[str, Any],
    *,
    min_support: int | None = None,
) -> VersionedImproverPolicy | None:
    """Return the strongest supported single-change candidate, if one exists."""
    candidates = propose_policy_candidates(parent, feedback_analysis, min_support=min_support)
    return candidates[0] if candidates else None


def _apply_recommended_change(
    parent: VersionedImproverPolicy,
    change: Mapping[str, Any],
    *,
    pattern: Mapping[str, Any],
    feedback_analysis: Mapping[str, Any],
    ledger_digest: str,
) -> VersionedImproverPolicy:
    field_name = str(change["field"]).strip()
    operation = str(change["operation"]).strip().lower()
    value = _plain_json_value(change["value"])
    ranking_weights = _thaw_json_value(parent.ranking_weights)
    generation_directives = _thaw_json_value(parent.generation_directives)
    budget_policy = _thaw_json_value(parent.budget_policy)

    root, *path = field_name.split(".")
    target = {
        "ranking_weights": ranking_weights,
        "generation_directives": generation_directives,
        "budget_policy": budget_policy,
    }[root]
    _apply_deep_change(target, path, operation, value)

    refs = set(parent.evidence_refs)
    for ref in feedback_analysis.get("evidence_refs", []):
        if isinstance(ref, str) and ref.strip():
            refs.add(ref.strip())
    for ref in pattern.get("evidence_cohort_ids", []):
        if isinstance(ref, str) and ref.strip():
            refs.add(ref.strip())
    pattern_id = pattern.get("pattern_id")
    if isinstance(pattern_id, str) and pattern_id.strip():
        refs.add(f"pattern:{pattern_id.strip()}")

    version_payload = {
        "parent_version_id": parent.version_id,
        "training_ledger_digest": ledger_digest,
        "change": {
            "field": field_name,
            "operation": operation,
            "value": value,
        },
        "ranking_policy": parent.ranking_policy,
        "ranking_weights": ranking_weights,
        "generation_directives": generation_directives,
        "budget_policy": budget_policy,
        "evidence_refs": sorted(refs),
    }
    version_digest = hashlib.sha256(_canonical_json(version_payload).encode("utf-8")).hexdigest()
    return VersionedImproverPolicy(
        version_id=f"I_{version_digest[:16]}",
        parent_version_id=parent.version_id,
        training_ledger_digest=ledger_digest,
        ranking_policy=parent.ranking_policy,
        ranking_weights=ranking_weights,
        generation_directives=generation_directives,
        budget_policy=budget_policy,
        evidence_refs=tuple(refs),
    )


def _is_concrete_change(change: Mapping[str, Any]) -> bool:
    if set(change) != {"field", "operation", "value", "rationale"}:
        return False
    field_name = change.get("field")
    operation = change.get("operation")
    rationale = change.get("rationale")
    if not all(isinstance(value, str) and value.strip() for value in (field_name, operation, rationale)):
        return False
    field_name = field_name.strip()
    operation = operation.strip().lower()
    parts = field_name.split(".")
    if parts[0] == "ranking_weights":
        value = change.get("value")
        finite_value = _finite_number(value)
        return (
            len(parts) == 2
            and parts[1] in RANKING_FEATURES
            and operation in {"set", "increase", "decrease"}
            and finite_value is not None
            and (operation == "set" or finite_value > 0.0)
        )
    if field_name == "budget_policy.top_m":
        value = change.get("value")
        return operation == "increase" and isinstance(value, int) and not isinstance(value, bool) and value > 0
    if field_name in {
        "generation_directives.require_unique_candidate_fingerprint",
        "generation_directives.require_distinct_intervention_surfaces",
        "generation_directives.preserve_partial_progress_and_target_residual",
    }:
        return operation == "set" and change.get("value") is True
    if len(parts) == 3 and ".".join(parts[:2]) in {
        "generation_directives.require_activation_evidence",
        "generation_directives.avoid_target_regression",
    }:
        return bool(parts[2]) and operation == "set" and change.get("value") is True
    return False


def _apply_deep_change(target: dict[str, Any], path: list[str], operation: str, value: Any) -> None:
    if not path:
        raise ValueError("recommended policy field must identify a leaf")
    cursor = target
    for part in path[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        if not isinstance(existing, dict):
            raise ValueError(f"recommended policy path crosses non-mapping field: {part!r}")
        cursor = existing
    leaf = path[-1]
    if operation == "set":
        cursor[leaf] = value
        return
    if operation == "increase":
        current = cursor.get(leaf)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError(f"cannot increase non-numeric policy field: {leaf!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("increase value must be numeric")
        result = float(current) + float(value)
        if not math.isfinite(result):
            raise ValueError("increased policy value must be finite")
        cursor[leaf] = int(result) if isinstance(current, int) and isinstance(value, int) else result
        return
    if operation == "decrease":
        current = cursor.get(leaf)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError(f"cannot decrease non-numeric policy field: {leaf!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("decrease value must be numeric")
        result = float(current) - float(value)
        if not math.isfinite(result):
            raise ValueError("decreased policy value must be finite")
        cursor[leaf] = int(result) if isinstance(current, int) and isinstance(value, int) else result
        return
    raise ValueError(f"unsupported policy change operation: {operation!r}")


def _minimum_support(parent: VersionedImproverPolicy, explicit: int | None) -> int:
    value = explicit if explicit is not None else parent.budget_policy.get("min_pattern_support", 2)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("min_support must be a positive integer")
    return value


def _feedback_ledger_digest(feedback_analysis: Mapping[str, Any]) -> str:
    for key in ("training_ledger_digest", "source_ledger_digest", "ledger_digest"):
        value = feedback_analysis.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return canonical_policy_digest(_plain_json_value(feedback_analysis))


def _validate_ranking_weights(weights: Mapping[str, Any]) -> None:
    unknown = set(weights) - RANKING_FEATURES
    missing = RANKING_FEATURES - set(weights)
    if unknown:
        raise ValueError(f"unknown ranking weights: {sorted(unknown)!r}")
    if missing:
        raise ValueError(f"missing ranking weights: {sorted(missing)!r}")
    for name, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"ranking weight {name!r} must be finite")


def _validate_budget_policy(policy: Mapping[str, Any]) -> None:
    required = {"top_m", "min_pattern_support"}
    missing = required - set(policy)
    if missing:
        raise ValueError(f"missing budget policy fields: {sorted(missing)!r}")
    for name in required:
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"budget policy {name!r} must be a positive integer")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _pattern_metric(pattern: Mapping[str, Any], primary: str, fallback: str) -> float | None:
    if primary in pattern:
        return _finite_number(pattern.get(primary))
    return _finite_number(pattern.get(fallback))


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, float):
        raise ValueError("policy values must be finite")
    raise TypeError(f"unsupported policy value type: {type(value).__name__}")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _plain_json_value(value: Any) -> Any:
    return _thaw_json_value(_freeze_json_value(value))


def _canonical_json(value: Any) -> str:
    return json.dumps(_plain_json_value(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_DEFAULT_RANKING_WEIGHTS = FrozenDict(
    {
        "executable": 100.0,
        "coverage": 20.0,
        "atomicity": 5.0,
        "duplicate": -30.0,
    }
)
_DEFAULT_GENERATION_DIRECTIVES = FrozenDict()
_DEFAULT_BUDGET_POLICY = FrozenDict(
    {
        "top_m": 1,
        "min_pattern_support": 2,
    }
)
_EMPTY_LEDGER_DIGEST = "sha256:" + hashlib.sha256(b"[]").hexdigest()
