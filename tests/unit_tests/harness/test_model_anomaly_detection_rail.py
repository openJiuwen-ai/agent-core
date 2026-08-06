# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    ModelClientConfig,
    ModelRequestConfig,
    UserMessage,
)
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.rails.model_anomaly_detection_rail import ModelAnomalyDetectionRail


_DEFAULT_ABC_REPEAT_COUNT = 54


def _make_ctx(agent=None):
    if agent is None:
        agent = MagicMock()
    return AgentCallbackContext(agent=agent, extra={})


def _make_agent() -> ReActAgent:
    return ReActAgent(card=AgentCard(description="anomaly detection rail test")).configure(
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
        self.last_messages = None

    async def invoke(self, **kwargs):
        raise NotImplementedError

    async def stream(self, **kwargs):
        self.call_count += 1
        self.last_messages = kwargs.get("messages")
        if self.mode in {"loop", "loop_exhausted"} and (self.mode == "loop_exhausted" or self.call_count == 1):
            for _ in range(_DEFAULT_ABC_REPEAT_COUNT):
                yield AssistantMessageChunk(reasoning_content="abc")
            return
        if self.mode in {"timeout", "timeout_exhausted"} and (self.mode == "timeout_exhausted" or self.call_count == 1):
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg="LLM stream timeout: stream frame timeout: stage=idle_chunk",
            )
        if self.mode in {"empty_content", "empty_content_exhausted"} and (
            self.mode == "empty_content_exhausted" or self.call_count == 1
        ):
            # One non-repeating reasoning chunk with empty content: should hit
            # empty-content detection, not the repeated-suffix detector.
            # finish_reason is required so inspect_stream_chunk can raise inside
            # the model-call body (retry without changing @rail).
            yield AssistantMessageChunk(
                reasoning_content=(
                    "Unique planning notes that never form a short repeated suffix."
                )
            )
            yield AssistantMessageChunk(finish_reason="stop")
            return
        yield AssistantMessageChunk(content="recovered")


class _FakeContext:
    def __init__(self, messages=None):
        self._messages = list(messages or [])

    def get_messages(self):
        return list(self._messages)

    def set_messages(self, messages, with_history: bool = True):
        self._messages = list(messages)


@pytest.mark.asyncio
async def test_short_repeated_stream_output_below_total_threshold_is_ignored():
    rail = ModelAnomalyDetectionRail()
    ctx = _make_ctx()

    await rail.before_model_call(ctx)

    for _ in range(6):
        await rail.inspect_stream_chunk(ctx, AssistantMessageChunk(reasoning_content="abc"))


@pytest.mark.asyncio
async def test_repeated_stream_output_raises_model_error():
    rail = ModelAnomalyDetectionRail()
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
    rail = ModelAnomalyDetectionRail()
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
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.5, 1.0, 2.0])
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
    # Backoff applied before each retry (exact schedule).
    assert [call.kwargs["delay_seconds"] for call in ctx.request_retry.call_args_list] == [0.5, 1.0]
    assert rail.repeat_retry_count == 0


@pytest.mark.asyncio
async def test_backoff_delay_follows_schedule_and_clamps():
    rail = ModelAnomalyDetectionRail(backoff_seconds=[0.5, 1.0, 2.0])
    assert rail.backoff_delay(0) == 0.5
    assert rail.backoff_delay(1) == 1.0
    assert rail.backoff_delay(2) == 2.0
    # Indices beyond the schedule clamp to the last entry.
    assert rail.backoff_delay(5) == 2.0

    # Default schedule matches the documented (0.5, 1.0, 2.0).
    default_rail = ModelAnomalyDetectionRail()
    assert [default_rail.backoff_delay(i) for i in range(3)] == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_stream_timeout_exception_retries_twice_then_resets():
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.5, 1.0, 2.0])
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
    assert [call.kwargs["delay_seconds"] for call in ctx.request_retry.call_args_list] == [0.5, 1.0]
    assert rail.stream_timeout_retry_count == 0


@pytest.mark.asyncio
async def test_before_invoke_resets_retry_counters():
    rail = ModelAnomalyDetectionRail()
    rail.repeat_retry_count = 1
    rail.stream_timeout_retry_count = 1
    rail.empty_content_retry_count = 1

    await rail.before_invoke(_make_ctx())

    assert rail.repeat_retry_count == 0
    assert rail.stream_timeout_retry_count == 0
    assert rail.empty_content_retry_count == 0


