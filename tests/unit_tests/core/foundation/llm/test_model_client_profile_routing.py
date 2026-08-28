# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountModelClient,
)
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import (
    LLMAuthMode,
    LLMApiMode,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import normalize_model_client_config


def _request_config() -> ModelRequestConfig:
    return ModelRequestConfig(model="test-model")


def test_legacy_deepseek_provider_routes_to_openai_client_with_profile():
    config = ModelClientConfig(
        client_provider="DeepSeek",
        api_key="sk-test",
        api_base="https://api.deepseek.com/v1",
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, OpenAIModelClient)
    assert client.model_client_config.client_provider == ProviderType.OpenAI.value
    assert client.model_client_config.endpoint_profile == "deepseek"


def test_legacy_deepseek_provider_normalizes_to_openai_protocol_metadata():
    config = ModelClientConfig(
        client_provider="DeepSeek",
        api_key="sk-test",
        api_base="https://api.deepseek.com/v1",
    )

    normalized = normalize_model_client_config(config)

    assert normalized.client_provider == ProviderType.OpenAI.value
    assert normalized.endpoint_profile == "deepseek"
    assert normalized.legacy_client_provider == ProviderType.DeepSeek.value


def test_legacy_affinity_provider_normalizes_kv_extension_defaults():
    config = ModelClientConfig(
        client_provider="InferenceAffinity",
        api_key="sk-test",
        api_base="https://example.test",
        verify_ssl=False,
    )

    normalized = normalize_model_client_config(config)

    assert normalized.client_provider == ProviderType.OpenAI.value
    assert normalized.endpoint_profile == "openai_compatible"
    assert normalized.extensions.kv_cache.mode == "release"
    assert normalized.legacy_client_provider == ProviderType.InferenceAffinity.value


def test_legacy_alias_does_not_override_explicit_auth_or_extensions():
    config = ModelClientConfig(
        client_provider="AscendAffinity",
        api_key="sk-test",
        api_base="https://example.test",
        auth_mode=LLMAuthMode.ApiKey,
        extensions={"kv_cache": {"mode": "none"}},
        verify_ssl=False,
    )

    normalized = normalize_model_client_config(config)

    assert normalized.client_provider == ProviderType.OpenAI.value
    assert normalized.auth_mode == LLMAuthMode.ApiKey.value
    assert normalized.extensions.kv_cache.mode == "none"


def test_openai_deepseek_profile_routes_to_openai_client_with_profile():
    config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="deepseek",
        api_key="sk-test",
        api_base="https://api.deepseek.com/v1",
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, OpenAIModelClient)
    assert client.model_client_config.endpoint_profile == "deepseek"


def test_openai_account_oauth_shape_routes_to_account_client():
    config = ModelClientConfig(
        client_provider="OpenAI",
        api_mode=LLMApiMode.Responses,
        auth_mode=LLMAuthMode.OpenAIAccountOAuth,
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, OpenAIAccountModelClient)


def test_openai_account_alias_applies_when_auth_mode_unset():
    config = ModelClientConfig(
        client_provider="OpenAIAccount",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )

    normalized = normalize_model_client_config(config)

    assert normalized.client_provider == ProviderType.OpenAI.value
    assert normalized.auth_mode == LLMAuthMode.OpenAIAccountOAuth.value
    assert normalized.api_mode == LLMApiMode.Responses.value
    assert normalized.legacy_client_provider == ProviderType.OpenAIAccount.value


def test_openai_account_alias_applies_when_default_auth_mode_was_materialized():
    original = ModelClientConfig(
        client_provider="OpenAIAccount",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )
    polluted = ModelClientConfig(**original.model_dump())
    assert "auth_mode" in polluted.model_fields_set
    assert polluted.auth_mode in (LLMAuthMode.ApiKey, LLMAuthMode.ApiKey.value)

    normalized = normalize_model_client_config(polluted)
    client = create_model_client(polluted, _request_config())

    assert normalized.auth_mode == LLMAuthMode.OpenAIAccountOAuth.value
    assert normalized.api_mode == LLMApiMode.Responses.value
    assert isinstance(client, OpenAIAccountModelClient)


def test_openai_responses_api_key_shape_stays_on_openai_client():
    config = ModelClientConfig(
        client_provider="OpenAI",
        api_mode=LLMApiMode.Responses,
        auth_mode=LLMAuthMode.ApiKey,
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, OpenAIModelClient)


def test_anthropic_provider_routes_to_anthropic_client():
    config = ModelClientConfig(
        client_provider="Anthropic",
        api_key="sk-ant-test",
        api_base="https://api.anthropic.com",
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, AnthropicModelClient)


