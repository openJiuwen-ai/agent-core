# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared Pydantic configuration for Symphony public data contracts."""

from __future__ import annotations

from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints, field_validator

from openjiuwen.symphony.models._redaction import redact_sensitive_json, redact_sensitive_text

NonEmptyString: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
JsonObject: TypeAlias = dict[str, JsonValue]


class SymphonyModel(BaseModel):
    """Base model for immutable, forward-compatible JSON contracts."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        revalidate_instances="always",
        validate_default=True,
    )

    @field_validator("metadata", "details", mode="before", check_fields=False)
    @classmethod
    def _redact_public_extensions(cls, value: object) -> object:
        return redact_sensitive_json(value)

    @field_validator(
        "description", "message", "reason", "error", "summary", "source", mode="before", check_fields=False
    )
    @classmethod
    def _redact_public_text(cls, value: object) -> object:
        return redact_sensitive_text(value) if isinstance(value, str) else value
