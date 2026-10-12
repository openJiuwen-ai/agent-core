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
    assert "stage=first_token" in message
    assert "chunk_count=0" in message
    assert "first_token_elapsed=" in message
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
    assert "stage=idle_token" in message
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


@pytest.mark.asyncio
async def test_model_stream_timeout_carries_error_message_into_callback():
    """The LLM_CALL_ERROR event carries the built detail as error_message.

    The raised asyncio.TimeoutError renders as an empty string, so the span
    would otherwise record a blank cause even though the stage/timeout/chunk
    diagnostics were already assembled here.
    """
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.callback.events import LLMCallEvents

    captured: list[dict] = []

    async def capture_error(**kwargs):
        captured.append(kwargs)

    Runner.callback_framework.on(LLMCallEvents.LLM_CALL_ERROR)(capture_error)

    async def stalled_stream(**kwargs):
        await asyncio.sleep(1.0)
        yield AssistantMessageChunk(content="never")

    model = _build_model_with_stream(
        stalled_stream,
        first_timeout=0.01,
        idle_timeout=0.01,
    )

    with pytest.raises(BaseError):
        async for _ in model.stream(messages=[]):
            pass

    assert captured, "LLM_CALL_ERROR must be triggered on stream timeout"
    event_kwargs = captured[-1]
    assert isinstance(event_kwargs.get("error"), asyncio.TimeoutError)
    error_message = event_kwargs.get("error_message")
    assert isinstance(error_message, str)
    assert "LLM stream timeout" in error_message
    assert "stage=first_token" in error_message
    assert "chunk_count=0" in error_message
    assert "model=mock-model" in error_message


@pytest.mark.asyncio
async def test_model_stream_role_only_delta_does_not_satisfy_first_token_timeout():
    async def role_delta_then_stall_stream(**kwargs):
        yield AssistantMessageChunk(content="")
        await asyncio.sleep(0.2)
        yield AssistantMessageChunk(content="late-token")

    model = _build_model_with_stream(
        role_delta_then_stall_stream,
        first_timeout=0.05,
        idle_timeout=1.0,
    )

    with pytest.raises(BaseError) as exc_info:
        async for _ in model.stream(messages=[]):
            pass

    message = str(exc_info.value)
    assert "stage=first_token" in message
    assert "chunk_count=1" in message
    assert "token_chunk_count=0" in message


@pytest.mark.asyncio
async def test_model_stream_heartbeat_deltas_do_not_reset_idle_timeout():
    async def heartbeat_stream(**kwargs):
        yield AssistantMessageChunk(content="token")
        for _ in range(10):
            await asyncio.sleep(0.03)
            yield AssistantMessageChunk(content="")
        yield AssistantMessageChunk(content="never")

    model = _build_model_with_stream(
        heartbeat_stream,
        first_timeout=1.0,
        idle_timeout=0.1,
    )
    received = []

    with pytest.raises(BaseError) as exc_info:
        async for chunk in model.stream(messages=[]):
            received.append(chunk.content)

    message = str(exc_info.value)
    assert "never" not in received
    assert "stage=idle_token" in message
    assert "token_chunk_count=1" in message


@pytest.mark.asyncio
async def test_model_stream_token_chunks_reset_idle_budget():
    async def steady_stream(**kwargs):
        for index in range(5):
            await asyncio.sleep(0.03)
            yield AssistantMessageChunk(reasoning_content=f"r{index}")

    model = _build_model_with_stream(
        steady_stream,
        first_timeout=1.0,
        idle_timeout=0.1,
    )

    received = [chunk.reasoning_content async for chunk in model.stream(messages=[])]

    assert received == ["r0", "r1", "r2", "r3", "r4"]


@pytest.mark.asyncio
async def test_model_stream_consumer_time_does_not_count_against_idle_budget():
    async def two_token_stream(**kwargs):
        yield AssistantMessageChunk(content="a")
        yield AssistantMessageChunk(content="b")

    model = _build_model_with_stream(
        two_token_stream,
        first_timeout=1.0,
        idle_timeout=0.05,
    )
    received = []

    async for chunk in model.stream(messages=[]):
        received.append(chunk.content)
        await asyncio.sleep(0.1)

    assert received == ["a", "b"]


