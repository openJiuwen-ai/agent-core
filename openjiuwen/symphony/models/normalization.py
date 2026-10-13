# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auditable decisions and non-fatal issues produced by I/O normalization."""

from __future__ import annotations

from pydantic import Field, field_validator

from openjiuwen.symphony.models._base import JsonObject, NonEmptyString, SymphonyModel
from openjiuwen.symphony.models._redaction import is_sensitive_name, redact_sensitive_text


class NormalizationDecision(SymphonyModel):
    """One deterministic field-level normalization choice."""

    direction: NonEmptyString
    index: int | None = Field(default=None, ge=0)
    field: NonEmptyString
    raw_value: str = ""
    token: str = ""
    normalized_value: str = ""
    method: NonEmptyString
    vocabulary: NonEmptyString
    vocabulary_version: NonEmptyString
    details: JsonObject = Field(default_factory=dict)

    @field_validator("raw_value", mode="before")
    @classmethod
    def _redact_raw_value(cls, value: object) -> object:
        return redact_sensitive_text(value) if isinstance(value, str) else value

    @field_validator("token", mode="before")
    @classmethod
    def _redact_sensitive_token(cls, value: object) -> object:
        if isinstance(value, str) and is_sensitive_name(value):
            return "<redacted>"
        return value


class NormalizationIssue(SymphonyModel):
    """One non-fatal cleanup or fallback observed during normalization."""

    code: NonEmptyString
    message: NonEmptyString
    direction: str = ""
    index: int | None = Field(default=None, ge=0)
    details: JsonObject = Field(default_factory=dict)


__all__ = ["NormalizationDecision", "NormalizationIssue"]