def test_openai_profiles_route_to_unified_openai_client():
    for profile in (
        "openrouter",
        "siliconflow",
        "dashscope",
        "moonshot",
        "minimax",
        "modelarts",
        "qianfan",
        "volcengine",
        "zhipu",
        "mimo",
    ):
        config = ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile=profile,
            api_key="sk-test",
            api_base="https://example.test/v1",
            verify_ssl=False,
        )

        client = create_model_client(config, _request_config())

        assert isinstance(client, OpenAIModelClient)
        assert client.model_client_config.endpoint_profile == profile


def test_vendor_aliases_route_to_openai_client_by_default():
    for provider, profile in (
        ("Moonshot", "moonshot"),
        ("MiniMax", "minimax"),
        ("ModelArts", "modelarts"),
        ("VolcEngine", "volcengine"),
        ("Qianfan", "qianfan"),
        ("Zhipu", "zhipu"),
        ("MiMo", "mimo"),
    ):
        config = ModelClientConfig(
            client_provider=provider,
            api_key="sk-test",
            api_base="https://example.test/v1",
            verify_ssl=False,
        )

        client = create_model_client(config, _request_config())

        assert isinstance(client, OpenAIModelClient)
        assert client.model_client_config.client_provider == ProviderType.OpenAI.value
        assert client.model_client_config.endpoint_profile == profile
        assert client.model_client_config.legacy_client_provider == provider


def test_vendor_aliases_route_to_anthropic_client_when_api_mode_requests_messages():
    for provider, profile in (
        ("Moonshot", "moonshot"),
        ("MiniMax", "minimax"),
        ("ModelArts", "modelarts"),
        ("VolcEngine", "volcengine"),
        ("Qianfan", "qianfan"),
        ("Zhipu", "zhipu"),
        ("MiMo", "mimo"),
    ):
        config = ModelClientConfig(
            client_provider=provider,
            api_mode=LLMApiMode.AnthropicMessages,
            api_key="sk-test",
            api_base="https://example.test",
            verify_ssl=False,
        )

        client = create_model_client(config, _request_config())

        assert isinstance(client, AnthropicModelClient)
        assert client.model_client_config.client_provider == ProviderType.Anthropic.value
        assert client.model_client_config.endpoint_profile == profile
        assert client.model_client_config.legacy_client_provider == provider


def test_openai_provider_with_anthropic_api_mode_routes_to_anthropic_client():
    config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="minimax",
        api_mode=LLMApiMode.AnthropicMessages,
        api_key="sk-test",
        api_base="https://api.minimaxi.com/anthropic",
        verify_ssl=False,
    )

    client = create_model_client(config, _request_config())

    assert isinstance(client, AnthropicModelClient)
    assert client.model_client_config.endpoint_profile == "minimax"


def test_openai_kv_extensions_route_to_unified_openai_client():
    release_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test",
        api_base="https://example.test",
        extensions={"kv_cache": {"mode": "release"}},
        verify_ssl=False,
    )
    affinity_config = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test",
        auth_mode=LLMAuthMode.CustomHeaders,
        extensions={"kv_cache": {"mode": "affinity"}},
        verify_ssl=False,
    )

    release_client = create_model_client(release_config, _request_config())
    affinity_client = create_model_client(affinity_config, _request_config())

    assert isinstance(release_client, OpenAIModelClient)
    assert release_client.supports_kv_cache_release()
    assert isinstance(affinity_client, OpenAIModelClient)
    assert affinity_client.supports_kv_cache_affinity()


def _plain_openai_config(**overrides) -> ModelClientConfig:
    data = {
        "client_provider": "OpenAI",
        "api_key": "sk-test",
        "api_base": "https://example.test",
        "verify_ssl": False,
    }
    data.update(overrides)
    return ModelClientConfig(**data)


def test_model_supports_kv_cache_release_false_for_plain_openai():
    model = Model(_plain_openai_config(), _request_config())

    assert model.supports_kv_cache_release() is False
    assert model.supports_kv_cache_affinity() is False


def test_model_supports_kv_cache_release_true_for_legacy_inference_affinity():
    model = Model(
        _plain_openai_config(client_provider="InferenceAffinity"),
        _request_config(),
    )

    assert model.supports_kv_cache_release() is True
    assert model.supports_kv_cache_affinity() is False


def test_model_supports_kv_cache_release_true_for_explicit_release_extension():
    model = Model(
        _plain_openai_config(extensions={"kv_cache": {"mode": "release"}}),
        _request_config(),
    )

    assert model.supports_kv_cache_release() is True


def test_inference_affinity_model_enables_release_for_openai_provider():
    model = InferenceAffinityModel(
        model_client_config=_plain_openai_config(),
        model_config=_request_config(),
    )

    assert model.model_client_config.extensions.kv_cache.mode == "release"
    assert model._client.supports_kv_cache_release() is True
    assert model.supports_kv_cache_release() is True
