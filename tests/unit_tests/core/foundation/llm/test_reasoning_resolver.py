# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
    ReasoningConfig,
    get_reasoning_capability,
    get_reasoning_capability_catalog,
)
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import OpenAIAccountModelClient
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.reasoning import reasoning_request_controls, resolve_reasoning_plan
from openjiuwen.core.foundation.llm.schema.config import LLMApiMode, LLMAuthMode, ProviderType
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import normalize_model_client_config


def _openai_config(**overrides) -> ModelClientConfig:
    data = {
        "client_provider": "OpenAI",
        "api_key": "sk-test",
        "api_base": "https://api.openai.com/v1",
    }
    data.update(overrides)
    return ModelClientConfig(**data)


def test_reasoning_config_is_public_recommended_shape() -> None:
    config = ModelRequestConfig(
        model="gpt-5-mini",
        reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=1024),
    )

    assert config.reasoning.mode == "enabled"
    assert config.reasoning.effort == "high"
    assert config.reasoning.budget_tokens == 1024


def test_legacy_raw_reasoning_dict_remains_accepted() -> None:
    config = ModelRequestConfig(model="legacy-model", reasoning={"enabled": False, "budget": 32})

    assert config.reasoning == {"enabled": False, "budget": 32}


def test_reasoning_request_controls_only_exposes_wire_controls() -> None:
    params = {
        "model": "GLM-5.2",
        "messages": [{"role": "user", "content": "secret prompt"}],
        "api_key": "secret-key",
        "extra_body": {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "unrelated": "private",
        },
    }

    assert reasoning_request_controls(params) == {
        "extra_body": {
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        }
    }


def test_normalized_dashscope_still_resolves_provider_identity() -> None:
    normalized = normalize_model_client_config(
        ModelClientConfig(
            client_provider=ProviderType.DashScope,
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    request = ModelRequestConfig(
        model="qwen-plus",
        reasoning=ReasoningConfig(mode="enabled", budget_tokens=4096),
    )

    assert normalized.client_provider == ProviderType.OpenAI.value
    assert normalized.legacy_client_provider == ProviderType.DashScope.value
    assert resolve_reasoning_plan(normalized, request).extra_body == {
        "enable_thinking": True,
        "thinking_budget": 4096,
    }


def test_openai_chat_reasoning_effort_is_added_to_sdk_params() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(model="gpt-5-mini", reasoning=ReasoningConfig(effort="high")),
        _openai_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["reasoning_effort"] == "high"
    assert "reasoning" not in params


def test_openai_chat_disabled_uses_current_none_effort_value() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(model="gpt-5.1", reasoning=ReasoningConfig(mode="disabled")),
        _openai_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["reasoning_effort"] == "none"


def test_dashscope_qwen_reasoning_uses_extra_body_not_sdk_kwargs() -> None:
    client_config = normalize_model_client_config(
        ModelClientConfig(
            client_provider="DashScope",
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="qwen-plus",
            reasoning=ReasoningConfig(mode="enabled", budget_tokens=2048),
        ),
        client_config,
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "enable_thinking" not in params
    assert params["extra_body"] == {"enable_thinking": True, "thinking_budget": 2048}


def test_dashscope_qwen38_does_not_send_effort_and_budget_together() -> None:
    client_config = normalize_model_client_config(
        ModelClientConfig(
            client_provider="DashScope",
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=2048),
        ),
        client_config,
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["extra_body"] == {"enable_thinking": True, "thinking_budget": 2048}


def test_dashscope_qwen38_maps_openai_high_effort_to_xhigh() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
    )

    assert plan.extra_body == {"enable_thinking": True, "reasoning_effort": "xhigh"}


def test_dashscope_anthropic_qwen38_uses_output_config_effort() -> None:
    # Model Studio's Anthropic endpoint deprecates budget_tokens; intensity is
    # its output_config.effort extension (high maps to qwen3.8's xhigh tier).
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        ModelClientConfig(
            client_provider="DashScope",
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/apps/anthropic",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "enabled"}
    assert params["output_config"] == {"effort": "xhigh"}


def _dashscope_anthropic_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="DashScope",
        api_key="sk-test",
        api_base="https://dashscope.aliyuncs.com/apps/anthropic",
        api_mode=LLMApiMode.AnthropicMessages,
    )


def test_dashscope_anthropic_qwen38_explicit_budget_wins_over_effort() -> None:
    # DashScope rejects budget + effort together; an explicit numeric budget
    # must be sent alone.
    plan = resolve_reasoning_plan(
        _dashscope_anthropic_config(),
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=4096),
        ),
    )

    assert plan.sdk_params == {"thinking": {"type": "enabled", "budget_tokens": 4096}}


