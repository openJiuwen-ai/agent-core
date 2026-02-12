# coding: utf-8
import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ProviderType


class _TempMockClient(BaseModelClient):
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


def test_model_client_config_model_validate_invalid_provider_raises_base_error():
    with pytest.raises(BaseError) as error:
        ModelClientConfig(
            client_provider="mock-LLM",
            api_key="sk-test",
            api_base="http://localhost"
        )
    assert error.value.code == StatusCode.MODEL_PROVIDER_INVALID.code


def test_model_client_config_allows_registered_string_provider():
    from openjiuwen.core.foundation.llm.model import _CLIENT_TYPE_REGISTRY

    provider = "TempMockLLM"
    _CLIENT_TYPE_REGISTRY[provider] = _TempMockClient
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
