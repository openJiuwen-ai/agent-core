"""Exact, evidence-bound model identifiers; never infer a snapshot from its name."""

from __future__ import annotations

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
        raw = path.read_bytes()
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
