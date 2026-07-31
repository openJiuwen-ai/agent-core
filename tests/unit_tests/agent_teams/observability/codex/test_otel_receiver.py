# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the Codex package's filtered native OTLP trace receiver."""

from __future__ import annotations

import asyncio
import gzip
from urllib.parse import urlsplit

import pytest


def _trace_payload() -> bytes:
    pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    resource_attribute = resource_spans.resource.attributes.add()
    resource_attribute.key = "service.name"
    resource_attribute.value.string_value = "codex-app-server"
    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = "codex-core"

    noise = scope_spans.spans.add()
    noise.name = "auth"
    noise.start_time_unix_nano = 10
    noise.end_time_unix_nano = 20

    model = scope_spans.spans.add()
    model.name = "run_sampling_request"
    model.trace_id = bytes.fromhex("11" * 16)
    model.span_id = bytes.fromhex("22" * 8)
    model.parent_span_id = bytes.fromhex("33" * 8)
    model.start_time_unix_nano = 1_700_000_000_000_000_000
    model.end_time_unix_nano = 1_700_000_000_250_000_000
    for key, value in (
        ("turn_id", "turn-1"),
        ("model", "gpt-test"),
    ):
        attribute = model.attributes.add()
        attribute.key = key
        attribute.value.string_value = value

    return request.SerializeToString()


@pytest.mark.level0
def test_decode_only_native_logical_model_span():
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex.otel_receiver import (
        _decode_model_spans,
    )

    events = _decode_model_spans(_trace_payload())

    assert len(events) == 1
    event = events[0]
    assert event["name"] == "run_sampling_request"
    assert event["start_time_ns"] == 1_700_000_000_000_000_000
    assert event["end_time_ns"] == 1_700_000_000_250_000_000
    assert event["attributes"]["turn_id"] == "turn-1"
    assert event["attributes"]["model"] == "gpt-test"
    assert event["trace_id"] == "11" * 16
    assert event["span_id"] == "22" * 8
    assert event["parent_span_id"] == "33" * 8
    assert event["resource_attributes"]["service.name"] == "codex-app-server"
    assert event["scope_name"] == "codex-core"


@pytest.mark.level0
def test_gzip_decompression_has_expansion_limit(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex import otel_receiver

    monkeypatch.setattr(otel_receiver, "_MAX_DECOMPRESSED_BYTES", 64)

    with pytest.raises(ValueError, match="decompressed OTLP request is too large"):
        otel_receiver._decompress_gzip_limited(gzip.compress(b"x" * 65))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_loopback_receiver_accepts_binary_otlp_traces():
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex.otel_receiver import (
        CodexOtelTraceReceiver,
    )

    received = []
    receiver = await CodexOtelTraceReceiver.start(received.append)
    if receiver is None:
        pytest.skip("loopback sockets are unavailable in this execution sandbox")
    assert receiver.endpoint is not None
    endpoint = urlsplit(receiver.endpoint)
    payload = _trace_payload()
    reader, writer = await asyncio.open_connection(
        endpoint.hostname,
        endpoint.port,
    )
    writer.write(
        (
            f"POST {endpoint.path} HTTP/1.1\r\n"
            f"Host: {endpoint.hostname}\r\n"
            "Content-Type: application/x-protobuf\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        + payload,
    )
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    await receiver.aclose()

    assert response.startswith(b"HTTP/1.1 200 OK")
    assert len(received) == 1
    assert received[0]["attributes"]["turn_id"] == "turn-1"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_loopback_receiver_times_out_incomplete_request(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex import otel_receiver

    monkeypatch.setattr(otel_receiver, "_REQUEST_READ_TIMEOUT_S", 0.01)
    receiver = await otel_receiver.CodexOtelTraceReceiver.start(lambda _: None)
    if receiver is None:
        pytest.skip("loopback sockets are unavailable in this execution sandbox")
    assert receiver.endpoint is not None
    endpoint = urlsplit(receiver.endpoint)
    reader, writer = await asyncio.open_connection(
        endpoint.hostname,
        endpoint.port,
    )

    response = await asyncio.wait_for(reader.read(), timeout=1.0)
    writer.close()
    await writer.wait_closed()
    await receiver.aclose()

    assert response.startswith(b"HTTP/1.1 408 Request Timeout")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_receiver_close_cancels_stalled_handlers(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex import otel_receiver

    monkeypatch.setattr(otel_receiver, "_CLOSE_TIMEOUT_S", 0.01)
    receiver = otel_receiver.CodexOtelTraceReceiver(lambda _: None)
    stalled = asyncio.create_task(asyncio.sleep(60))
    receiver._tasks.add(stalled)

    await receiver.aclose()

    assert stalled.cancelled()
