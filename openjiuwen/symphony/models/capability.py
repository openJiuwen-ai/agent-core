# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability inventory and source snapshot contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import Field, JsonValue, field_validator, model_validator

from openjiuwen.symphony.models._base import JsonObject, NonEmptyString, SymphonyModel
from openjiuwen.symphony.models._redaction import is_sensitive_name, redact_sensitive_json


class CapabilityIO(SymphonyModel):
    """One semantic input or output exposed by a capability."""

    name: NonEmptyString
    type: NonEmptyString
    description: str = ""
    required: bool | None = None
    default: JsonValue = None
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _redact_sensitive_default(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        name = str(value.get("name") or "")
        raw_default = value.get("default")
        if raw_default is None:
            return value
        payload = dict(value)
        payload["default"] = redact_sensitive_json(raw_default)
        if not is_sensitive_name(name):
            return payload
        raw_metadata = payload.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        metadata["default_redacted"] = True
        payload["default"] = None
        payload["metadata"] = metadata
        return payload


class SemanticProfile(SymphonyModel):
    """Retrieval-oriented semantic description of a capability."""

    summary: str = ""
    capabilities: tuple[NonEmptyString, ...] = ()
    use_cases: tuple[NonEmptyString, ...] = ()
    limitations: tuple[NonEmptyString, ...] = ()
    keywords: tuple[NonEmptyString, ...] = ()

    @field_validator("capabilities", "use_cases", "limitations", "keywords", mode="after")
    @classmethod
    def _deduplicate_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class CapabilityDescriptor(SymphonyModel):
    """Provider-owned metadata for one discoverable capability."""

    capability_id: NonEmptyString
    capability_type: NonEmptyString
    name: NonEmptyString
    description: str = ""
    source: str = ""
    available: bool = True
    inputs: tuple[CapabilityIO, ...] = ()
    outputs: tuple[CapabilityIO, ...] = ()
    classification: str = ""
    tags: tuple[NonEmptyString, ...] = ()
    content_hash: str = ""
    semantic_content: str = Field(default="", exclude=True, repr=False)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("tags", mode="after")
    @classmethod
    def _deduplicate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class SourceSnapshot(SymphonyModel):
    """Stable identity and optional diagnostics for a provider inventory snapshot."""

    snapshot_id: NonEmptyString
    source: str = ""
    content_hash: str = ""
    captured_at: datetime | None = None
    capability_count: int | None = Field(default=None, ge=0)
    metadata: JsonObject = Field(default_factory=dict)
