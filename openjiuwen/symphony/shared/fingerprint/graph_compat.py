# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Graph-compatible fingerprint contracts retained during component migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.symphony.models import CapabilityFingerprint


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "default": self.default,
        }


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    type: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "description": self.description}


@dataclass(frozen=True)
class Fingerprint:
    """Normalized capability metadata; accepts the historical fingerprint shape."""

    type: str
    id: str
    name: str
    description: str
    version: str
    inputs: list[ParameterSpec] = field(default_factory=list)
    outputs: list[ArtifactSpec] = field(default_factory=list)
    static_data: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    @property
    def capability_id(self) -> str:
        return self.id

    @property
    def capability_type(self) -> str:
        return self.type

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_type": self.type,
            "capability_id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "static_data": self.static_data,
            "content_hash": self.content_hash,
        }

    def to_internal_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["id"] = payload.pop("capability_id")
        payload["type"] = payload.pop("capability_type")
        return payload

    def graph_identity_dict(self) -> dict[str, Any]:
        """Return only fields that affect graph construction and matching."""

        return _graph_identity(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Fingerprint":
        capability_id = str(payload.get("capability_id") or payload.get("id") or "").strip()
        capability_type = str(payload.get("capability_type") or payload.get("type") or "skill").strip()
        if not capability_id:
            raise ValueError("Capability fingerprint requires capability_id.")
        return cls(
            type=capability_type,
            id=capability_id,
            name=str(payload.get("name") or capability_id),
            description=str(payload.get("description") or ""),
            version=str(payload.get("version") or "1.0.0"),
            inputs=[
                item
                if isinstance(item, ParameterSpec)
                else ParameterSpec(
                    name=str(item.get("name") or "input"),
                    type=str(item.get("type") or "unknown"),
                    required=bool(item.get("required", True)),
                    description=str(item.get("description") or ""),
                    default=item.get("default"),
                )
                for item in payload.get("inputs", [])
            ],
            outputs=[
                item
                if isinstance(item, ArtifactSpec)
                else ArtifactSpec(
                    name=str(item.get("name") or "result"),
                    type=str(item.get("type") or "unknown"),
                    description=str(item.get("description") or ""),
                )
                for item in payload.get("outputs", [])
            ],
            static_data=dict(payload.get("static_data") or {}),
            content_hash=str(payload.get("content_hash") or ""),
        )


# Graph internals operate only on the normalized legacy shape.  Canonical
# fingerprints are accepted at explicit boundaries and coerced before use.
FingerprintLike = Fingerprint


def _graph_identity(value: Fingerprint) -> dict[str, Any]:
    return {
        "type": value.type,
        "id": value.id,
        "name": value.name,
        "description": value.description,
        "version": value.version,
        "inputs": [item.to_dict() for item in value.inputs],
        "outputs": [item.to_dict() for item in value.outputs],
    }


def coerce_fingerprint(value: object) -> Fingerprint:
    if isinstance(value, Fingerprint):
        return value
    if isinstance(value, CapabilityFingerprint):
        validated = CapabilityFingerprint.model_validate(value)
        payload = validated.to_dict()
        payload["content_hash"] = validated.content_hash
        return Fingerprint.from_dict(payload)
    if isinstance(value, dict):
        return Fingerprint.from_dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return Fingerprint.from_dict(to_dict())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return Fingerprint.from_dict(model_dump(mode="python"))
    raise TypeError(f"Unsupported capability fingerprint: {type(value).__name__}")
