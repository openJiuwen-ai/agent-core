# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import (
    LLMApiMode,
    LLMAuthMode,
    ModelRequestConfig,
    ModelClientConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import normalize_model_client_config


def _value(value):
    return value.value if hasattr(value, "value") else value


def _implementation_provider(client_config: ModelClientConfig) -> str:
    provider = _value(client_config.client_provider)
    api_mode = _value(getattr(client_config, "api_mode", None))
    if api_mode == LLMApiMode.AnthropicMessages.value:
        return ProviderType.Anthropic.value

    if provider != ProviderType.OpenAI.value:
        return provider

    auth_mode = _value(getattr(client_config, "auth_mode", LLMAuthMode.ApiKey.value))
    if auth_mode == LLMAuthMode.OpenAIAccountOAuth.value:
        return ProviderType.OpenAIAccount.value
    return ProviderType.OpenAI.value


def _builtin_model_client(provider, client_config: ModelClientConfig, model_config: ModelRequestConfig):
    if client_config is None:
        return None
    if provider == ProviderType.OpenAI.value:
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
        return OpenAIModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.OpenAIAccount.value:
        from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import OpenAIAccountModelClient
        return OpenAIAccountModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.Anthropic.value:
        from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
        return AnthropicModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.IntelliRouter.value:
        from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import \
            IntelliRouterModelClient
        return IntelliRouterModelClient(model_config=model_config, model_client_config=client_config)
    return None


def create_model_client(client_config: ModelClientConfig, model_config: ModelRequestConfig) -> BaseModelClient:
    """Create corresponding ModelClient instance based on client_type

    Args:
        client_config: Client configuration

    Returns:
        BaseModelClient: ModelClient instance

    Raises:
        ValueError: When client_provider is not supported
    """
    if client_config.client_provider is None:
        raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                          error_msg="model client config client_provider is none")
    if client_config.client_id is None:
        raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                          error_msg="model client config client_id is none")
    provider = client_config.client_provider.value if isinstance(client_config.client_provider, ProviderType)\
        else client_config.client_provider
    normalized_config = normalize_model_client_config(client_config)
    dispatch_provider = _implementation_provider(normalized_config)
    dispatch_config = normalized_config
    client = _builtin_model_client(dispatch_provider, dispatch_config, model_config)
    if client is not None:
        return client
    try:
        client = get_client_registry().get_client(provider, "llm", model_config=model_config,
                                                  model_client_config=client_config)
    except ValueError:
        supported_types = [name[4:] for name in get_client_registry().list_clients() if name.startswith("llm_")]
        raise build_error(
            StatusCode.MODEL_PROVIDER_INVALID,
            error_msg=f"Unsupported client_provider: '{client_config.client_provider}', Supported types:"
                      f" {supported_types}"
        )
    return client
