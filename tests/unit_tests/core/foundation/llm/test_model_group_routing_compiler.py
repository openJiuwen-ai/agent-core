# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for model selection routing compiler."""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.routing import (
    ModelGroupModelCandidate,
    ResolvedModelGroup,
    compile_model_selection,
    get_model_group_models,
)
from openjiuwen.core.foundation.llm.schema.config import ProviderType


def test_compile_single_model_preserves_explicit_request_defaults():
    compiled = compile_model_selection({
        "model_id": "m1",
        "model_name": "qwen-plus",
        "provider": ProviderType.DashScope.value,
        "api_key": "sk",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode",
        "endpoint_profile": "dashscope",
        "request_defaults": {"temperature": 0.4, "top_p": None},
    })

    assert compiled.selected_type == "model"
    assert compiled.model_client_config.client_provider == ProviderType.DashScope.value
    assert compiled.model_client_config.endpoint_profile == "dashscope"
    assert compiled.model_request_config.model_name == "qwen-plus"
    assert compiled.model_request_config.temperature == 0.4
    assert compiled.model_request_config.top_p is None


def test_compile_single_model_ignores_model_request_default_aliases():
    compiled = compile_model_selection({
        "model_id": "m1",
        "model_name": "qwen-plus",
        "provider": ProviderType.DashScope.value,
        "api_key": "sk",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode",
        "endpoint_profile": "dashscope",
        "request_defaults": {
            "model": "wrong-model",
            "model_name": "also-wrong",
            "temperature": 0.4,
        },
    })

    assert compiled.model_request_config.model_name == "qwen-plus"
    assert compiled.model_request_config.temperature == 0.4


def test_compile_model_group_without_strategy_uses_first_enabled_route_as_single_model():
    compiled = compile_model_selection({
        "model_group_id": "group-1",
        "request_config": {"max_tokens": 1024},
        "routes": [
            {
                "route_id": "route-a",
                "model": {
                    "model_id": "m1",
                    "model_name": "qwen-plus",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://api.openai.com",
                    "request_defaults": {"context_window": 100000, "temperature": 0.4},
                },
            },
            {
                "route_id": "route-b",
                "model": {
                    "model_id": "m2",
                    "model_name": "deepseek-chat",
                    "provider": ProviderType.DeepSeek.value,
                    "api_key": "sk-b",
                    "api_base": "https://api.deepseek.com",
                    "request_defaults": {"context_window": 10000},
                },
            },
        ],
    })

    assert compiled.selected_type == "model_group"
    assert compiled.model_client_config.client_provider == ProviderType.OpenAI.value
    assert compiled.model_client_config.intelli_router is None
    assert compiled.model_client_config.api_key == "sk-a"
    assert compiled.model_client_config.api_base == "https://api.openai.com"
    assert compiled.model_request_config.model_name == "qwen-plus"
    assert compiled.model_request_config.context_window == 100000
    assert compiled.model_request_config.temperature == 0.4
    assert compiled.model_request_config.max_tokens == 1024


def test_compile_model_group_none_strategy_preserves_reasoning_for_single_model_client():
    compiled = compile_model_selection({
        "model_group_id": "group-1",
        "routing": {"strategy": None},
        "routes": [{
            "route_id": "route-a",
            "model": {
                "model_id": "m1",
                "model_name": "claude-sonnet-4-5",
                "provider": ProviderType.Anthropic.value,
                "api_key": "sk",
                "api_base": "https://api.anthropic.com",
                "request_defaults": {
                    "reasoning": {"mode": "enabled", "budget_tokens": 2048},
                },
            },
        }],
    })

    assert compiled.model_client_config.client_provider == ProviderType.Anthropic.value
    assert compiled.model_client_config.intelli_router is None
    assert compiled.model_request_config.model_name == "claude-sonnet-4-5"
    assert compiled.model_request_config.reasoning is not None
    assert compiled.model_request_config.reasoning.mode == "enabled"
    assert compiled.model_request_config.reasoning.budget_tokens == 2048


