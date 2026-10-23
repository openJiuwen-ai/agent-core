"""Versioned immutable scientific-condition metadata and comparisons."""

from __future__ import annotations

import copy
import hashlib
import json
import importlib.metadata
import platform
from dataclasses import dataclass
from typing import Any, Mapping

from ..config import Config
from ..models.encoders import FixedBaseEncoders


SCIENTIFIC_CONFIGURATION_SCHEMA = 5
MUTABLE_CONTINUATION_CONTROLS = ("training.max_outer_cycles",)


def configured_encoder_metadata(config: Config) -> dict[str, Any]:
    """Construct the metadata of the fixed encoders declared by ``config``."""

    return FixedBaseEncoders(
        config.n_agents,
        configuration=config.encoders,
        scheme_order=config.scheme_order,
        categories=dict(config.history_categories) or None,
    ).metadata()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ScientificConfiguration:
    """Immutable experiment projection plus explicit continuation controls."""

    schema_version: int
    immutable: Mapping[str, Any]
    mutable_continuation: Mapping[str, Any]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "immutable": copy.deepcopy(dict(self.immutable)),
            "mutable_continuation": copy.deepcopy(dict(self.mutable_continuation)),
            "fingerprint": self.fingerprint,
        }


def scientific_configuration(
    config: Config,
    *,
    encoder_metadata: Mapping[str, Any] | None = None,
    architecture_identifiers: Mapping[str, str],
) -> ScientificConfiguration:
    """Build the frozen condition while isolating sanctioned resume controls."""

    payload = config.to_dict()
    training = dict(payload["training"])
    mutable = {
        "training.max_outer_cycles": training.pop("max_outer_cycles"),
    }
    payload["training"] = training
    immutable = {
        "config": payload,
        "fixed_encoder_metadata": copy.deepcopy(dict(encoder_metadata or configured_encoder_metadata(config))),
        "architecture_identifiers": dict(architecture_identifiers),
        "mutable_continuation_controls": MUTABLE_CONTINUATION_CONTROLS,
        "numerical_runtime": {
            "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "numpy")},
            "optimizer": "torch.optim.Adam",
            "construction_order": [
                "routing_query",
                "routing_answer",
                "routing_history_independent_layers",
                "routing_head",
                "weighting_query",
                "weighting_history_independent_layers",
                "weighting_head",
            ],
            "device": "cpu",
        },
    }
    fingerprint = hashlib.sha256(_canonical_json(immutable).encode("utf-8")).hexdigest()
    return ScientificConfiguration(
        SCIENTIFIC_CONFIGURATION_SCHEMA,
        immutable,
        mutable,
        fingerprint,
    )


def _differences(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        differences: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left:
                differences.append(f"{path}: missing from checkpoint")
            elif key not in right:
                differences.append(f"{path}: missing from active configuration")
            else:
                differences.extend(_differences(left[key], right[key], path))
        return differences
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return [f"{prefix}: checkpoint length {len(left)} != active length {len(right)}"]
        differences = []
        for index, (old, new) in enumerate(zip(left, right, strict=True)):
            differences.extend(_differences(old, new, f"{prefix}[{index}]"))
        return differences
    return [] if left == right else [f"{prefix}: checkpoint={left!r}, active={right!r}"]


def validate_scientific_compatibility(
    saved: Mapping[str, Any],
    active: ScientificConfiguration,
) -> None:
    """Reject an incompatible checkpoint before any state is restored."""

    if saved.get("schema_version") != active.schema_version:
        raise ValueError(
            "incompatible scientific-configuration schema: "
            f"checkpoint={saved.get('schema_version')!r}, active={active.schema_version!r}"
        )
    saved_immutable = saved.get("immutable")
    if not isinstance(saved_immutable, Mapping):
        raise ValueError("checkpoint is missing immutable scientific configuration")
    saved_fingerprint = saved.get("fingerprint")
    expected_saved_fingerprint = hashlib.sha256(_canonical_json(saved_immutable).encode("utf-8")).hexdigest()
    if saved_fingerprint != expected_saved_fingerprint:
        raise ValueError("checkpoint scientific-configuration fingerprint is corrupt")
    if saved_fingerprint != active.fingerprint:
        differences = _differences(saved_immutable, active.immutable)
        detail = "; ".join(differences[:8]) or "fingerprints differ"
        raise ValueError(f"incompatible immutable scientific configuration: {detail}")

    saved_mutable = saved.get("mutable_continuation")
    if not isinstance(saved_mutable, Mapping):
        raise ValueError("checkpoint is missing mutable continuation controls")
    saved_ceiling = saved_mutable.get("training.max_outer_cycles")
    active_ceiling = active.mutable_continuation["training.max_outer_cycles"]
    if not isinstance(saved_ceiling, int) or active_ceiling < saved_ceiling:
        raise ValueError(
            "active training.max_outer_cycles may only preserve or extend the " "checkpoint continuation ceiling"
        )