# ---------------------------------------------------------------------------
# Anthropic tool-argument streaming
# ---------------------------------------------------------------------------

# Each individual gap below stays far under the idle budget, but their sum
# exceeds it.  Only a chunk that resets ``waited_since_progress`` survives that.
_ANTHROPIC_CLIENT_CONFIG = None


def _make_anthropic_client():
    from openjiuwen.core.foundation.llm import ModelClientConfig
    from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
        AnthropicModelClient,
    )
    from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig

    return AnthropicModelClient(
        ModelRequestConfig(model_name="claude-opus-4"),
        ModelClientConfig(
            client_provider="Anthropic",
            api_key="mock-key",
            api_base="https://api.anthropic.com",
            verify_ssl=False,
        ),
    )


def _tool_arg_events(partials):
    """Build the Anthropic event sequence for one streamed tool call."""
    from unittest.mock import MagicMock

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "t1"
    tool_block.name = "lookup"
    tool_block.input = {}

    start = MagicMock(type="content_block_start", index=0)
    start.content_block = tool_block

    deltas = []
    for partial in partials:
        event = MagicMock(type="content_block_delta", index=0)
        event.delta = MagicMock(type="input_json_delta", partial_json=partial)
        deltas.append(event)

    stop = MagicMock(type="content_block_stop", index=0)
    return [start, *deltas, stop]


@pytest.mark.asyncio
async def test_model_stream_anthropic_tool_args_do_not_trip_idle_timeout():
    """A long tool-argument stream must not be cut off by ``stream_idle_timeout``.

    ``input_json_delta`` is the only event that advances Anthropic tool
    arguments.  While it was swallowed the stream produced nothing for the whole
    argument phase and the idle budget ran out.  Emitting a metadata-only chunk
    does *not* help either: ``carries_output_token()`` ignores metadata, so such a
    chunk stays a framing frame (see
    ``test_model_stream_heartbeat_deltas_do_not_reset_idle_timeout``) and the
    budget keeps draining.  The delta has to carry a real tool call fragment.

    Every gap here is individually small; only their cumulative sum exceeds the
    idle budget, so this fails if any delta fails to reset the budget.
    """
    partials = ['{"loc', 'ation": ', '"New', ' York', '"}']

    async def anthropic_tool_arg_stream(**kwargs):
        client = _make_anthropic_client()
        state = {}
        # A leading text chunk satisfies the first-token budget, mirroring the
        # real "call a tool" flow where the model narrates before the arguments.
        yield AssistantMessageChunk(content="looking it up")
        for event in _tool_arg_events(partials):
            await asyncio.sleep(0.04)
            chunk = client._event_to_chunk(event, state)
            if chunk is not None:
                yield chunk

    model = _build_model_with_stream(
        anthropic_tool_arg_stream,
        first_timeout=1.0,
        idle_timeout=0.15,
    )

    received = [chunk async for chunk in model.stream(messages=[])]

    fragments = [
        call.arguments
        for chunk in received
        for call in (chunk.tool_calls or [])
        if call.arguments
    ]
    assert fragments == partials, "上游必须收到逐片递进的工具参数"
    assert "".join(fragments) == '{"location": "New York"}'


@pytest.mark.asyncio
async def test_model_stream_anthropic_tool_args_merge_into_one_complete_call():
    """The accumulated message must expose exactly one tool call with full args."""
    partials = ['{"loc', 'ation": ', '"New', ' York', '"}']

    async def anthropic_tool_arg_stream(**kwargs):
        client = _make_anthropic_client()
        state = {}
        for event in _tool_arg_events(partials):
            chunk = client._event_to_chunk(event, state)
            if chunk is not None:
                yield chunk

    model = _build_model_with_stream(
        anthropic_tool_arg_stream,
        first_timeout=1.0,
        idle_timeout=1.0,
    )

    accumulated = None
    async for chunk in model.stream(messages=[]):
        accumulated = accumulated + chunk if accumulated is not None else chunk

    assert len(accumulated.tool_calls) == 1
    call = accumulated.tool_calls[0]
    assert call.id == "t1"
    assert call.name == "lookup"
    assert call.arguments == '{"location": "New York"}', "参数不得重复拼接"