def test_dashscope_anthropic_qwen38_disabled_sends_thinking_disabled() -> None:
    plan = resolve_reasoning_plan(
        _dashscope_anthropic_config(),
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {"thinking": {"type": "disabled"}}


def test_dashscope_anthropic_glm52_maps_effort_to_output_config() -> None:
    plan = resolve_reasoning_plan(
        _dashscope_anthropic_config(),
        ModelRequestConfig(
            model="glm-5.2",
            reasoning=ReasoningConfig(mode="enabled", effort="xhigh"),
        ),
    )

    assert plan.sdk_params == {
        "thinking": {"type": "enabled"},
        "output_config": {"effort": "max"},
    }


def test_dashscope_anthropic_plain_toggle_enabled_sends_no_budget() -> None:
    # Older Qwen models on the dashscope_budget wire have no effort field;
    # plain "enabled" must use the vendor's default thinking budget instead of
    # pinning the 1024 API minimum.
    plan = resolve_reasoning_plan(
        _dashscope_anthropic_config(),
        ModelRequestConfig(
            model="qwen3.7-plus",
            reasoning=ReasoningConfig(mode="enabled"),
        ),
    )

    assert plan.sdk_params == {"thinking": {"type": "enabled"}}


def test_anthropic_manual_budget_maps_labeled_efforts() -> None:
    # Claude Sonnet 4.5 only supports budget-based thinking; labeled levels
    # encode as budget_tokens (low=2048, medium=8192, high=16384).
    for effort, budget in (("low", 2048), ("medium", 8192), ("high", 16384)):
        plan = resolve_reasoning_plan(
            ModelClientConfig(
                client_provider="Anthropic",
                api_key="sk-ant-test",
                api_base="https://api.anthropic.com",
            ),
            ModelRequestConfig(
                model="claude-sonnet-4-5",
                reasoning=ReasoningConfig(mode="enabled", effort=effort),
            ),
        )
        assert plan.sdk_params == {
            "thinking": {"type": "enabled", "budget_tokens": budget}
        }, f"effort={effort}"


def test_anthropic_manual_budget_plain_enabled_uses_medium_budget() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
        ModelRequestConfig(
            model="claude-sonnet-4-5",
            reasoning=ReasoningConfig(mode="enabled"),
        ),
    )

    assert plan.sdk_params == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


def test_custom_endpoint_claude_sonnet_45_uses_manual_budget_capability() -> None:
    # Without a model-name fallback, custom Anthropic hosts would inherit the
    # protocol default (off/low/medium/high/max). Sonnet 4.5 is budget-only and
    # must not expose max in the UI or save validation would reject it.
    capability = get_reasoning_capability(
        provider="OpenAI",
        model="claude-sonnet-4-5",
        protocol="anthropic",
        api_base="https://my-gateway.example/anthropic",
    )
    assert capability.options == ("off", "low", "medium", "high")
    assert capability.recommended == "medium"

    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://my-gateway.example/anthropic",
        ),
        ModelRequestConfig(
            model="claude-sonnet-4.5-20250929",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
    )
    assert plan.sdk_params == {"thinking": {"type": "enabled", "budget_tokens": 16384}}


