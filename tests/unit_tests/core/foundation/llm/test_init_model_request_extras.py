# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""init_model must forward reasoning / extra_body into ModelRequestConfig."""

from __future__ import annotations

from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    build_request_body,
    expand_nested_extra_body,
)


def test_init_model_folds_thinking_fields_into_extra_body() -> None:
    model = init_model(
        provider="OpenAI",
        model_name="test-model",
        api_key="sk-test",
        api_base="https://example.invalid/v1",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
        enable_thinking=True,
    )
    extras = model.model_config.model_extra or {}
    assert extras.get("reasoning_effort") == "max"
    assert extras.get("extra_body") == {
        "thinking": {"type": "enabled"},
        "enable_thinking": True,
    }
    assert "enable_thinking" not in extras
    assert model.model_config.top_p == 0.95


def test_expand_nested_extra_body_flattens_one_level() -> None:
    assert expand_nested_extra_body(None) == {}
    assert expand_nested_extra_body(
        {
            "extra_body": {"thinking": {"type": "enabled"}},
            "reasoning_effort": "max",
        }
    ) == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }
    # Outer keys win over nested keys on conflict.
    assert expand_nested_extra_body(
        {
            "extra_body": {"thinking": {"type": "disabled"}, "keep": 1},
            "thinking": {"type": "enabled"},
        }
    ) == {
        "keep": 1,
        "thinking": {"type": "enabled"},
    }


def test_build_request_body_places_thinking_at_root() -> None:
    body = build_request_body(
        model="gpt-test",
        messages="hello",
        include_reasoning_encrypted_content=False,
        extra_body={
            "extra_body": {"thinking": {"type": "enabled"}},
            "reasoning_effort": "max",
        },
    )
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"
    assert "extra_body" not in body


def test_openai_responses_request_body_flattens_config_extra_body() -> None:
    client = OpenAIModelClient(
        ModelRequestConfig(
            model="gpt-test",
            extra_body={"thinking": {"type": "enabled"}},
            reasoning_effort="max",
        ),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test",
            api_base="https://example.invalid/v1",
            verify_ssl=False,
        ),
    )
    body = client._build_responses_request_body(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
    )
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"
    assert "extra_body" not in body


def test_move_openai_extra_body_extensions_moves_enable_thinking() -> None:
    params = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hi"}],
        "enable_thinking": True,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    OpenAIModelClient._move_openai_extra_body_extensions(params)
    assert "enable_thinking" not in params
    assert params["extra_body"] == {
        "thinking": {"type": "enabled"},
        "enable_thinking": True,
    }


def test_chat_params_from_init_model_keep_enable_thinking_in_extra_body() -> None:
    model = init_model(
        provider="OpenAI",
        model_name="test-model",
        api_key="sk-test",
        api_base="https://example.invalid/v1",
        enable_thinking=True,
        extra_body={"thinking": {"type": "enabled"}},
    )
    client = model._client
    assert isinstance(client, OpenAIModelClient)

    params = client._build_request_params(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )
    client._move_openai_extra_body_extensions(params)

    assert "enable_thinking" not in params
    assert params["extra_body"]["enable_thinking"] is True
    assert params["extra_body"]["thinking"] == {"type": "enabled"}