@pytest.mark.asyncio
async def test_after_model_call_empty_content_appends_newline_and_raises():
    rail = ModelAnomalyDetectionRail()
    context = _FakeContext([UserMessage(content="please continue")])
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ModelCallInputs(
            response=AssistantMessage(content="", reasoning_content="long thinking"),
        ),
        context=context,
    )

    from openjiuwen.core.runner.callback.errors import AbortError

    with pytest.raises(AbortError) as exc_info:
        await rail.after_model_call(ctx)

    assert "LLM empty content detected" in str(exc_info.value.cause)
    assert context.get_messages()[-1].content == "please continue\n"


@pytest.mark.asyncio
async def test_after_model_call_removes_trailing_empty_assistant_then_nudges_previous():
    rail = ModelAnomalyDetectionRail()
    context = _FakeContext(
        [
            UserMessage(content="你好\n"),
            AssistantMessage(content="", reasoning_content="x" * 100),
        ]
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ModelCallInputs(
            response=AssistantMessage(content="", reasoning_content="still empty"),
        ),
        context=context,
    )

    from openjiuwen.core.runner.callback.errors import AbortError

    with pytest.raises(AbortError):
        await rail.after_model_call(ctx)

    messages = context.get_messages()
    assert len(messages) == 1
    # Even if previous already ended with \\n, append another one.
    assert messages[0].content == "你好\n\n"


@pytest.mark.asyncio
async def test_after_model_call_ignores_empty_content_when_tool_calls_present():
    rail = ModelAnomalyDetectionRail()
    context = _FakeContext([UserMessage(content="please continue")])
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ModelCallInputs(
            response=AssistantMessage(
                content="",
                tool_calls=[{"id": "1", "type": "function", "name": "todo_list", "arguments": "{}"}],
            ),
        ),
        context=context,
    )

    await rail.after_model_call(ctx)
    assert context.get_messages()[-1].content == "please continue"


@pytest.mark.asyncio
async def test_empty_content_exception_retries_twice_then_resets():
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.5, 1.0, 2.0])
    ctx = _make_ctx()
    ctx.request_retry = MagicMock()
    ctx.exception = build_error(
        StatusCode.MODEL_CALL_FAILED,
        error_msg="LLM empty content detected: content is empty without tool_calls",
    )

    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)
    await rail.on_model_exception(ctx)

    assert ctx.request_retry.call_count == 2
    assert [call.kwargs["delay_seconds"] for call in ctx.request_retry.call_args_list] == [0.5, 1.0]
    assert rail.empty_content_retry_count == 0


@pytest.mark.asyncio
async def test_rail_retries_repeated_stream_output_in_agent_streaming_path():
    agent = _make_agent()
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
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
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
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
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
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
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
    await agent.register_rail(rail)
    model = _RetryStreamModel("timeout_exhausted")
    agent.set_llm(model)

    with pytest.raises(BaseError) as exc_info:
        await agent.invoke({"query": "timeout always"}, _streaming=True)

    assert "LLM stream timeout" in str(exc_info.value)
    assert model.call_count == 3


@pytest.mark.asyncio
async def test_rail_retries_empty_content_in_agent_streaming_path():
    agent = _make_agent()
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
    await agent.register_rail(rail)
    model = _RetryStreamModel("empty_content")
    agent.set_llm(model)

    result = await agent.invoke({"query": "empty once"}, _streaming=True)

    assert result["result_type"] == "answer"
    assert result["output"] == "recovered"
    assert model.call_count == 2
    # Retry should see the nudged last user message (context mutation took effect).
    assert model.last_messages is not None
    assert any(
        getattr(msg, "role", None) == "user"
        and isinstance(getattr(msg, "content", None), str)
        and str(msg.content).endswith("\n")
        for msg in model.last_messages
    )


@pytest.mark.asyncio
async def test_rail_propagates_empty_content_after_retry_exhaustion():
    agent = _make_agent()
    rail = ModelAnomalyDetectionRail(max_retries=2, backoff_seconds=[0.0])
    await agent.register_rail(rail)
    model = _RetryStreamModel("empty_content_exhausted")
    agent.set_llm(model)

    with pytest.raises(BaseError) as exc_info:
        await agent.invoke({"query": "empty always"}, _streaming=True)

    assert "LLM empty content detected" in str(exc_info.value)
    assert model.call_count == 3
