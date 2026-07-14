# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ProviderType


class _TempMockClient(BaseModelClient):
    __client_name__ = "TempMockLLM"
    pass


def test_model_client_config_accepts_supported_providers():
    cfg = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key="sk-test",
        api_base="http://localhost",
    )
    assert cfg.client_provider == ProviderType.OpenAI

    cfg2 = ModelClientConfig(
        client_provider=ProviderType.SiliconFlow,
        api_key="sk-test",
        api_base="http://localhost",
    )
    assert cfg2.client_provider == ProviderType.SiliconFlow

    cfg3 = ModelClientConfig(
        client_provider=ProviderType.OpenRouter,
        api_key="sk-test",
        api_base="http://localhost",
    )
    assert cfg3.client_provider == ProviderType.OpenRouter


def test_model_client_config_normalizes_openrouter_provider_case():
    cfg = ModelClientConfig(
        client_provider="OPENROUTER",
        api_key="sk-test",
        api_base="http://localhost",
    )
    assert cfg.client_provider == ProviderType.OpenRouter


def test_model_client_config_allows_registered_string_provider():
    provider = "TempMockLLM"
    cfg = ModelClientConfig(
        client_provider=provider,
        api_key="sk-test",
        api_base="http://localhost",
    )
    assert cfg.client_provider == provider


def test_model_client_config_defers_registered_provider_credentials_to_client():
    provider = "TempMockLLM"
    cfg = ModelClientConfig(client_provider=provider)

    assert cfg.client_provider == provider
    assert cfg.api_key == ""
    assert cfg.api_base == ""


def test_base_model_client_validates_registered_provider_api_base():
    cfg = ModelClientConfig(
        client_provider="TempMockLLM",
        api_key="sk-test",
    )
    client = SimpleNamespace(
        model_client_config=cfg,
        _get_client_name=lambda: "TempMockLLM",
    )

    with pytest.raises(BaseError) as error:
        BaseModelClient._validate_config(client)

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "api_base is required for TempMockLLM" in str(error.value)


def test_model_client_config_requires_api_key_for_non_openai_account_provider():
    with pytest.raises(BaseError) as error:
        ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_base="http://localhost",
        )

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "api_key is required for provider OpenAI" in str(error.value)


def test_model_client_config_requires_api_base_for_top_level_provider():
    with pytest.raises(BaseError) as error:
        ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
        )

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "api_base is required for provider OpenAI" in str(error.value)


def test_model_client_config_allows_openai_account_without_api_key():
    cfg = ModelClientConfig(
        client_provider=ProviderType.OpenAIAccount,
        api_base="http://localhost",
    )

    assert cfg.client_provider == ProviderType.OpenAIAccount
    assert cfg.api_key == ""


def test_model_client_config_requires_openai_account_api_base():
    with pytest.raises(BaseError) as error:
        ModelClientConfig(
            client_provider=ProviderType.OpenAIAccount,
        )

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "api_base is required for provider OpenAIAccount" in str(error.value)


def test_model_client_config_allows_intelli_router_without_top_level_credentials():
    cfg = ModelClientConfig(
        client_provider=ProviderType.IntelliRouter,
        intelli_router_deployments=[
            {
                "model_name": "qwen-turbo",
                "api_key": "deployment-key",
                "api_base": "https://dashscope.aliyuncs.com",
                "provider": "dashscope",
            }
        ],
    )

    assert cfg.client_provider == ProviderType.IntelliRouter
    assert cfg.api_key == ""
    assert cfg.api_base == ""


def test_model_client_config_allows_intelli_router_value_without_top_level_credentials():
    cfg = ModelClientConfig(
        client_provider="intelli_router",
        intelli_router_deployments=[
            {
                "model_name": "qwen-turbo",
                "api_key": "deployment-key",
                "api_base": "https://dashscope.aliyuncs.com",
                "provider": "dashscope",
            }
        ],
    )

    assert cfg.client_provider == ProviderType.IntelliRouter.value
    assert cfg.api_key == ""
    assert cfg.api_base == ""


def test_model_client_config_normalizes_intelli_router_enum_name():
    cfg = ModelClientConfig(client_provider="IntelliRouter")

    assert cfg.client_provider == ProviderType.IntelliRouter.value
    assert cfg.api_key == ""
    assert cfg.api_base == ""


def test_model_client_config_timeout_must_be_positive():
    with pytest.raises(ValidationError) as error:
        ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base="http://localhost",
            timeout=0,
        )
    assert error.value.errors()[0]["type"] == "greater_than"


def test_model_client_config_accepts_custom_headers():
    cfg = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key="sk-test",
        api_base="http://localhost",
        custom_headers={"X-Custom": "custom"},
    )

    assert cfg.custom_headers == {"X-Custom": "custom"}
