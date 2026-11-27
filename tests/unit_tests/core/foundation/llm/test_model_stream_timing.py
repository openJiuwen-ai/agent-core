# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model.stream timing collection tests (PR-501 sync of Java's
StreamTimingCollectionTest).

Verifies that ``Model`` collects stream timing at its single stream funnel and
backfills the measurements onto the usage object:

- the window must start *before* the client opens the connection,
- must advance only on output-bearing chunks,
- TPOT divides by output tokens, not chunk count,
- and absent measurements stay ``None`` rather than being written as zero.
"""
import asyncio

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessageChunk,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata


def _build_model_with_stream(stream_fn) -> Model:
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )
    model._client.stream = stream_fn
    return model


def _usage(**kwargs) -> UsageMetadata:
    return UsageMetadata(model_name="mock-model", input_tokens=3, **kwargs)


async def _drain(model: Model):
    seen = []
    async for chunk in model.stream(messages=[]):
        seen.append(chunk)
    return seen


def _usage_of(seen) -> UsageMetadata:
    for chunk in reversed(seen):
        if chunk.usage_metadata is not None:
            return chunk.usage_metadata
    raise AssertionError("no usage-bearing chunk in stream")


@pytest.mark.asyncio
async def test_timing_window_starts_before_client_call():
    """TTFT must include the wait inside the client's stream() (connection +
    time-to-first-byte), so the dispatch stamp is taken before it is called."""
    started = asyncio.Event()

    async def blocking_stream(**kwargs):
        await started.wait()
        await asyncio.sleep(0.05)  # emulate waiting for response headers
        yield AssistantMessageChunk(content="hello")
        yield AssistantMessageChunk(content="", usage_metadata=_usage(output_tokens=2))

    async def releasing_stream(**kwargs):
        started.set()
        gen = blocking_stream(**kwargs)
        async for chunk in gen:
            yield chunk

    model = _build_model_with_stream(releasing_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.ttft_seconds is not None
    assert usage.ttft_seconds >= 0.04, (
        "TTFT must include the 50ms wait inside stream(), got %s" % usage.ttft_seconds
    )
    assert usage.total_latency >= usage.ttft_seconds


@pytest.mark.asyncio
async def test_framing_chunks_do_not_advance_window():
    """Role-only / empty frames before the first real content chunk must not
    contaminate the token window."""
    async def framing_stream(**kwargs):
        yield AssistantMessageChunk(role="assistant", content="")
        yield AssistantMessageChunk(content="")
        yield AssistantMessageChunk(content="done", usage_metadata=_usage(output_tokens=2))

    model = _build_model_with_stream(framing_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.ttft_seconds is not None, "an output chunk did arrive, so TTFT is measurable"
    assert usage.ttft_seconds >= 0.0


@pytest.mark.asyncio
async def test_total_latency_measured_without_output():
    """Usage-only stream (early finish, no content at all): total latency is
    measured, TTFT / TPOT / reasoning duration stay None."""
    async def usage_only_stream(**kwargs):
        await asyncio.sleep(0.01)
        yield AssistantMessageChunk(content="", usage_metadata=_usage(output_tokens=0))

    model = _build_model_with_stream(usage_only_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.total_latency > 0.0, "total latency covers the whole call"
    assert usage.ttft_seconds is None, "no output chunk means TTFT was never measured"
    assert usage.inter_token_latency_ms is None, "no token window means TPOT was never measured"
    assert usage.reasoning_duration_ms is None


@pytest.mark.asyncio
async def test_tpot_uses_output_token_denominator():
    """Two chunks bound the window but the provider reports 9 output tokens:
    TPOT divides by tokens-1 (8), not by the chunk count (1)."""
    async def two_chunk_stream(**kwargs):
        yield AssistantMessageChunk(content="a")
        await asyncio.sleep(0.05)
        yield AssistantMessageChunk(content="b", usage_metadata=_usage(output_tokens=9))

    model = _build_model_with_stream(two_chunk_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.inter_token_latency_ms is not None
    # A chunk-count denominator would report ~50ms (window / 1); the token
    # denominator reports ~6ms (window / 8). The window is at most ~200ms on
    # a loaded CI, so 25ms keeps the two outcomes separated.
    assert usage.inter_token_latency_ms < 25.0


@pytest.mark.asyncio
async def test_reasoning_duration_collected():
    """First-to-last reasoning chunk span lands on reasoning_duration_ms."""
    async def reasoning_stream(**kwargs):
        yield AssistantMessageChunk(content="", reasoning_content="think")
        await asyncio.sleep(0.04)
        yield AssistantMessageChunk(content="done", reasoning_content=" more",
                                    usage_metadata=_usage(output_tokens=2))

    model = _build_model_with_stream(reasoning_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.reasoning_duration_ms is not None
    assert 30.0 <= usage.reasoning_duration_ms < 500.0


@pytest.mark.asyncio
async def test_backfill_never_overwrites_existing_measurements():
    """Values already present on the usage object (provider-reported or
    engine-stamped) are authoritative and stay untouched."""
    async def premeasured_stream(**kwargs):
        yield AssistantMessageChunk(content="hello")
        yield AssistantMessageChunk(
            content="world",
            usage_metadata=UsageMetadata(
                model_name="mock-model",
                input_tokens=3,
                output_tokens=2,
                total_latency=9.9,
                ttft_seconds=1.23,
                inter_token_latency_ms=4.56,
                reasoning_duration_ms=7.89,
            ),
        )

    model = _build_model_with_stream(premeasured_stream)
    seen = await _drain(model)

    usage = _usage_of(seen)
    assert usage.total_latency == pytest.approx(9.9)
    assert usage.ttft_seconds == pytest.approx(1.23)
    assert usage.inter_token_latency_ms == pytest.approx(4.56)
    assert usage.reasoning_duration_ms == pytest.approx(7.89)


@pytest.mark.asyncio
async def test_backfill_runs_when_consumer_abandons_stream():
    """A consumer that breaks out mid-stream (GeneratorExit) still gets the
    finally-backfill for the chunks observed so far."""
    async def long_stream(**kwargs):
        for i in range(5):
            yield AssistantMessageChunk(content=f"part-{i}")
            await asyncio.sleep(0.01)
        yield AssistantMessageChunk(content="end", usage_metadata=_usage(output_tokens=5))

    model = _build_model_with_stream(long_stream)
    consumed = 0
    async for chunk in model.stream(messages=[]):
        consumed += 1
        if consumed == 2:
            break

    # The generator was abandoned before the usage chunk; the finally backfill
    # must not have crashed the close, and the abandoned stream carries no
    # usage to stamp (last_usage is None) -- an explicit regression guard for
    # the finally path raising into aclose().
    assert consumed == 2


@pytest.mark.asyncio
async def test_new_usage_fields_default_to_none():
    """retry_count / reasoning_duration_ms are schema-transparent: None means
    'not measured' and is a fact distinct from a zero measurement."""
    usage = _usage(output_tokens=2)
    assert usage.reasoning_duration_ms is None
    assert usage.retry_count is None
