# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the SDK request-param allowlist in BaseModelClient."""

from typing import Any, Dict, List, Optional, Union

import pytest

from openjiuwen.core.foundation.llm import AnthropicModelClient
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)


def _make_client(**request_fields: Any) -> AnthropicModelClient:
    client_config = ModelClientConfig(
        client_provider="Anthropic",
        api_key="mock-key",
        api_base="https://api.anthropic.com",
        verify_ssl=False,
    )
    request_config = ModelRequestConfig(model_name="claude-opus-4", **request_fields)
    return AnthropicModelClient(request_config, client_config)


def _params(client: AnthropicModelClient, **overrides: Any) -> Dict[str, Any]:
    kwargs = dict(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=None,
        top_p=None,
        model="claude-opus-4",
        stop=None,
        max_tokens=None,
        stream=False,
    )
    kwargs.update(overrides)
    return client._build_anthropic_params(**kwargs)


class TestSdkParamAllowlist:
    def test_unknown_extra_fields_are_dropped_before_sdk_call(self):
        client = _make_client(
            reasoning_level="max",
            my_custom_flag="hello",
        )
        params = _params(client)
        assert "reasoning_level" not in params
        assert "my_custom_flag" not in params

    def test_whitelisted_native_fields_are_forwarded(self):
        client = _make_client(
            extra_body={"vendor_flag": True},
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
        )
        params = _params(client)
        assert params["extra_body"]["vendor_flag"] is True
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"] == {"effort": "high"}

    def test_sampling_and_model_fields_still_explicit(self):
        client = _make_client(temperature=0.4)
        params = _params(client)
        # temperature is consumed by the explicit sampling branch; the
        # allowlist path must not duplicate it as a raw SDK kwarg.
        assert "temperature" not in params
