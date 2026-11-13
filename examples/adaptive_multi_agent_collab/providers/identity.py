"""Exact, evidence-bound model identifiers; never infer a snapshot from its name."""

from __future__ import annotations

from ..infrastructure.storage import read_bytes, read_json

import copy
from dataclasses import replace
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class ModelBinding(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def served_model(self) -> str | None: ...

    @property
    def served_provider(self) -> str | None: ...

    @property
    def provider_endpoint(self) -> str | None: ...


def validate_model_identity_evidence(binding: ModelBinding, model: str, evidence: Mapping[str, Any] | None) -> None:
    """Require a structured alias/canonical-slug mapping and the original direct route."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("catalog identity requires an explicit nonempty canonical model identifier")
    if not evidence or evidence.get("protocol") != "documented_accounting_model_identity_1":
        raise ValueError("accounting metadata returned a different served model")
    expected = {
        "requested_model": binding.model,
        "metadata_model": model,
        "served_provider": binding.served_provider,
        "provider_endpoint": binding.provider_endpoint,
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("accounting model identity evidence differs from the original binding")
    if binding.served_model not in (None, binding.model):
        raise ValueError("catalog evidence cannot replace an explicitly fixed served model")
    artifacts = {}
    for kind in ("model_catalog", "endpoint_catalog"):
        item = evidence[kind]
        path = Path(item["path"])
        raw = read_bytes(path)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("accounting model identity catalog changed")
        artifacts[kind] = json.loads(raw)
    models = [row for row in artifacts["model_catalog"]["data"] if row.get("id") == binding.model]
    if len(models) != 1 or models[0].get("canonical_slug") != model:
        raise ValueError("model catalog does not explicitly map this alias to the returned canonical slug")
    endpoint = artifacts["endpoint_catalog"]["data"]
    routes = [
        row
        for row in endpoint["endpoints"]
        if row.get("tag") == binding.provider_endpoint and row.get("provider_name") == binding.served_provider
    ]
    if endpoint.get("id") != binding.model or len(routes) != 1 or routes[0].get("model_id") != binding.model:
        raise ValueError("endpoint catalog does not establish the original direct provider/model binding")


def with_response_identity_evidence(prepared: Any, evidence: Mapping[str, Mapping[str, Any]]) -> Any:
    """Validate exact public identity evidence without changing scientific settings."""
    from .gateway import OpenRouterBinding

    if not isinstance(evidence, Mapping) or not set(evidence).issubset(prepared.backends):
        raise ValueError("response identity evidence must refer only to the frozen backend pool")
    for key, item in evidence.items():
        binding = OpenRouterBinding.from_dict(prepared.backends[key]["gateway"])
        binding.validate()
        canonical = item.get("metadata_model")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ValueError("response identity evidence requires an exact nonempty canonical model identifier")
        validate_model_identity_evidence(binding, canonical, item)
    return replace(prepared, response_identity_evidence=copy.deepcopy(dict(evidence)))


def with_registered_response_identity(prepared: Any, specification_path: str | Path) -> Any:
    """Attach validated shared evidence only through the registered campaign binding."""
    source = Path(specification_path).resolve()
    specification = read_json(source)
    if specification.get("policy_transition") or specification.get("revised_condition"):
        raise ValueError("historical diagnostic transitions are retired; execution is unavailable")
    if specification.get("independent_campaign"):
        from ..operations.policy_activation import policy_response_identity

        return policy_response_identity(prepared, source)
    campaign = Path(specification["campaign_directory"])
    campaign = campaign if campaign.is_absolute() else source.parent / campaign
    if (campaign / "active_condition.json").exists():
        raise ValueError("historical diagnostic transitions are retired; execution is unavailable")
    if prepared.response_identity_evidence:
        raise ValueError("response identity normalization requires its registered campaign boundary")
    return prepared
