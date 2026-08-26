# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from enum import Enum
from typing import Optional, Union, Any, Self, Literal

from pydantic import BaseModel, Field, model_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class ProviderType(str, Enum):
    """ModelClientProvider type"""
    OpenAI = "OpenAI"
    OpenAIAccount = "OpenAIAccount"
    OpenRouter = "OpenRouter"
    Anthropic = "Anthropic"
    SiliconFlow = "SiliconFlow"
    DashScope = "DashScope"
    DeepSeek = "DeepSeek"
    InferenceAffinity = "InferenceAffinity"
    AscendAffinity = "AscendAffinity"
    IntelliRouter = "intelli_router"


class LLMApiMode(str, Enum):
    ChatCompletions = "chat_completions"
    Responses = "responses"
    AnthropicMessages = "anthropic_messages"


class LLMAuthMode(str, Enum):
    ApiKey = "api_key"
    NoneAuth = "none"
    CustomHeaders = "custom_headers"
    OpenAIAccountOAuth = "openai_account_oauth"


class KVCacheExtensionConfig(BaseModel):
    mode: Literal["none", "release", "affinity"] = "none"
    release_endpoint: str = "/release_kv_cache"
    session_field: str = "cache_salt"
    enable_cache_sharing_field: str = "cache_sharing"
    affinity_field: str = "agent_hint"


class LLMExtensionsConfig(BaseModel):
    prompt_cache: dict[str, Any] = Field(default_factory=dict)
    kv_cache: KVCacheExtensionConfig = Field(default_factory=KVCacheExtensionConfig)
    response_fields: dict[str, Any] = Field(default_factory=dict)
    request_extra_body: dict[str, Any] = Field(default_factory=dict)


_TOP_LEVEL_API_KEY_PROVIDERS = {
    ProviderType.OpenAI.value,
    ProviderType.OpenRouter.value,
    ProviderType.Anthropic.value,
    ProviderType.SiliconFlow.value,
    ProviderType.DashScope.value,
    ProviderType.DeepSeek.value,
    ProviderType.InferenceAffinity.value,
}
_TOP_LEVEL_API_BASE_PROVIDERS = _TOP_LEVEL_API_KEY_PROVIDERS | {ProviderType.OpenAIAccount.value}
_DEFAULT_API_BASE_BY_ENDPOINT_PROFILE = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}


class ModelClientConfig(BaseModel):
    """ModelClient config"""
    client_id: str = Field(default_factory=lambda: str(uuid.uuid4()),
        description="The ModelClient client ID is a unique identifier used for registration in the Runner")
    client_provider: Union[ProviderType, str] = Field(
        ...,
        description="Service provider identification, Enumeration value: OpenAI, OpenRouter, "
                    "OpenAIAccount, SiliconFlow, DashScope, InferenceAffinity or ICBC"
    )
    api_key: str = Field(default="", description="API key")
    api_base: str = Field(default="", description="API base URL")
    timeout: float = Field(
        default=360.0,
        gt=0,
        description="HTTP request timeout in seconds (must be greater than 0). Kept >= "
                    "stream_first_chunk_timeout so the transport layer never times out before "
                    "the application-level stream timeout."
    )
    stream_first_chunk_timeout: Optional[float] = Field(
        default=300.0,
        gt=0,
        description="Maximum seconds to wait for the first parsed streaming chunk; None disables it"
    )
    stream_idle_timeout: Optional[float] = Field(
        default=60.0,
        gt=0,
        description="Maximum seconds to wait between parsed streaming chunks; None disables it"
    )

    max_retries: int = Field(
        default=1,
        description="Maximum number of thin SDK-level retries for transient HTTP failures "
                    "(connection drops, 429, 5xx). Whole-call retries are handled by LLMRetryRail."
    )
    use_shared_llm_http_client: bool = Field(
        default=True,
        description="Reuse a process-wide, long-lived HTTP client (with its own keep-alive connection pool) "
                    "instead of building/closing a client per request. Emergency kill-switch: set to False "
                    "to fall back to per-request clients."
    )
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")
    ssl_cert: Optional[str] = Field(default=None, description="Path to SSL certificate file")
    custom_headers: Optional[dict[str, Any]] = Field(
        default=None,
        description="Developer-provided headers merged per LLM call"
    )
    api_mode: Optional[LLMApiMode] = Field(default=None, description="LLM API mode")
    auth_mode: LLMAuthMode = Field(default=LLMAuthMode.ApiKey, description="LLM authentication mode")
    endpoint_profile: Optional[str] = Field(default=None, description="OpenAI-compatible endpoint profile name")
    extensions: LLMExtensionsConfig = Field(default_factory=LLMExtensionsConfig)
    legacy_client_provider: Optional[str] = Field(default=None, description="Original provider before normalization")
    model_config = {
        "extra": "allow",
    }

    @model_validator(mode='after')
    def validate_client_provider(self) -> Self:
        """Validate and normalize client_provider."""
        if isinstance(self.client_provider, ProviderType):
            self._validate_top_level_provider_config(self.client_provider.value)
            return self
        provider = self.client_provider.value if isinstance(self.client_provider, ProviderType) \
            else str(self.client_provider)
        provider = provider.strip()
        # Normalize enum names and values, including aliases such as
        # "IntelliRouter" -> "intelli_router".
        provider_map = {
            alias.lower(): builtin.value
            for builtin in ProviderType
            for alias in (builtin.name, builtin.value)
        }
        normalized_provider = provider_map.get(provider.lower())
        if normalized_provider:
            self.client_provider = normalized_provider
            self._validate_top_level_provider_config(normalized_provider)
            return self

        from openjiuwen.core.common.clients import get_client_registry
        supported_types = [name[4:] for name in get_client_registry().list_clients() if name.startswith("llm_")]
        for builtin in ProviderType:
            supported_types.append(builtin.value)
        supported_types = list(set(supported_types))
        if provider in supported_types:
            self.client_provider = provider
            self._validate_top_level_provider_config(provider)
            return self
        else:
            raise build_error(
                StatusCode.MODEL_PROVIDER_INVALID,
                error_msg=f"unavailable model provider: {provider},"
                          f"and available providers are: {supported_types}"
            )

    def _validate_top_level_provider_config(self, provider: str) -> None:
        if not str(self.api_base or "").strip() and self.endpoint_profile:
            default_api_base = _DEFAULT_API_BASE_BY_ENDPOINT_PROFILE.get(
                str(self.endpoint_profile).strip().lower()
            )
            if default_api_base:
                self.api_base = default_api_base
        if self.auth_mode != LLMAuthMode.ApiKey.value and self.auth_mode != LLMAuthMode.ApiKey:
            requires_api_key = False
        else:
            requires_api_key = provider in _TOP_LEVEL_API_KEY_PROVIDERS
        if requires_api_key and not str(self.api_key or "").strip():
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg=f"api_key is required for provider {provider}."
            )
        if provider in _TOP_LEVEL_API_BASE_PROVIDERS and not str(self.api_base or "").strip():
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg=f"api_base is required for provider {provider}."
            )


class ModelRequestConfig(BaseModel):
    """Model config"""
    model_name: str = Field(default="", alias="model", description="Model name, e.g. gpt-4")
    temperature: float = Field(default=0.95, description="Temperature parameter, controlling the randomness of outputs")
    top_p: float = Field(default=0.95, description="Top-p sampling parameter")
    max_tokens: Optional[int] = Field(default=None, description="Maximum number of tokens to generate")
    stop: Union[Optional[str], None] = Field(default=None, description="Stop sequence")
    model_config = {"extra": "allow", "populate_by_name": True}
