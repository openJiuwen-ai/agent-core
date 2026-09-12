# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Focused coverage for provider facts consumed by trajectory observers."""

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _make_client() -> OpenAIModelClient:
    return OpenAIModelClient(
        ModelRequestConfig(model="mock-model"),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test-key",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
    )


@pytest.mark.asyncio
async def test_parse_response_preserves_trace_provider_facts():
    response = _Obj(
        id="resp-1",
        model="returned-model",
        system_fingerprint="fp-1",
        service_tier="default",
        prompt_token_ids=[1, 2],
        usage=_Obj(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
            input_tokens_details=_Obj(cached_tokens=1, cache_creation_tokens=2),
        ),
        choices=[_Obj(message=_Obj(content="ok"), finish_reason="stop", token_ids=[3, 4], logprobs=None)],
    )

    message = await _make_client()._parse_response(response)

    assert message.response_id == "resp-1"
    assert message.response_model == "returned-model"
    assert message.provider_metadata == {"system_fingerprint": "fp-1", "service_tier": "default"}
    assert message.usage_metadata.cache_read_tokens == 1
    assert message.usage_metadata.cache_creation_input_tokens == 2


def test_provider_metadata_excludes_unknown_fields():
    metadata = OpenAIModelClient._response_provider_metadata(
        _Obj(system_fingerprint="fp-1", service_tier="priority", api_key="secret")
    )
    assert metadata == {"system_fingerprint": "fp-1", "service_tier": "priority"}
