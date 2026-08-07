# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from copy import deepcopy
from typing import Any, Callable

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.config import (
    LLMAuthMode,
    LLMApiMode,
    ModelClientConfig,
    ProviderType,
)


class EndpointProfile(BaseModel):
    """Structured rules for an OpenAI-compatible endpoint profile."""

    name: str
    protocol: str = ProviderType.OpenAI.value
    api_mode: str = LLMApiMode.ChatCompletions.value
    default_api_base: str | None = None
    message_transforms: list[str] = Field(default_factory=list)
    request_transforms: list[str] = Field(default_factory=list)
    response_transforms: list[str] = Field(default_factory=list)
    stream_transforms: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


def _deepseek_reasoning_content(messages: list[dict]) -> list[dict]:
    for message in messages:
        if message.get("role") == "assistant":
            message.setdefault("reasoning_content", "")
    return messages


MESSAGE_TRANSFORMS: dict[str, Callable[[list[dict]], list[dict]]] = {
    "deepseek_reasoning_content": _deepseek_reasoning_content,
}


ENDPOINT_PROFILES: dict[str, EndpointProfile] = {
    "openai": EndpointProfile(name="openai"),
    "openai_compatible": EndpointProfile(name="openai_compatible"),
    "deepseek": EndpointProfile(
        name="deepseek",
        message_transforms=["deepseek_reasoning_content"],
    ),
    "openrouter": EndpointProfile(name="openrouter"),
    "siliconflow": EndpointProfile(name="siliconflow"),
    "dashscope": EndpointProfile(name="dashscope"),
    "ollama": EndpointProfile(
        name="ollama",
        default_api_base="http://localhost:11434/v1",
    ),
    "lmstudio": EndpointProfile(
        name="lmstudio",
        default_api_base="http://localhost:1234/v1",
    ),
    "vllm": EndpointProfile(name="vllm"),
    "sglang": EndpointProfile(name="sglang"),
}


LEGACY_PROVIDER_ALIASES: dict[str, dict[str, Any]] = {
    ProviderType.DeepSeek.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "deepseek",
    },
    ProviderType.OpenRouter.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "openrouter",
    },
    ProviderType.SiliconFlow.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "siliconflow",
    },
    ProviderType.DashScope.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "dashscope",
    },
    ProviderType.InferenceAffinity.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "openai_compatible",
        "extensions": {
            "kv_cache": {"mode": "release"},
        },
    },
    ProviderType.AscendAffinity.value: {
        "client_provider": ProviderType.OpenAI.value,
        "endpoint_profile": "openai_compatible",
        "auth_mode": LLMAuthMode.CustomHeaders.value,
        "extensions": {
            "kv_cache": {"mode": "affinity"},
        },
    },
    ProviderType.OpenAIAccount.value: {
        "client_provider": ProviderType.OpenAI.value,
        "api_mode": LLMApiMode.Responses.value,
        "auth_mode": LLMAuthMode.OpenAIAccountOAuth.value,
    },
}


def deep_merge_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Merge default values without overriding explicit user-provided values."""
    merged = deepcopy(defaults)
    for key, value in target.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_defaults(value, merged[key])
        else:
            merged[key] = value
    return merged


def normalize_model_client_config(config: ModelClientConfig) -> ModelClientConfig:
    """Return a normalized config carrying protocol/profile/auth/api-mode metadata."""
    provider = (
        config.client_provider.value
        if isinstance(config.client_provider, ProviderType)
        else config.client_provider
    )
    provider = str(provider or "").strip()
    alias = LEGACY_PROVIDER_ALIASES.get(provider)
    if not alias:
        return config

    data = config.model_dump()
    explicit_data = config.model_dump(exclude_unset=True)
    explicit_fields = config.model_fields_set
    data["legacy_client_provider"] = provider
    for key, value in alias.items():
        if key == "extensions":
            continue
        if key == "client_provider" or key not in explicit_fields:
            data[key] = value

    if alias.get("extensions"):
        normalized_extensions = deep_merge_defaults(
            alias["extensions"],
            data.get("extensions") or {},
        )
        explicit_extensions = explicit_data.get("extensions") or {}
        data["extensions"] = deep_merge_defaults(
            explicit_extensions,
            normalized_extensions,
        )
    return ModelClientConfig(**data)


def resolve_endpoint_profile(config: ModelClientConfig) -> EndpointProfile:
    profile_name = getattr(config, "endpoint_profile", None)
    if not profile_name:
        profile_name = "openai" if config.client_provider == ProviderType.OpenAI.value else "openai_compatible"
    try:
        return ENDPOINT_PROFILES[str(profile_name)]
    except KeyError as exc:
        raise build_error(
            StatusCode.MODEL_SERVICE_CONFIG_ERROR,
            error_msg=f"unknown endpoint_profile: {profile_name}"
        ) from exc


def apply_message_transforms(config: ModelClientConfig, messages: list[dict]) -> list[dict]:
    profile = resolve_endpoint_profile(config)
    transformed = messages
    for name in profile.message_transforms:
        transform = MESSAGE_TRANSFORMS.get(name)
        if transform is None:
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg=f"unknown message transform: {name}"
            )
        transformed = transform(transformed)
    return transformed
