# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DTOs used to compile resolved model selections into LLM runtime config."""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig


class ModelSelection(BaseModel):
    """A persisted or user-provided model selection reference."""

    type: Literal["model", "model_group"]
    id: str


class ResolvedModel(BaseModel):
    """A concrete callable model resolved by the upper layer."""

    model_id: str
    model_name: str
    provider: str
    api_key: str = ""
    api_base: str = ""
    source: str = "defaults"
    endpoint_profile: Optional[str] = None
    fallback_tag: Optional[str] = None
    model_description: Optional[str] = None
    client_options: dict[str, Any] = Field(default_factory=dict)
    request_defaults: dict[str, Any] = Field(default_factory=dict)


class ResolvedRoute(BaseModel):
    """One candidate route inside a model group."""

    route_id: str
    model: ResolvedModel
    enabled: bool = True
    request_overrides: dict[str, Any] = Field(default_factory=dict)
    tpm: Optional[int] = None
    rpm: Optional[int] = None
    timeout: Optional[float] = None


class ResolvedModelGroup(BaseModel):
    """A resolved model group ready for core-side compilation."""

    model_group_id: str
    routes: list[ResolvedRoute]
    routing: dict[str, Any] = Field(default_factory=dict)
    request_config: dict[str, Any] = Field(default_factory=dict)


class CompiledModelSelection(BaseModel):
    """The two configs needed by ``Model`` after compiling a selection."""

    model_client_config: ModelClientConfig
    model_request_config: ModelRequestConfig
    selected_type: Literal["model", "model_group"]
    selected_id: str


ResolvedSelection = Union[ResolvedModel, ResolvedModelGroup]