def test_anthropic_client_raises_max_tokens_above_thinking_budget() -> None:
    # Both the official API and Anthropic-compatible gateways require
    # max_tokens to exceed a manual thinking budget; the client raises the
    # ceiling instead of shrinking the requested thinking depth.
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="claude-sonnet-4-5",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=4096,
        stream=False,
    )

    assert params["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert params["max_tokens"] == 16384 + 1024


def test_explicit_extra_body_reasoning_controls_are_not_overridden() -> None:
    client_config = normalize_model_client_config(
        ModelClientConfig(
            client_provider="DashScope",
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="qwen-plus",
            reasoning=ReasoningConfig(mode="enabled", budget_tokens=2048),
            extra_body={"thinking_budget": 1024},
        ),
        client_config,
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["extra_body"] == {"thinking_budget": 1024}


def test_per_call_reasoning_config_overrides_model_config_legacy_controls() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(model="gpt-5-mini", reasoning_effort="low"),
        _openai_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        reasoning=ReasoningConfig(effort="high"),
    )

    assert params["reasoning_effort"] == "high"


def test_unknown_openai_compatible_profile_model_does_not_generate_private_thinking_fields() -> None:
    # "vllm" used to be an example of an unknown profile here; it is now a
    # real dialect with its own toggle rules, so use a truly unknown one.
    config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test",
        api_base="https://example.test/v1",
        endpoint_profile="my-inhouse-gateway",
    )
    request = ModelRequestConfig(
        model="brand-new-model",
        reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=2048),
    )

    plan = resolve_reasoning_plan(config, request)

    assert plan.sdk_params == {"reasoning_effort": "high"}
    assert plan.extra_body == {}


def test_anthropic_reasoning_config_generates_native_thinking() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="claude-opus-4",
            reasoning=ReasoningConfig(mode="enabled", budget_tokens=2048),
        ),
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def _deepseek_chat_config(**overrides) -> ModelClientConfig:
    data = {
        "client_provider": "OpenAI",
        "endpoint_profile": "deepseek",
        "api_key": "sk-test",
        "api_base": "https://api.deepseek.com",
    }
    data.update(overrides)
    return ModelClientConfig(**data)


def test_deepseek_profile_enabled_sends_thinking_type_and_effort() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        _deepseek_chat_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["reasoning_effort"] == "high"
    assert params["extra_body"] == {"thinking": {"type": "enabled"}}


def test_deepseek_profile_disabled_sends_thinking_disabled_not_effort_off() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
        _deepseek_chat_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "reasoning_effort" not in params
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}


def test_normalized_deepseek_provider_uses_chat_thinking_toggle() -> None:
    normalized = normalize_model_client_config(
        ModelClientConfig(
            client_provider=ProviderType.DeepSeek,
            api_key="sk-test",
            api_base="https://api.deepseek.com",
        )
    )
    request = ModelRequestConfig(
        model="deepseek-v4-pro",
        reasoning=ReasoningConfig(mode="disabled"),
    )

    plan = resolve_reasoning_plan(normalized, request)

    assert plan.sdk_params == {}
    assert plan.extra_body == {"thinking": {"type": "disabled"}}


def test_deepseek_anthropic_disabled_sends_thinking_disabled() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-test",
            api_base="https://api.deepseek.com/anthropic",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "disabled"}


def test_deepseek_anthropic_enabled_maps_effort_to_output_config() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-test",
            api_base="https://api.deepseek.com/anthropic",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "enabled"}
    assert params["output_config"] == {"effort": "high"}


def test_anthropic_disabled_sends_thinking_type_disabled() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
        ModelRequestConfig(
            model="claude-opus-4",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {"thinking": {"type": "disabled"}}
    assert plan.extra_body == {}


def test_anthropic_enabled_maps_effort_to_output_config() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
        ModelRequestConfig(
            model="claude-opus-4",
            reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=2048),
        ),
    )

    assert plan.sdk_params == {
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "output_config": {"effort": "high"},
    }


def test_anthropic_enabled_without_budget_uses_adaptive_thinking() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
        ModelRequestConfig(
            model="claude-sonnet-5",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
    )

    assert plan.sdk_params == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }


def test_anthropic_auto_without_controls_is_empty_plan() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="sk-ant-test",
            api_base="https://api.anthropic.com",
        ),
        ModelRequestConfig(
            model="claude-opus-4",
            reasoning=ReasoningConfig(mode="auto"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {}


def _moonshot_chat_config(**overrides) -> ModelClientConfig:
    data = {
        "client_provider": "OpenAI",
        "endpoint_profile": "moonshot",
        "api_key": "sk-test",
        "api_base": "https://api.moonshot.cn/v1",
    }
    data.update(overrides)
    return ModelClientConfig(**data)


def _moonshot_anthropic_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="Anthropic",
        endpoint_profile="moonshot",
        api_key="sk-test",
        api_base="https://api.moonshot.cn/anthropic",
        api_mode=LLMApiMode.AnthropicMessages,
    )


def test_moonshot_k26_chat_disabled_sends_thinking_type() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(model="kimi-k2.6", reasoning=ReasoningConfig(mode="disabled")),
        _moonshot_chat_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "reasoning_effort" not in params
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}


