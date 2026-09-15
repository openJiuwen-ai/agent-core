# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``Model`` binds a per-request call id that observers can correlate on.

The id is what tells one in-flight LLM request from another. These tests pin
the two properties observers depend on: it is stable for the whole request
(every stream frame reports the same one, even though each frame runs in its
own ``asyncio.wait_for`` task) and it is unique per request (two calls running
concurrently never report the same one).
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.call_scope import (
    LlmObservationSuppression,
    expects_unified_llm_completion,
    get_current_llm_call_id,
    is_llm_observation_suppressed,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents


def _build_model() -> Model:
    """Build a Model whose transport is replaced per test."""
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
            stream_first_chunk_timeout=5.0,
            stream_idle_timeout=5.0,
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )


def test_observation_suppression_is_nested_and_restored() -> None:
    assert not is_llm_observation_suppressed()
    with LlmObservationSuppression():
        assert is_llm_observation_suppressed()
        with LlmObservationSuppression():
            assert is_llm_observation_suppressed()
        assert is_llm_observation_suppressed()
    assert not is_llm_observation_suppressed()


@pytest.mark.asyncio
async def test_invoke_binds_a_call_id_and_restores_the_previous_one():
    """invoke runs inside a scope; the caller's context is left as it was."""
    seen: list[str] = []

    async def fake_invoke(**kwargs):
        seen.append(get_current_llm_call_id())
        assert expects_unified_llm_completion()
        return AssistantMessage(content="ok")

    model = _build_model()
    model._client.invoke = fake_invoke

    assert get_current_llm_call_id() == ""
    assert not expects_unified_llm_completion()
    await model.invoke(messages=[])
    await model.invoke(messages=[])
    assert get_current_llm_call_id() == "", "the scope must not leak past the call"
    assert not expects_unified_llm_completion(), "the lifecycle bit must not leak"

    assert len(seen) == 2
    assert all(seen), "every invoke must run under a call id"
    assert seen[0] != seen[1], "two calls must not share an id"


@pytest.mark.asyncio
async def test_stream_keeps_one_call_id_across_per_frame_task_hops():
    """Every frame of one stream reports the same id.

    ``Model.stream`` pulls each frame through ``asyncio.wait_for``, which runs
    it in its own task with a *copied* context. An id bound inside the stream
    would therefore be discarded between frames; this test is the guard that
    it is bound in the calling frame instead.
    """
    seen: list[str] = []

    async def fake_stream(**kwargs):
        for content in ("a", "b", "c"):
            seen.append(get_current_llm_call_id())
            assert expects_unified_llm_completion()
            yield AssistantMessageChunk(content=content)
        # The client triggers LLM_OUTPUT after its last frame; that trigger
        # must still see the id, so record the frame raising StopAsyncIteration.
        seen.append(get_current_llm_call_id())
        assert expects_unified_llm_completion()

    model = _build_model()
    model._client.stream = fake_stream

    received = [chunk.content async for chunk in model.stream(messages=[])]

    assert received == ["a", "b", "c"]
    assert len(seen) == 4
    assert all(seen), "every stream frame must run under a call id"
    assert len(set(seen)) == 1, f"one stream must report one id, got {set(seen)}"
    assert get_current_llm_call_id() == "", "the scope must not leak past the stream"
    assert not expects_unified_llm_completion(), "the lifecycle bit must not leak"


@pytest.mark.asyncio
async def test_concurrent_streams_get_distinct_call_ids():
    """Interleaved streams in separate tasks never observe each other's id."""
    started = asyncio.Event()
    seen: dict[str, list[str]] = {"first": [], "second": []}

    def _make_stream(label: str, gate: asyncio.Event | None):
        async def fake_stream(**kwargs):
            seen[label].append(get_current_llm_call_id())
            started.set()
            if gate is not None:
                await gate.wait()
            yield AssistantMessageChunk(content=label)
            seen[label].append(get_current_llm_call_id())

        return fake_stream

    release_first = asyncio.Event()

    first_model = _build_model()
    first_model._client.stream = _make_stream("first", release_first)
    second_model = _build_model()
    second_model._client.stream = _make_stream("second", None)

    async def _drain(model) -> None:
        async for _ in model.stream(messages=[]):
            pass

    first_task = asyncio.create_task(_drain(first_model))
    await started.wait()
    # The second stream runs start-to-finish while the first is parked mid-flight.
    await _drain(second_model)
    release_first.set()
    await first_task

    assert len(set(seen["first"])) == 1
    assert len(set(seen["second"])) == 1
    assert seen["first"][0] != seen["second"][0], "concurrent streams must not share an id"


@pytest.mark.asyncio
async def test_stream_completed_event_carries_the_fully_accumulated_message():
    completed: list[AssistantMessage] = []

    async def capture(*args, **kwargs):
        completed.append(kwargs["result"])

    async def fake_stream(**kwargs):
        yield AssistantMessageChunk(
            content="hel",
            reasoning_content="thi",
            response_id="resp-1",
        )
        yield AssistantMessageChunk(
            content="lo",
            reasoning_content="nk",
            finish_reason="stop",
            response_model="provider-model",
            completion_token_ids=[3, 4],
            provider_metadata={"status": "completed"},
        )

    framework = Runner.callback_framework
    framework.register_sync(
        LLMCallEvents.LLM_STREAM_COMPLETED,
        capture,
        namespace="test-model-stream-completed",
    )
    model = _build_model()
    model._client.stream = fake_stream
    try:
        received = [chunk.content async for chunk in model.stream(messages=[])]
    finally:
        framework.unregister_sync(LLMCallEvents.LLM_STREAM_COMPLETED, capture)

    assert received == ["hel", "lo"]
    assert len(completed) == 1
    message = completed[0]
    assert type(message) is AssistantMessage
    assert message.content == "hello"
    assert message.reasoning_content == "think"
    assert message.finish_reason == "stop"
    assert message.response_id == "resp-1"
    assert message.response_model == "provider-model"
    assert message.completion_token_ids == [3, 4]
    assert message.provider_metadata == {"status": "completed"}


@pytest.mark.parametrize(
    "parser_content",
    [False, 0, "", [], {}],
    ids=["false", "zero", "empty-string", "empty-list", "empty-dict"],
)
@pytest.mark.asyncio
async def test_stream_completed_event_preserves_falsy_parser_content(
    parser_content: object,
) -> None:
    completed: list[AssistantMessage] = []

    async def capture(*args: object, **kwargs: object) -> None:
        completed.append(kwargs["result"])

    async def fake_stream(**kwargs: object) -> AsyncIterator[AssistantMessageChunk]:
        yield AssistantMessageChunk(content="first")
        yield AssistantMessageChunk(content="second", parser_content=parser_content)

    framework = Runner.callback_framework
    framework.register_sync(
        LLMCallEvents.LLM_STREAM_COMPLETED,
        capture,
        namespace="test-model-stream-falsy-parser",
    )
    model = _build_model()
    model._client.stream = fake_stream
    try:
        received = [chunk.content async for chunk in model.stream(messages=[])]
    finally:
        framework.unregister_sync(LLMCallEvents.LLM_STREAM_COMPLETED, capture)

    assert received == ["first", "second"]
    assert len(completed) == 1
    assert type(completed[0].parser_content) is type(parser_content)
    assert completed[0].parser_content == parser_content
