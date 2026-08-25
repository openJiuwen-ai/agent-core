# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable tokenizer selection metadata."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompatibleTokenizerSpec(BaseModel):
    """A tokenizer from the same model family that may be used as fallback."""

    model_config = ConfigDict(populate_by_name=True)

    model: str
    tokenizer_id: str | None = Field(default=None, alias="id")
    source: Literal["huggingface", "modelscope", "provider_official", "local"] = "local"
    engine: Literal["auto", "tiktoken", "tokenizers"] = "auto"
    revision: str | None = None
    artifact_path: str | None = None
    sha256: str | None = None

    @field_validator("source", "engine", mode="before")
    @classmethod
    def _normalize_source(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return str(value).strip().casefold()


class TokenizerSpec(BaseModel):
    """Tokenizer configuration for one target model.

    ``engine`` is the preferred engine. The actual selected engine is exposed
    by ``TokenMeasurement.source`` after exact/family/fallback resolution.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: str = ""
    model: str = ""
    tokenizer_id: str | None = Field(default=None, alias="id")
    source: Literal["huggingface", "modelscope", "provider_official", "local"] | None = None
    revision: str | None = None
    artifact_path: str | None = None
    engine: Literal["auto", "tiktoken", "tokenizers"] = "auto"
    fallback_policy: Literal["family_tokenizer_then_default_tiktoken_then_string_length"] = (
        "family_tokenizer_then_default_tiktoken_then_string_length"
    )
    chat_template: str | None = None
    sha256: str | None = None
    family: str | None = None
    compatible_fallbacks: list[CompatibleTokenizerSpec] = Field(default_factory=list)

    @field_validator("source", "engine", mode="before")
    @classmethod
    def _normalize_enum_values(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return str(value).strip().casefold()


__all__ = ["CompatibleTokenizerSpec", "TokenizerSpec"]
