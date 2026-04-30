# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from enum import Enum
from typing import Optional, Union, Any, Self

from pydantic import BaseModel, Field, model_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class ProviderType(str, Enum):
    """ModelClientProvider type"""
    OpenAI = "OpenAI"
    OpenRouter = "OpenRouter"
    SiliconFlow = "SiliconFlow"
    DashScope = "DashScope"
    DeepSeek = "DeepSeek"
    InferenceAffinity = "InferenceAffinity"


class ModelClientConfig(BaseModel):
    """ModelClient config"""
    client_id: str = Field(default_factory=lambda: str(uuid.uuid4()),
        description="The ModelClient client ID is a unique identifier used for registration in the Runner")
    client_provider: Union[ProviderType, str] = Field(
        ...,
        description="Service provider identification, Enumeration value: OpenAI, OpenRouter, "
                    "SiliconFlow, DashScope, InferenceAffinity or ICBC"
    )
    api_key: str = Field(..., description="API key")
    api_base: str = Field(..., description="API base URL")
    timeout: float = Field(default=60.0, gt=0, description="Request timeout in seconds (must be greater than 0)")
    max_retries: int = Field(default=3, description="Maximum number of retries")
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")
    ssl_cert: Optional[str] = Field(default=None, description="Path to SSL certificate file")
    custom_headers: Optional[dict[str, Any]] = Field(
        default=None,
        description="Developer-provided headers merged per LLM call"
    )
    model_config = {"extra": "allow"}  # Allow extra fields injected by core/provider (e.g. default headers)

    @model_validator(mode='after')
    def validate_client_provider(self) -> Self:
        """Validate and normalize client_provider."""
        if isinstance(self.client_provider, ProviderType):
            return self
        provider = self.client_provider.value if isinstance(self.client_provider, ProviderType) \
            else str(self.client_provider)
        provider = provider.strip()
        if provider in ProviderType.__members__:
            self.client_provider = provider
            return self

        # Normalize common lowercase/mixed-case provider values to canonical keys.
        provider_map = {k.lower(): k.value for k in ProviderType}
        normalized_provider = provider_map.get(provider.lower())
        if normalized_provider:
            self.client_provider = normalized_provider
            return self

        from openjiuwen.core.common.clients import get_client_registry
        supported_types = [name[4:] for name in get_client_registry().list_clients() if name.startswith("llm_")]
        for builtin in ProviderType:
            supported_types.append(builtin.value)
        supported_types = list(set(supported_types))
        if provider in supported_types:
            self.client_provider = provider
            return self
        else:
            raise build_error(
                StatusCode.MODEL_PROVIDER_INVALID,
                error_msg=f"unavailable model provider: {provider},"
                          f"and available providers are: {supported_types}"
            )


class ModelRequestConfig(BaseModel):
    """Model config"""
    model_name: str = Field(default="", alias="model", description="Model name, e.g. gpt-4")
    temperature: float = Field(default=0.95, description="Temperature parameter, controlling the randomness of outputs")
    top_p: float = Field(default=0.1, description="Top-p sampling parameter")
    max_tokens: Optional[int] = Field(default=None, description="Maximum number of tokens to generate")
    stop: Union[Optional[str], None] = Field(default=None, description="Stop sequence")
    model_config = {"extra": "allow", "populate_by_name": True}
