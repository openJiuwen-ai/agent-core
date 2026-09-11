# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Consistency guards for the declarative reasoning capability tables.

``MODEL_REASONING_FALLBACKS`` intentionally duplicates the "home vendor" rows
from ``REASONING_PROFILES`` so custom endpoints can match by model name. These
tests fail when one table is updated and the other is forgotten.
"""

import pytest

from openjiuwen.core.foundation.llm.reasoning import (
    get_provider_reasoning_rules,
    get_reasoning_capability,
    resolve_reasoning_plan,
)
from openjiuwen.core.foundation.llm.reasoning_profiles import (
    MODEL_REASONING_FALLBACKS,
    REASONING_PROFILES,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
    ReasoningConfig,
)

# Maps a fallback pattern prefix to the vendor whose provider-scoped row is the
# source of truth for that model family's usual wire format.
_HOME_PROVIDER_BY_PREFIX = {
    "gpt-": "openai",
    "claude-": "anthropic",
    "qwen": "dashscope",
    "deepseek-": "deepseek",
    "kimi-": "moonshot",
    "minimax-": "minimax",
    "glm-": "zhipu",
    "codegeex-": "zhipu",
    "openpangu-": "modelarts",
    "seed-": "volcengine",
    "doubao-": "volcengine",
    "ernie-": "qianfan",
    "emie-": "qianfan",
    "qianfan-": "qianfan",
    "mimo-": "mimo",
    "mino-": "mimo",
}


def _home_provider(pattern: str) -> str:
    for prefix, provider in _HOME_PROVIDER_BY_PREFIX.items():
        if pattern.startswith(prefix):
            return provider
    raise AssertionError(
        f"Fallback pattern {pattern!r} has no home provider mapping; "
        "extend _HOME_PROVIDER_BY_PREFIX together with the new fallback."
    )


def _provider_profile_for_pattern(provider: str, pattern: str):
    for profile in REASONING_PROFILES:
        if profile.provider == provider and pattern in profile.model_patterns:
            return profile
    return None


def test_fallback_rows_are_cross_provider() -> None:
    assert all(profile.provider == "*" for profile in MODEL_REASONING_FALLBACKS)


def test_fallback_rows_stay_in_sync_with_home_provider_rows() -> None:
    for fallback in MODEL_REASONING_FALLBACKS:
        for pattern in fallback.model_patterns:
            provider = _home_provider(pattern)
            source = _provider_profile_for_pattern(provider, pattern)
            assert source is not None, (
                f"Fallback pattern {pattern!r} has no matching {provider!r} row in "
                "REASONING_PROFILES; update both tables together."
            )
            assert fallback.capability == source.capability, (
                f"Fallback capability for {pattern!r} diverged from the {provider!r} row."
            )
            assert fallback.openai_wire == source.openai_wire, (
                f"Fallback openai wire for {pattern!r} diverged from the {provider!r} row."
            )
            assert fallback.anthropic_wire == source.anthropic_wire, (
                f"Fallback anthropic wire for {pattern!r} diverged from the {provider!r} row."
            )
            assert fallback.anthropic_capability == source.anthropic_capability, (
                f"Fallback anthropic capability for {pattern!r} diverged from the {provider!r} row."
            )


def test_provider_reasoning_rules_expose_provider_scoped_patterns() -> None:
    zhipu_rules = get_provider_reasoning_rules(provider="Zhipu", endpoint_profile="zhipu")
    glm52 = next(rule for rule in zhipu_rules if "glm-5.2*" in rule["patterns"])
    assert glm52["capabilities"]["openai"]["options"] == ["off", "high", "max"]

    # Qianfan proxies GLM with a plain toggle: provider rules must reflect the
    # gateway-specific capability, not the home-vendor fallback.
    qianfan_rules = get_provider_reasoning_rules(
        provider="Qianfan",
        api_base="https://qianfan.baidubce.com/v2",
        endpoint_profile="qianfan",
    )
    glm52_proxy = next(rule for rule in qianfan_rules if "glm-5.2*" in rule["patterns"])
    assert glm52_proxy["capabilities"]["openai"]["options"] == ["off", "on"]


def test_provider_reasoning_rules_for_unknown_provider_are_empty() -> None:
    assert get_provider_reasoning_rules(provider="SomeGateway", api_base="http://10.0.0.1:8080/v1") == []


def test_provider_reasoning_rules_for_plain_openai_match_runtime_lookup() -> None:
    rules = get_provider_reasoning_rules(provider="OpenAI", api_base="https://api.openai.com/v1")
    assert any("gpt-5.6*" in rule["patterns"] for rule in rules)


def _vllm_client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="vllm",
        api_base="http://10.0.0.1:8080/v1",
        api_key="sk-test",
        verify_ssl=False,
    )


def test_vllm_profile_keeps_model_family_capability() -> None:
    # Wire is forced to DashScope-style enable_thinking, but the selectable
    # levels still follow the model-name fallback table.
    glm = get_reasoning_capability(
        provider="OpenAI",
        model="GLM-5.2",
        protocol="openai",
        endpoint_profile="vllm",
        api_base="http://10.0.0.1:8080/v1/",
    )
    deepseek = get_reasoning_capability(
        provider="OpenAI",
        model="Deepseek-V4-Flash-0731",
        protocol="openai",
        endpoint_profile="vllm",
    )
    qwen = get_reasoning_capability(
        provider="OpenAI",
        model="qwen3.8-max",
        protocol="openai",
        endpoint_profile="vllm",
    )
    assert glm.to_dict() == {"options": ["off", "high", "max"], "recommended": "max"}
    assert deepseek.to_dict() == {"options": ["off", "low", "high", "max"], "recommended": "high"}
    assert qwen.to_dict() == {"options": ["off", "low", "medium", "xhigh"], "recommended": "xhigh"}


@pytest.mark.parametrize(
    ("mode", "enabled"),
    [("disabled", False), ("enabled", True)],
)
def test_vllm_profile_overrides_model_name_fallback_wire(mode: str, enabled: bool) -> None:
    # Self-hosted gateways ignore the official GLM/DeepSeek thinking.type
    # control even for those model names; the vllm wire must win over the
    # model-name fallbacks and emit DashScope-style enable_thinking instead.
    plan = resolve_reasoning_plan(
        _vllm_client_config(),
        ModelRequestConfig(model="GLM-5.2", reasoning=ReasoningConfig(mode=mode)),
        request_model="GLM-5.2",
    )
    assert plan.sdk_params == {}
    assert plan.extra_body == {
        "enable_thinking": enabled,
        "chat_template_kwargs": {"enable_thinking": enabled},
    }


def test_vllm_profile_maps_effort_to_dashscope_thinking_budget() -> None:
    plan = resolve_reasoning_plan(
        _vllm_client_config(),
        ModelRequestConfig(
            model="GLM-5.2",
            reasoning=ReasoningConfig(mode="enabled", effort="high"),
        ),
        request_model="GLM-5.2",
    )
    assert plan.sdk_params == {}
    assert plan.extra_body == {
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget": 8192,
    }


def test_vllm_profile_maps_xhigh_effort_for_qwen38() -> None:
    plan = resolve_reasoning_plan(
        _vllm_client_config(),
        ModelRequestConfig(
            model="qwen3.8-max",
            reasoning=ReasoningConfig(mode="enabled", effort="xhigh"),
        ),
        request_model="qwen3.8-max",
    )
    assert plan.extra_body["enable_thinking"] is True
    assert plan.extra_body["thinking_budget"] == 16384
    # reasoning_effort must not accompany thinking_budget (DashScope rejects
    # the pair, and the probed gateway ignores reasoning_effort anyway).
    assert "reasoning_effort" not in plan.extra_body


def test_vllm_profile_prefers_explicit_budget_tokens() -> None:
    plan = resolve_reasoning_plan(
        _vllm_client_config(),
        ModelRequestConfig(
            model="GLM-5.2",
            reasoning=ReasoningConfig(mode="enabled", effort="high", budget_tokens=2048),
        ),
        request_model="GLM-5.2",
    )
    assert plan.extra_body == {
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget": 2048,
    }


def test_vllm_profile_auto_mode_sends_no_controls() -> None:
    plan = resolve_reasoning_plan(
        _vllm_client_config(),
        ModelRequestConfig(model="GLM-5.2", reasoning=ReasoningConfig(mode="auto")),
        request_model="GLM-5.2",
    )
    assert plan.sdk_params == {}
    assert plan.extra_body == {}


def test_self_hosted_host_without_profile_falls_back_to_model_name_wire() -> None:
    # No api_base host is hardcoded to the vllm dialect anymore: without an
    # explicit endpoint_profile, an unknown self-hosted host follows the
    # model-name fallback and emits the official GLM thinking.type control.
    plan = resolve_reasoning_plan(
        ModelClientConfig(
            client_provider="OpenAI",
            api_base="http://10.0.0.1:8080/v1/",
            api_key="sk-test",
            verify_ssl=False,
        ),
        ModelRequestConfig(model="GLM-5.2", reasoning=ReasoningConfig(mode="disabled")),
        request_model="GLM-5.2",
    )
    assert plan.sdk_params == {}
    assert plan.extra_body == {"thinking": {"type": "disabled"}}
