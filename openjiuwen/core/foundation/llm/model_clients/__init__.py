# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig, ProviderType


def _builtin_model_client(provider, client_config: ModelClientConfig, model_config: ModelRequestConfig):
    if client_config is None:
        return None
    if provider == ProviderType.OpenAI.value or provider == ProviderType.OpenRouter.value:
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
        return OpenAIModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.SiliconFlow.value:
        from openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client import \
            SiliconFlowModelClient
        return SiliconFlowModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.DashScope.value:
        from openjiuwen.core.foundation.llm.model_clients.dashscope_model_client import \
            DashScopeModelClient
        return DashScopeModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.InferenceAffinity.value:
        from openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client import \
            InferenceAffinityModelClient
        return InferenceAffinityModelClient(model_config=model_config, model_client_config=client_config)

    if provider == ProviderType.DeepSeek.value:
        from openjiuwen.core.foundation.llm.model_clients.deepseek_model_client import \
            DeepSeekModelClient
        return DeepSeekModelClient(model_config=model_config, model_client_config=client_config)
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
    client = _builtin_model_client(provider, client_config, model_config)
    if client is not None:
        return client
    try:
        client = get_client_registry().get_client(provider, "llm", model_config=model_config,
                                                  model_client_config=client_config)
    except ValueError as e:
        supported_types = [name[4:] for name in get_client_registry().list_clients() if name.startswith("llm_")]
        raise build_error(
            StatusCode.MODEL_PROVIDER_INVALID,
            error_msg=f"Unsupported client_provider: '{client_config.client_provider}', Supported types:"
                      f" {supported_types}"
        )
    return client