def test_moonshot_k26_chat_enabled_sends_thinking_type() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="kimi-k2.6",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        _moonshot_chat_config(),
    )

    params = client._build_request_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "reasoning_effort" not in params
    assert params["extra_body"] == {"thinking": {"type": "enabled"}}


def test_moonshot_k27_code_does_not_send_disabled_thinking() -> None:
    plan = resolve_reasoning_plan(
        _moonshot_chat_config(),
        ModelRequestConfig(
            model="kimi-k2.7-code",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {}


def test_moonshot_k3_uses_reasoning_effort_and_ignores_disable() -> None:
    enabled = resolve_reasoning_plan(
        _moonshot_chat_config(),
        ModelRequestConfig(
            model="kimi-k3",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
    )
    disabled = resolve_reasoning_plan(
        _moonshot_chat_config(),
        ModelRequestConfig(
            model="kimi-k3",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert enabled.sdk_params == {"reasoning_effort": "high"}
    assert enabled.extra_body == {}
    assert disabled.sdk_params == {}
    assert disabled.extra_body == {}


def test_moonshot_k3_maps_xhigh_to_max() -> None:
    plan = resolve_reasoning_plan(
        _moonshot_chat_config(),
        ModelRequestConfig(
            model="kimi-k3",
            reasoning=ReasoningConfig(mode="enabled", effort="xhigh"),
        ),
    )

    assert plan.sdk_params == {"reasoning_effort": "max"}


def test_moonshot_k26_anthropic_disabled_sends_thinking_type() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(model="kimi-k2.6", reasoning=ReasoningConfig(mode="disabled")),
        _moonshot_anthropic_config(),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "disabled"}
    assert "output_config" not in params


def test_moonshot_k3_anthropic_disable_does_not_send_thinking() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(model="kimi-k3", reasoning=ReasoningConfig(mode="disabled")),
        _moonshot_anthropic_config(),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "thinking" not in params


def test_openai_account_responses_reasoning_effort_maps_to_reasoning_body() -> None:
    client = OpenAIAccountModelClient(
        ModelRequestConfig(model="gpt-5-mini", reasoning=ReasoningConfig(effort="high")),
        ModelClientConfig(
            client_provider="OpenAIAccount",
            api_base="https://chatgpt.com/backend-api",
            auth_mode=LLMAuthMode.OpenAIAccountOAuth,
            api_mode=LLMApiMode.Responses,
        ),
    )

    body = client._build_openai_account_request_body(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
    )

    assert body["reasoning"] == {"effort": "high"}


def test_generic_openai_profile_infers_qianfan_from_api_base_for_glm() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://qianfan.baidubce.com/v2",
        ),
        ModelRequestConfig(
            model="glm-5",
            reasoning=ReasoningConfig(mode="disabled", effort="high"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {"thinking": {"type": "disabled"}}


def test_volcengine_deepseek_model_uses_deepseek_wire_shape_from_api_base() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://ark.cn-beijing.volces.com/api/v3",
        ),
        ModelRequestConfig(
            model="deepseek-v4-flash-260425",
            reasoning=ReasoningConfig(mode="enabled", effort="max"),
        ),
    )

    assert plan.sdk_params == {"reasoning_effort": "max"}
    assert plan.extra_body == {"thinking": {"type": "enabled"}}


def test_deepseek_v4_pro_maps_xhigh_to_max() -> None:
    plan = resolve_reasoning_plan(
        _deepseek_chat_config(),
        ModelRequestConfig(
            model="deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="enabled", effort="xhigh"),
        ),
    )

    assert plan.sdk_params == {"reasoning_effort": "max"}
    assert plan.extra_body == {"thinking": {"type": "enabled"}}


def test_modelarts_openpangu_uses_thinking_toggle_from_api_base() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://api.modelarts-maas.com/openai/v1",
        ),
        ModelRequestConfig(
            model="openpangu-2.0-pro",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {"thinking": {"type": "disabled"}}


def test_modelarts_glm52_uses_thinking_and_mapped_effort() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="ModelArts",
            api_key="sk-test",
            api_base="https://api.modelarts-maas.com/openai/v1",
        ),
        ModelRequestConfig(
            model="glm-5.2",
            reasoning=ReasoningConfig(mode="enabled", effort="xhigh"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }


def test_modelarts_anthropic_glm52_effort_goes_through_extra_body() -> None:
    client = AnthropicModelClient(
        ModelRequestConfig(
            model="glm-5.2",
            reasoning=ReasoningConfig(mode="enabled", effort="low"),
        ),
        ModelClientConfig(
            client_provider="ModelArts",
            api_key="sk-test",
            api_base="https://api.modelarts-maas.com/anthropic/v1",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
    )

    params = client._build_anthropic_params(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["thinking"] == {"type": "enabled"}
    assert params["extra_body"] == {"reasoning_effort": "high"}


def test_mimo_v25_uses_thinking_toggle_for_openai_and_anthropic() -> None:
    openai_plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://api.xiaomimimo.com/v1",
        ),
        ModelRequestConfig(
            model="mimo-v2.5-pro",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )
    anthropic_plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="MiMo",
            api_key="sk-test",
            api_base="https://token-plan-cn.xiaomimimo.com/anthropic",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
        ModelRequestConfig(
            model="mimo-v2.5",
            reasoning=ReasoningConfig(mode="enabled"),
        ),
    )

    assert openai_plan.extra_body == {"thinking": {"type": "disabled"}}
    assert anthropic_plan.sdk_params == {"thinking": {"type": "enabled"}}


def test_volcengine_seed_uses_minimal_to_disable_reasoning() -> None:
    disabled = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="VolcEngine",
            api_key="sk-test",
            api_base="https://ark.cn-beijing.volces.com/api/v3",
        ),
        ModelRequestConfig(
            model="seed-2.0-mini",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )
    max_effort = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="VolcEngine",
            api_key="sk-test",
            api_base="https://ark.cn-beijing.volces.com/api/v3",
        ),
        ModelRequestConfig(
            model="seed-2.0-lite",
            reasoning=ReasoningConfig(mode="enabled", effort="max"),
        ),
    )

    assert disabled.sdk_params == {"reasoning_effort": "minimal"}
    assert max_effort.sdk_params == {"reasoning_effort": "high"}


