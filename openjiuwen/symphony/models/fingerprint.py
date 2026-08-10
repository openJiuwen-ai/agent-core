# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability fingerprint and artifact envelope contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, SerializationInfo, field_serializer, field_validator, model_validator

from openjiuwen.symphony.models._base import JsonObject, NonEmptyString, SymphonyModel
from openjiuwen.symphony.models._redaction import redact_sensitive_json
from openjiuwen.symphony.models.capability import (
    CapabilityDescriptor,
    CapabilityIO,
    SemanticProfile,
    SourceSnapshot,
)
from openjiuwen.symphony.models.evaluation import (
    EvidenceRef,
    FailureReason,
    ImprovementSuggestion,
    QualityResult,
)


class CapabilityFingerprint(CapabilityDescriptor):
    """Versioned semantic and quality view shared by Symphony subsystems."""

    version: NonEmptyString = "1.0.0"
    static_data: JsonObject = Field(default_factory=dict)
    semantic_profile: SemanticProfile = Field(default_factory=SemanticProfile)
    inputs: tuple[CapabilityIO, ...] = ()
    outputs: tuple[CapabilityIO, ...] = ()
    classification: str = ""
    tags: tuple[NonEmptyString, ...] = ()
    content_hash: str = ""
    semantic_content: str = Field(default="", exclude=True, repr=False)
    quality: QualityResult | None = None
    failures: tuple[FailureReason, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    suggestions: tuple[ImprovementSuggestion, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _accept_graph_io_contracts(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        for field_name in ("inputs", "outputs"):
            raw_items = payload.get(field_name)
            if isinstance(raw_items, (list, tuple)):
                payload[field_name] = tuple(_graph_io_payload(item) for item in raw_items)
        return payload

    @field_validator("tags", mode="after")
    @classmethod
    def _deduplicate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("static_data", mode="before")
    @classmethod
    def _redact_graph_extensions(cls, value: object) -> object:
        return redact_sensitive_json(value)

    @model_validator(mode="after")
    def _validate_quality_identity(self) -> CapabilityFingerprint:
        if self.quality is not None and (
            self.quality.capability_id,
            self.quality.capability_type,
        ) != (self.capability_id, self.capability_type):
            raise ValueError("quality identity does not match fingerprint")
        return self

    @property
    def id(self) -> str:
        """Compatibility identity used by the orchestration graph."""

        return self.capability_id

    @property
    def type(self) -> str:
        """Compatibility capability type used by the orchestration graph."""

        return self.capability_type

    def to_dict(self) -> dict[str, Any]:
        """Return the graph-compatible capability-first representation."""

        return {
            "capability_id": self.capability_id,
            "capability_type": self.capability_type,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "inputs": [_input_graph_dict(item) for item in self.inputs],
            "outputs": [_output_graph_dict(item) for item in self.outputs],
            "static_data": redact_sensitive_json(self.static_data),
        }

    def to_internal_dict(self) -> dict[str, Any]:
        """Return the historical graph representation at the private boundary."""

        payload = self.to_dict()
        payload["id"] = payload.pop("capability_id")
        payload["type"] = payload.pop("capability_type")
        return payload

    def graph_identity_dict(self) -> dict[str, Any]:
        """Return only fields that affect graph construction and matching."""

        return {
            "type": self.capability_type,
            "id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "inputs": [_input_graph_dict(item) for item in self.inputs],
            "outputs": [_output_graph_dict(item) for item in self.outputs],
        }


class FingerprintArtifact(SymphonyModel):
    """Schema-versioned ``fingerprint.json`` envelope."""

    schema_version: str = "1.0"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_snapshot: SourceSnapshot
    fingerprints: tuple[CapabilityFingerprint, ...] = ()

    @field_serializer("fingerprints")
    def _serialize_public_fingerprints(
        self,
        value: tuple[CapabilityFingerprint, ...],
        info: SerializationInfo,
    ) -> tuple[dict[str, Any], ...]:
        """Keep graph-only compatibility fields out of ``fingerprint.json``."""

        return tuple(item.model_dump(mode=info.mode, exclude={"version", "static_data"}) for item in value)

    @field_validator("generated_at")
    @classmethod
    def _normalize_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a UTC offset")
        return value.astimezone(UTC)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        value = value.strip()
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) is None:
            raise ValueError("schema_version must be a dotted numeric version")
        if value.split(".", maxsplit=1)[0] != "1":
            raise ValueError(f"unsupported fingerprint schema major version: {value}")
        return value

    @model_validator(mode="after")
    def _validate_unique_fingerprints(self) -> FingerprintArtifact:
        identities = [(item.capability_type, item.capability_id) for item in self.fingerprints]
        if len(identities) != len(set(identities)):
            raise ValueError("fingerprints must have unique capability identities")
        if any(not item.content_hash.strip() for item in self.fingerprints):
            raise ValueError("artifact fingerprints must include content_hash")
        return self


def _graph_io_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    return value


def _input_graph_dict(value: CapabilityIO) -> dict[str, Any]:
    value = CapabilityIO.model_validate(value)
    return {
        "name": value.name,
        "type": value.type,
        "required": True if value.required is None else value.required,
        "description": value.description,
        "default": value.default,
    }


def _output_graph_dict(value: CapabilityIO) -> dict[str, Any]:
    value = CapabilityIO.model_validate(value)
    return {
        "name": value.name,
        "type": value.type,
        "description": value.description,
    }
