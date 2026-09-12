# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for model selection routing compiler."""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.routing import compile_model_selection
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
    assert compiled.model_request_config.top_p == 0.95


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


def test_compile_model_group_to_intelli_router_config():
    compiled = compile_model_selection({
        "model_group_id": "group-1",
        "routing": {
            "strategy": "ordered-failover",
            "timeout_seconds": 300,
            "enable_health_check": True,
            "health_check_interval_seconds": 60,
            "enable_observability": True,
        },
        "request_config": {"max_tokens": 1024, "temperature": None},
        "routes": [
            {
                "route_id": "route-a",
                "model": {
                    "model_id": "m1",
                    "model_name": "qwen-plus",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk-a",
                    "api_base": "https://dashscope.aliyuncs.com/compatible-mode",
                    "endpoint_profile": "dashscope",
                    "fallback_tag": "qwen-plus",
                    "model_description": "cheap model good for general tasks",
                    "client_options": {
                        "custom_headers": {"X-Test": "1"},
                    },
                    "request_defaults": {"top_p": 0.7},
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
        ],
    })

    assert compiled.selected_type == "model_group"
    assert compiled.model_client_config.client_provider == ProviderType.IntelliRouter.value
    router_config = compiled.model_client_config.intelli_router
    assert router_config is not None
    assert router_config.model_group_id == "group-1"
    assert router_config.strategy == "ordered-failover"
    assert router_config.num_retries == 0
    assert router_config.timeout == 300
    assert router_config.enable_health_check is True
    assert router_config.health_check_interval == 60
    assert router_config.enable_observability is True
    assert compiled.model_request_config.model_name == ""
    deployments = router_config.deployments
    assert len(deployments) == 1
    assert deployments[0].route_id == "route-a"
    assert deployments[0].model_id == "m1"
    assert deployments[0].provider == "dashscope"
    assert deployments[0].endpoint_profile == "dashscope"
    assert deployments[0].fallback_tag == "qwen-plus"
    assert deployments[0].model_description == "cheap model good for general tasks"
    assert deployments[0].request_defaults == {"top_p": 0.7, "max_tokens": 1024}
    assert deployments[0].custom_headers == {"X-Test": "1"}


def test_compile_model_group_ignores_unsupported_deployment_client_options():
    compiled = compile_model_selection({
        "model_group_id": "group-1",
        "request_config": {
            "model": "wrong-model",
            "model_name": "also-wrong",
            "max_tokens": 1024,
        },
        "routes": [{
            "route_id": "route-a",
            "request_overrides": {"model": "route-wrong-model", "temperature": 0.2},
            "model": {
                "model_id": "m1",
                "model_name": "qwen-plus",
                "provider": ProviderType.OpenAI.value,
                "api_key": "sk",
                "api_base": "https://api.openai.com",
                "client_options": {
                    "auth_mode": "custom_headers",
                    "api_mode": "responses",
                    "custom_headers": {"X-Test": "1"},
                    "verify_ssl": False,
                },
            },
        }],
    })

    router_config = compiled.model_client_config.intelli_router
    assert router_config is not None
    deployment = router_config.deployments[0]
    deployment_data = deployment.model_dump(exclude_none=True)
    assert "auth_mode" not in deployment_data
    assert "api_mode" not in deployment_data
    assert deployment.custom_headers == {"X-Test": "1"}
    assert deployment.verify_ssl is False
    assert deployment.request_defaults == {"max_tokens": 1024, "temperature": 0.2}


def test_compile_tag_filtered_strategy_kwargs():
    compiled = compile_model_selection({
        "model_group_id": "group-1",
        "routing": {
            "strategy": "tag-filtered",
            "strategy_kwargs": {"fallback_tag": "fast-model"},
        },
        "routes": [{
            "route_id": "route-a",
            "model": {
                "model_id": "m1",
                "model_name": "gpt-4o-mini",
                "provider": ProviderType.OpenAI.value,
                "api_key": "sk",
                "api_base": "https://api.openai.com",
                "fallback_tag": "fast-model",
            },
        }],
    })

    router_config = compiled.model_client_config.intelli_router
    assert router_config is not None
    assert router_config.strategy == "tag-filtered"
    assert router_config.strategy_kwargs == {"fallback_tag": "fast-model"}


def test_compile_tag_filtered_rejects_non_string_fallback_tag():
    with pytest.raises(BaseError):
        compile_model_selection({
            "model_group_id": "group-1",
            "routing": {
                "strategy": "tag-filtered",
                "strategy_kwargs": {"fallback_tag": ["fast-model"]},
            },
            "routes": [{
                "route_id": "route-a",
                "model": {
                    "model_id": "m1",
                    "model_name": "gpt-4o-mini",
                    "provider": ProviderType.OpenAI.value,
                    "api_key": "sk",
                    "api_base": "https://api.openai.com",
                },
            }],
        })
