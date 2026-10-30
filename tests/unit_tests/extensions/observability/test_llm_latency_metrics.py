# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_span_in_context

from openjiuwen.core.foundation.llm import AssistantMessageChunk, UsageMetadata
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.extensions.observability import callback_handler as callback_handler_module
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.gen_ai_semconv import GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK
from openjiuwen.extensions.observability.semconv import (
    OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_BYTE_MS,
    OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN_MS,
    OJ_GEN_AI_RESPONSE_TPOT_MS,
    OJ_REQUEST_RETRY_COUNT,
)
from tests.test_logger import logger

_MS = 1_000_000


class _FakeClock:
    """Deterministic stand-in for the ``time`` module used by the handler."""

    def __init__(self) -> None:
        self.now_ns = 1_000 * _MS

    def advance_ms(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * _MS

    def monotonic_ns(self) -> int:
        return self.now_ns

    def time_ns(self) -> int:
        return self.now_ns

    def monotonic(self) -> float:
        return self.now_ns / 1e9

    def time(self) -> float:
        return self.now_ns / 1e9


def _open_streaming_span(monkeypatch: pytest.MonkeyPatch, clock: _FakeClock) -> tuple[Any, Any, Any]:
    provider = TracerProvider()
    tracer = provider.get_tracer("latency-metrics-test")
    root = tracer.start_span("agent.root")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="latency-metrics-test"),
        tracer=tracer,
    )
    monkeypatch.setattr(callback_handler_module, "time", clock)
    monkeypatch.setattr(handler, "_get_parent_context_for_llm_tool", lambda: set_span_in_context(root))
    span = handler._open_llm_span({"messages": [], "model": "reasoning-model"}, is_streaming=True)
    assert span is not None
    monkeypatch.setattr(callback_handler_module, "get_current_llm_span", lambda: span)
    return handler, span, provider


@pytest.mark.asyncio
async def test_time_to_first_token_skips_role_only_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    handler, span, provider = _open_streaming_span(monkeypatch, clock)

    clock.advance_ms(280)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content=""))
    clock.advance_ms(3_720)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content="", reasoning_content="think"))
    clock.advance_ms(500)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content="answer"))

    attrs = span.attributes
    logger.info("latency attrs: %s", dict(attrs))
    assert attrs[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] == pytest.approx(0.28)
    assert attrs[OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN_MS] == pytest.approx(4_000.0)
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_time_to_first_token_counts_tool_call_name_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    handler, span, provider = _open_streaming_span(monkeypatch, clock)

    clock.advance_ms(100)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content=""))
    clock.advance_ms(900)
    await handler.on_llm_stream_output(
        result=AssistantMessageChunk(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="search", arguments="")],
        ),
    )

    assert span.attributes[OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN_MS] == pytest.approx(1_000.0)
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_tpot_measures_between_token_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    handler, span, provider = _open_streaming_span(monkeypatch, clock)

    clock.advance_ms(100)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content=""))
    clock.advance_ms(2_000)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content="a"))
    clock.advance_ms(1_000)
    await handler.on_llm_stream_output(result=AssistantMessageChunk(content="b"))
    clock.advance_ms(50)
    await handler.on_llm_stream_output(
        result=AssistantMessageChunk(
            content="",
            finish_reason="stop",
            usage_metadata=UsageMetadata(input_tokens=10, output_tokens=11, total_tokens=21),
        ),
    )

    # 10 inter-token gaps across the 1000 ms between the first and last token;
    # the 2 s wait behind the role-only chunk and the trailing usage frame
    # are both excluded.
    assert span.attributes[OJ_GEN_AI_RESPONSE_TPOT_MS] == pytest.approx(100.0)
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_response_started_records_ttfb_and_retry_count_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    handler, span, provider = _open_streaming_span(monkeypatch, clock)

    clock.advance_ms(150)
    await handler.on_llm_response_started(retry_count=2)
    clock.advance_ms(500)
    await handler.on_llm_response_started(retry_count=5)

    assert span.attributes[OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_BYTE_MS] == pytest.approx(150.0)
    assert span.attributes[OJ_REQUEST_RETRY_COUNT] == 2
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_response_started_without_retry_count_records_only_ttfb(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    handler, span, provider = _open_streaming_span(monkeypatch, clock)

    clock.advance_ms(40)
    await handler.on_llm_response_started(retry_count=None)

    assert span.attributes[OJ_GEN_AI_RESPONSE_TIME_TO_FIRST_BYTE_MS] == pytest.approx(40.0)
    assert OJ_REQUEST_RETRY_COUNT not in span.attributes
    span.end()
    provider.shutdown()


def test_sdk_retry_count_reads_stainless_header() -> None:
    request = httpx.Request("POST", "https://example.test/v1", headers={"x-stainless-retry-count": "3"})
    response = httpx.Response(200, request=request)

    assert BaseModelClient._sdk_retry_count(response) == 3


def test_sdk_retry_count_unknown_without_header_or_request() -> None:
    no_header = httpx.Response(200, request=httpx.Request("POST", "https://example.test/v1"))
    no_request = httpx.Response(200)

    assert BaseModelClient._sdk_retry_count(no_header) is None
    assert BaseModelClient._sdk_retry_count(no_request) is None
    assert BaseModelClient._sdk_retry_count(None) is None


def test_sdk_retry_count_ignores_malformed_header() -> None:
    request = httpx.Request("POST", "https://example.test/v1", headers={"x-stainless-retry-count": "n/a"})

    assert BaseModelClient._sdk_retry_count(httpx.Response(200, request=request)) is None

