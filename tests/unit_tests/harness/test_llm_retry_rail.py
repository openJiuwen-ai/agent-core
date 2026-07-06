# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm import AssistantMessageChunk, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.llm_retry_rail import LLMRetryRail


_DEFAULT_ABC_REPEAT_COUNT = 54


def _make_ctx(agent=None):
    if agent is None:
        agent = MagicMock()
    return AgentCallbackContext(agent=agent, extra={})


def _make_agent() -> ReActAgent:
    return ReActAgent(card=AgentCard(description="retry rail test")).configure(
        ReActAgentConfig(
            model_config_obj=ModelRequestConfig(model="mock-model"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="sk-test",
                api_base="https://mock.local/v1",
                verify_ssl=False,
            ),
            prompt_template=[{"role": "system", "content": "You are a test assistant."}],
        )
    )


class _RetryStreamModel:
    def __init__(self, mode: str):
        self.mode = mode
        self.call_count = 0

    async def invoke(self, **kwargs):
        raise NotImplementedError

    async def stream(self, **kwargs):
        self.call_count += 1
        if self.mode in {"loop", "loop_exhausted"} and (self.mode == "loop_exhausted" or self.call_count == 1):
            for _ in range(_DEFAULT_ABC_REPEAT_COUNT):
                yield AssistantMessageChunk(reasoning_content="abc")
            return
        if self.mode in {"timeout", "timeout_exhausted"} and (self.mode == "timeout_exhausted" or self.call_count == 1):
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg="LLM stream timeout: stream frame timeout: stage=idle_chunk",
            )
        yield AssistantMessageChunk(content="recovered")


@pytest.mark.asyncio
async def test_short_repeated_stream_output_below_total_threshold_is_ignored():
    rail = LLMRetryRail()
    ctx = _make_ctx()

    await rail.before_model_call(ctx)

    for _ in range(6):
        await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(reasoning_content="abc"))


@pytest.mark.asyncio
async def test_repeated_stream_output_raises_model_error():
    rail = LLMRetryRail()
    ctx = _make_ctx()

    await rail.before_model_call(ctx)

    for _ in range(_DEFAULT_ABC_REPEAT_COUNT - 1):
        await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(reasoning_content="abc"))

    with pytest.raises(BaseError) as exc_info:
        await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(reasoning_content="abc"))

    message = str(exc_info.value)
    assert "LLM repeated stream output detected" in message
    assert "field=reasoning_content" in message
    assert f"repeat_count={_DEFAULT_ABC_REPEAT_COUNT}" in message


@pytest.mark.asyncio
async def test_single_char_repetition_raises_model_error():
    rail = LLMRetryRail()
    ctx = _make_ctx()

    await rail.before_model_call(ctx)
    await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(content="a" * 99))

    with pytest.raises(BaseError) as exc_info:
        await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(content="a"))

    message = str(exc_info.value)
    assert "LLM repeated stream output detected" in message
    assert "field=content" in message
    assert "repeat_count=100" in message


@pytest.mark.asyncio
async def test_repeat_exception_retries_twice_then_resets():
    rail = LLMRetryRail(max_retries=2)
    ctx = _make_ctx()
    ctx.request_retry = MagicMock()
    ctx.exception = build_error(
        StatusCode.MODEL_CALL_FAILED,
        error_msg="LLM repeated stream output detected: field=content",
    )

    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)

    assert ctx.request_retry.call_count == 2
    assert rail.repeat_retry_count == 0


@pytest.mark.asyncio
async def test_stream_timeout_exception_retries_twice_then_resets():
    rail = LLMRetryRail(max_retries=2)
    ctx = _make_ctx()
    ctx.request_retry = MagicMock()
    ctx.exception = build_error(
        StatusCode.MODEL_CALL_FAILED,
        error_msg="LLM stream timeout: stream frame timeout: stage=idle_chunk",
    )

    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)

    assert ctx.request_retry.call_count == 2
    assert rail.stream_timeout_retry_count == 0


@pytest.mark.asyncio
async def test_before_invoke_resets_retry_counters():
    rail = LLMRetryRail()
    rail.repeat_retry_count = 1
    rail.stream_timeout_retry_count = 1

    await rail.before_invoke(_make_ctx())

    assert rail.repeat_retry_count == 0
    assert rail.stream_timeout_retry_count == 0


@pytest.mark.asyncio
async def test_rail_retries_repeated_stream_output_in_agent_streaming_path():
    agent = _make_agent()
    rail = LLMRetryRail(max_retries=2)
    await agent.register_rail(rail)
    model = _RetryStreamModel("loop")
    agent.set_llm(model)

    result = await agent.invoke({"query": "loop once"}, _streaming=True)

    assert result["result_type"] == "answer"
    assert result["output"] == "recovered"
    assert model.call_count == 2


@pytest.mark.asyncio
async def test_rail_retries_stream_timeout_in_agent_streaming_path():
    agent = _make_agent()
    rail = LLMRetryRail(max_retries=2)
    await agent.register_rail(rail)
    model = _RetryStreamModel("timeout")
    agent.set_llm(model)

    result = await agent.invoke({"query": "timeout once"}, _streaming=True)

    assert result["result_type"] == "answer"
    assert result["output"] == "recovered"
    assert model.call_count == 2


@pytest.mark.asyncio
async def test_rail_propagates_repeated_stream_output_after_retry_exhaustion():
    agent = _make_agent()
    rail = LLMRetryRail(max_retries=2)
    await agent.register_rail(rail)
    model = _RetryStreamModel("loop_exhausted")
    agent.set_llm(model)

    with pytest.raises(BaseError) as exc_info:
        await agent.invoke({"query": "loop always"}, _streaming=True)

    assert "LLM repeated stream output detected" in str(exc_info.value)
    assert model.call_count == 3


@pytest.mark.asyncio
async def test_rail_propagates_stream_timeout_after_retry_exhaustion():
    agent = _make_agent()
    rail = LLMRetryRail(max_retries=2)
    await agent.register_rail(rail)
    model = _RetryStreamModel("timeout_exhausted")
    agent.set_llm(model)

    with pytest.raises(BaseError) as exc_info:
        await agent.invoke({"query": "timeout always"}, _streaming=True)

    assert "LLM stream timeout" in str(exc_info.value)
    assert model.call_count == 3