@pytest.mark.parametrize("strategy", ["ordered-failover", "tag-filtered"])
def test_compile_model_group_rejects_routing_strategy_until_protocol_dependency_is_enabled(strategy):
    with pytest.raises(BaseError, match="routing.strategy=.*not supported yet"):
        compile_model_selection({
            "model_group_id": "group-1",
            "routing": {"strategy": strategy},
            "routes": [{
                "route_id": "route-a",
                "model": {
                    "model_id": "m1",
                    "model_name": "qwen-plus",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://api.openai.com",
                },
            }],
        })


@pytest.mark.parametrize("strategy", ["", "none"])
def test_compile_model_group_rejects_invalid_no_routing_strategy_strings(strategy):
    with pytest.raises(BaseError):
        compile_model_selection({
            "model_group_id": "group-1",
            "routing": {"strategy": strategy},
            "routes": [{
                "route_id": "route-a",
                "model": {
                    "model_id": "m1",
                    "model_name": "qwen-plus",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://api.openai.com",
                },
            }],
        })


def test_compile_model_group_rejects_when_no_enabled_routes():
    with pytest.raises(BaseError):
        compile_model_selection({
            "model_group_id": "group-1",
            "routes": [{
                "route_id": "route-a",
                "enabled": False,
                "model": {
                    "model_id": "m1",
                    "model_name": "qwen-plus",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://api.openai.com",
                },
            }],
        })


def test_get_model_group_models_builds_callable_candidates_from_enabled_routes():
    model_group = ResolvedModelGroup.model_validate({
        "model_group_id": "group-1",
        "request_config": {"max_tokens": 1024},
        "routes": [
            {
                "route_id": "route-a",
                "request_overrides": {"top_p": 0.8},
                "model": {
                    "model_id": "m1",
                    "model_name": "deepseek-chat",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://api.deepseek.com",
                    "endpoint_profile": "deepseek",
                    "fallback_tag": "general",
                    "model_description": "general model",
                    "request_defaults": {
                        "temperature": 0.2,
                        "reasoning": {"mode": "enabled", "effort": "high"},
                    },
                },
            },
            {
                "route_id": "route-b",
                "enabled": False,
                "model": {
                    "model_id": "m2",
                    "model_name": "deepseek-chat",
                    "provider": ProviderType.DeepSeek.value,
                    "api_key": "sk-b",
                    "api_base": "https://api.deepseek.com",
                },
            },
            {
                "route_id": "route-c",
                "model": {
                    "model_id": "m3",
                    "model_name": "claude-sonnet-4-5",
                    "provider": ProviderType.Anthropic.value,
                    "api_key": "sk-c",
                    "api_base": "https://api.anthropic.com",
                },
            },
        ],
    })

    candidates = get_model_group_models(model_group)

    assert all(isinstance(candidate, ModelGroupModelCandidate) for candidate in candidates)
    assert [candidate.route_id for candidate in candidates] == ["route-a", "route-c"]
    assert [candidate.model_id for candidate in candidates] == ["m1", "m3"]
    assert candidates[0].model_name == "deepseek-chat"
    assert candidates[0].client_provider == ProviderType.OpenAI.value
    assert candidates[0].provider_key == "deepseek"
    assert candidates[0].fallback_tag == "general"
    assert candidates[0].model_description == "general model"
    assert candidates[0].model.model_client_config.api_key == "sk-a"
    assert candidates[0].model.model_client_config.endpoint_profile == "deepseek"
    assert candidates[0].model.model_config.model_name == "deepseek-chat"
    assert candidates[0].model.model_config.temperature == 0.2
    assert candidates[0].model.model_config.top_p == 0.8
    assert candidates[0].model.model_config.max_tokens == 1024
    assert candidates[0].model.model_config.reasoning is not None
    assert candidates[0].model.model_config.reasoning.effort == "high"
