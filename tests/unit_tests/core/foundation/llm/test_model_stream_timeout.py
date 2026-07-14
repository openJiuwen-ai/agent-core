# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import re

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    AssistantMessageChunk,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)


def _build_model_with_stream(
        stream_fn,
        *,
        first_timeout: float = 10.0,
        idle_timeout: float = 10.0,
) -> Model:
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
            stream_first_chunk_timeout=first_timeout,
            stream_idle_timeout=idle_timeout,
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )
    model._client.stream = stream_fn
    return model


@pytest.mark.asyncio
async def test_model_stream_raises_first_chunk_timeout():
    async def slow_first_chunk_stream(**kwargs):
        await asyncio.sleep(0.05)
        yield AssistantMessageChunk(content="late-first")

    model = _build_model_with_stream(
        slow_first_chunk_stream,
        first_timeout=0.01,
        idle_timeout=1.0,
    )

    with pytest.raises(BaseError) as exc_info:
        async for _ in model.stream(messages=[]):
            pass

    message = str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert "LLM stream timeout" in message
    assert "stage=first_chunk" in message
    assert "chunk_count=0" in message
    assert "first_chunk_elapsed=" in message
    assert "total_elapsed=" in message
    assert "model=mock-model" in message


@pytest.mark.asyncio
async def test_model_stream_raises_idle_timeout_on_third_frame():
    async def slow_third_frame_stream(**kwargs):
        yield AssistantMessageChunk(content="frame-1")
        await asyncio.sleep(0.02)
        yield AssistantMessageChunk(content="frame-2")
        await asyncio.sleep(0.12)
        yield AssistantMessageChunk(content="frame-3")

    model = _build_model_with_stream(
        slow_third_frame_stream,
        first_timeout=1.0,
        idle_timeout=0.1,
    )
    received = []

    with pytest.raises(BaseError) as exc_info:
        async for chunk in model.stream(messages=[]):
            received.append(chunk.content)

    message = str(exc_info.value)
    assert received == ["frame-1", "frame-2"]
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert "LLM stream timeout" in message
    assert "stage=idle_chunk" in message
    assert "chunk_count=2" in message
    assert "idle_elapsed=" in message
    assert "total_elapsed=" in message
    assert ", elapsed=" not in message
    idle_match = re.search(r"idle_elapsed=([0-9.]+)s", message)
    total_match = re.search(r"total_elapsed=([0-9.]+)s", message)
    assert idle_match is not None
    assert total_match is not None
    assert float(total_match.group(1)) >= float(idle_match.group(1))
    assert "model=mock-model" in message
