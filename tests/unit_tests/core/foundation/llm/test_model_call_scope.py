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

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id


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


@pytest.mark.asyncio
async def test_invoke_binds_a_call_id_and_restores_the_previous_one():
    """invoke runs inside a scope; the caller's context is left as it was."""
    seen: list[str] = []

    async def fake_invoke(**kwargs):
        seen.append(get_current_llm_call_id())
        return AssistantMessage(content="ok")

    model = _build_model()
    model._client.invoke = fake_invoke

    assert get_current_llm_call_id() == ""
    await model.invoke(messages=[])
    await model.invoke(messages=[])
    assert get_current_llm_call_id() == "", "the scope must not leak past the call"

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
            yield AssistantMessageChunk(content=content)
        # The client triggers LLM_OUTPUT after its last frame; that trigger
        # must still see the id, so record the frame raising StopAsyncIteration.
        seen.append(get_current_llm_call_id())

    model = _build_model()
    model._client.stream = fake_stream

    received = [chunk.content async for chunk in model.stream(messages=[])]

    assert received == ["a", "b", "c"]
    assert len(seen) == 4
    assert all(seen), "every stream frame must run under a call id"
    assert len(set(seen)) == 1, f"one stream must report one id, got {set(seen)}"
    assert get_current_llm_call_id() == "", "the scope must not leak past the stream"


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
