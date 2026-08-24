# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.llm.schema.config import LLMAuthMode, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


def test_max_output_tokens_is_alias_of_max_tokens():
    by_legacy = ModelRequestConfig(model="gpt-4", max_tokens=256)
    by_alias = ModelRequestConfig(model="gpt-4", max_output_tokens=256)

    assert by_legacy.max_tokens == 256
    assert by_legacy.max_output_tokens == 256
    assert by_alias.max_tokens == 256
    assert by_alias.max_output_tokens == 256


def test_max_input_tokens_defaults_to_none_and_rejects_non_positive():
    cfg = ModelRequestConfig(model="gpt-4")
    assert cfg.max_input_tokens is None

    with pytest.raises(ValidationError):
        ModelRequestConfig(model="gpt-4", max_input_tokens=0)


def test_model_dump_keeps_max_tokens_and_includes_max_input_tokens():
    cfg = ModelRequestConfig(model="gpt-4", max_output_tokens=128, max_input_tokens=12000)
    dumped = cfg.model_dump()

    assert dumped["max_tokens"] == 128
    assert dumped["max_input_tokens"] == 12000
    assert "max_output_tokens" not in dumped


def test_request_payload_sends_max_tokens_but_not_max_input_tokens():
    client = OpenAIModelClient(
        ModelRequestConfig(model="gpt-4", max_tokens=256, max_input_tokens=12000),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test",
            api_base="https://example.test/v1",
            auth_mode=LLMAuthMode.ApiKey,
            verify_ssl=False,
        ),
    )

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

    assert params["max_tokens"] == 256
    assert "max_input_tokens" not in params
    extra_body = params.get("extra_body") or {}
    assert "max_input_tokens" not in extra_body
