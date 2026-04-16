# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