def test_minimax_m27_keeps_reasoning_split_but_does_not_send_disable_toggle() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openai",
            api_key="sk-test",
            api_base="https://api.minimaxi.com/v1",
        ),
        ModelRequestConfig(
            model="MiniMax-M2.7",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {"reasoning_split": True}


def test_minimax_m3_supports_adaptive_and_disabled_toggles() -> None:
    config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="minimax",
        api_key="sk-test",
        api_base="https://api.minimaxi.com/v1",
    )

    enabled = resolve_reasoning_plan(
        config,
        ModelRequestConfig(
            model="MiniMax-M3",
            reasoning=ReasoningConfig(mode="enabled"),
        ),
    )
    disabled = resolve_reasoning_plan(
        config,
        ModelRequestConfig(
            model="MiniMax-M3",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert enabled.extra_body == {"thinking": {"type": "adaptive"}}
    assert disabled.extra_body == {"thinking": {"type": "disabled"}}


def test_openrouter_uses_effort_none_for_disabled_reasoning() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            endpoint_profile="openrouter",
            api_key="sk-test",
            api_base="https://openrouter.ai/api/v1",
        ),
        ModelRequestConfig(
            model="deepseek/deepseek-v4-pro",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.extra_body == {"reasoning": {"effort": "none"}}


def test_openrouter_anthropic_mode_uses_gateway_reasoning_shape() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-test",
            api_base="https://openrouter.ai/api",
            api_mode=LLMApiMode.AnthropicMessages,
        ),
        ModelRequestConfig(
            model="anthropic/claude-sonnet-4.5",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {"reasoning": {"enabled": True, "effort": "high"}}


def test_openrouter_listed_aggregated_prefixes_use_unified_reasoning_shape() -> None:
    for model in (
        "poolside/laguna-s-2.1:free",
        "z-ai/glm-5.2",
        "moonshotai/kimi-k3",
        "minimax/minimax-m3",
        "google/gemini-2.5-flash",
    ):
        plan = resolve_reasoning_plan(
            ModelClientConfig(
                client_provider="OpenRouter",
                api_key="sk-test",
                api_base="https://openrouter.ai/api/v1",
            ),
            ModelRequestConfig(
                model=model,
                reasoning=ReasoningConfig(mode="enabled", effort="high"),
            ),
        )

        assert plan.extra_body == {"reasoning": {"enabled": True, "effort": "high"}}


def test_reasoning_capability_is_provider_scoped() -> None:
    official = get_reasoning_capability(
        provider="DeepSeek",
        model="deepseek-v4-pro",
        protocol="openai",
    )
    modelarts = get_reasoning_capability(
        provider="ModelArts",
        model="deepseek-v4-pro",
        protocol="openai",
    )

    assert official.options == ("off", "low", "high", "max")
    assert modelarts.options == ("off", "on")


def test_reasoning_capability_describes_always_on_and_unsupported_models() -> None:
    kimi = get_reasoning_capability(provider="Moonshot", model="kimi-k3", protocol="openai")
    codegeex = get_reasoning_capability(provider="Zhipu", model="codegeex-4", protocol="openai")

    assert kimi.options == ("low", "high", "max")
    assert codegeex.options == ()


def test_always_on_profile_ignores_an_invalid_disable_request() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-test",
            api_base="https://openrouter.ai/api/v1",
        ),
        ModelRequestConfig(
            model="moonshotai/kimi-k3",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {}


def test_dashscope_qwen38_capability_aligned_across_protocols() -> None:
    # The Anthropic-compatible endpoint exposes output_config.effort for
    # qwen3.8-max, so both protocols offer the same labeled levels.
    openai = get_reasoning_capability(
        provider="DashScope",
        model="qwen3.8-max",
        protocol="openai",
    )
    anthropic = get_reasoning_capability(
        provider="Anthropic",
        model="qwen3.8-max",
        protocol="anthropic",
        api_base="https://token-plan.cn-beijing.maas.aliyuncs.com/anthropic",
    )

    assert openai.options == ("off", "low", "medium", "xhigh")
    assert anthropic.options == ("off", "low", "medium", "xhigh")


def test_unlisted_models_inherit_protocol_defaults() -> None:
    openai = get_reasoning_capability(provider="custom", model="future-model", protocol="openai")
    anthropic = get_reasoning_capability(
        provider="custom",
        model="future-model",
        protocol="anthropic",
    )

    assert openai.options == ("off", "low", "medium", "high")
    assert anthropic.options == ("off", "low", "medium", "high", "max")


def test_api_base_can_identify_provider_for_capability_lookup() -> None:
    capability = get_reasoning_capability(
        provider="OpenAI",
        model="qwen3.8-max",
        protocol="openai",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert capability.options == ("off", "low", "medium", "xhigh")
    assert capability.recommended == "xhigh"


def test_openai_model_families_do_not_share_an_impossible_effort_union() -> None:
    original = get_reasoning_capability(provider="OpenAI", model="gpt-5", protocol="openai")
    gpt_54 = get_reasoning_capability(provider="OpenAI", model="gpt-5.4", protocol="openai")
    gpt_56 = get_reasoning_capability(provider="OpenAI", model="gpt-5.6", protocol="openai")

    assert original.options == ("minimal", "low", "medium", "high")
    assert gpt_54.options == ("off", "low", "medium", "high", "xhigh")
    assert gpt_56.options == ("off", "low", "medium", "high", "xhigh", "max")


def test_custom_endpoint_uses_model_name_before_protocol_default() -> None:
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test",
                api_base="http://10.0.0.1:8080/v1/",
        ),
        ModelRequestConfig(
            model="GLM-5.2",
            reasoning=ReasoningConfig(mode="disabled"),
        ),
    )

    assert plan.sdk_params == {}
    assert plan.extra_body == {"thinking": {"type": "disabled"}}


def test_custom_model_prefix_is_ignored_for_model_name_fallback() -> None:
    capability = get_reasoning_capability(
        provider="custom",
        model="z-ai/GLM-5.2",
        protocol="openai",
    )

    assert capability.options == ("off", "high", "max")


def test_frontend_capability_catalog_is_compact_and_contains_model_fallbacks() -> None:
    catalog = get_reasoning_capability_catalog()
    glm = next(item for item in catalog["model_fallbacks"] if "glm-5.2*" in item["patterns"])

    assert catalog["protocol_defaults"]["openai"] == {
        "options": ["off", "low", "medium", "high"],
        "recommended": None,
    }
    assert glm["capabilities"]["openai"] == {
        "options": ["off", "high", "max"],
        "recommended": "max",
    }
