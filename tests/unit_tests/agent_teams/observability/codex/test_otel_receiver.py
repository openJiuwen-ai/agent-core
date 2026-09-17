# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the shared loopback OTLP receiver and Codex's filtered handle."""

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
def test_decode_spans_keeps_every_native_span():
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.shared_otlp import _decode_spans

    events = _decode_spans(_trace_payload())

    # The shared receiver decodes all spans; per-member filtering happens in
    # each subscriber (e.g. CodexOtelTraceReceiver keeps run_sampling_request).
    assert len(events) == 2
    model = next(event for event in events if event["name"] == "run_sampling_request")
    assert model["start_time_ns"] == 1_700_000_000_000_000_000
    assert model["end_time_ns"] == 1_700_000_000_250_000_000
    assert model["attributes"]["turn_id"] == "turn-1"
    assert model["attributes"]["model"] == "gpt-test"
    assert model["trace_id"] == "11" * 16
    assert model["span_id"] == "22" * 8
    assert model["parent_span_id"] == "33" * 8
    assert model["resource_attributes"]["service.name"] == "codex-app-server"
    assert model["scope_name"] == "codex-core"


@pytest.mark.level0
def test_gzip_decompression_has_expansion_limit(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability import shared_otlp

    monkeypatch.setattr(shared_otlp, "_MAX_DECOMPRESSED_BYTES", 64)

    with pytest.raises(ValueError, match="decompressed OTLP request is too large"):
        shared_otlp._decompress_gzip_limited(gzip.compress(b"x" * 65))


async def _post(endpoint_url: str, payload: bytes, *, timeout_s: float | None = None) -> bytes:
    endpoint = urlsplit(endpoint_url)
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
    response = await asyncio.wait_for(reader.read(), timeout=timeout_s)
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_handle_filters_logical_model_spans(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.codex.otel_receiver import (
        CodexOtelTraceReceiver,
    )
    from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

    # Private shared-receiver instance so closing it cannot disable the
    # process-wide singleton for other tests.
    shared = get_shared_otlp_receiver().__class__()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.shared_otlp._receiver",
        shared,
    )
    received = []
    receiver = await CodexOtelTraceReceiver.start(received.append)
    if receiver is None:
        monkeypatch.undo()
        pytest.skip("loopback sockets are unavailable in this execution sandbox")
    try:
        assert receiver.endpoint == shared.endpoint
        response = await _post(receiver.endpoint, _trace_payload(), timeout_s=2.0)
        assert response.startswith(b"HTTP/1.1 200 OK")
        # Only the logical model span reaches the Codex callback; the noise
        # span ("auth") is filtered by the per-member handle.
        assert len(received) == 1
        assert received[0]["attributes"]["turn_id"] == "turn-1"
    finally:
        await receiver.aclose()
        await shared.aclose()
        monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_shared_receiver_fans_out_and_detaches(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

    # Use a private receiver instance: the process-wide singleton is shared
    # with other tests, and closing it here would disable it for them.
    shared = get_shared_otlp_receiver().__class__()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.shared_otlp._receiver",
        shared,
    )
    received_a: list[dict] = []
    received_b: list[dict] = []
    subscriber_a = await shared.subscribe(received_a.append)
    subscriber_b = await shared.subscribe(received_b.append)
    assert subscriber_a is not None and subscriber_b is not None
    try:
        response = await _post(shared.endpoint, _trace_payload(), timeout_s=2.0)
        assert response.startswith(b"HTTP/1.1 200 OK")
        # Both subscribers see both decoded spans (fan-out); filtering is each
        # subscriber's own concern.
        assert len(received_a) == 2
        assert len(received_b) == 2

        shared.unsubscribe(subscriber_a)
        received_a.clear()
        received_b.clear()
        response = await _post(shared.endpoint, _trace_payload(), timeout_s=2.0)
        assert response.startswith(b"HTTP/1.1 200 OK")
        assert received_a == []
        assert len(received_b) == 2
    finally:
        await shared.aclose()
        monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_loopback_receiver_times_out_incomplete_request(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability import shared_otlp
    from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

    monkeypatch.setattr(shared_otlp, "_REQUEST_READ_TIMEOUT_S", 0.01)
    shared = get_shared_otlp_receiver()
    receiver = shared_otlp.SharedOtlpReceiver()
    # Use a fresh instance: the singleton may already be bound with the
    # unpatched timeout from another test.
    monkeypatch.setattr(shared_otlp, "_receiver", None)
    monkeypatch.setattr(receiver, "_start_lock", asyncio.Lock())
    subscriber_id = await receiver.subscribe(lambda _: None)
    if subscriber_id is None:
        pytest.skip("loopback sockets are unavailable in this execution sandbox")
    try:
        endpoint = urlsplit(receiver.endpoint)
        reader, writer = await asyncio.open_connection(
            endpoint.hostname,
            endpoint.port,
        )
        # Send headers claiming a body but never send the body: the patched
        # read timeout must produce a 408.
        writer.write(
            (
                f"POST {endpoint.path} HTTP/1.1\r\n"
                f"Host: {endpoint.hostname}\r\n"
                "Content-Type: application/x-protobuf\r\n"
                "Content-Length: 10\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii"),
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=1.0)
        writer.close()
        await writer.wait_closed()
        assert response.startswith(b"HTTP/1.1 408 Request Timeout")
    finally:
        await receiver.aclose()
        monkeypatch.setattr(shared_otlp, "_receiver", shared)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_grpc_listener_serves_traces_and_logs(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    pytest.importorskip("grpc")
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
        ExportLogsServiceRequest,
    )
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import (
        LogsServiceStub,
    )
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
        TraceServiceStub,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

    from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver

    # Private instance so closing it cannot disable the process-wide singleton.
    shared = get_shared_otlp_receiver().__class__()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.shared_otlp._receiver",
        shared,
    )
    received: list[dict] = []
    subscriber_id = await shared.subscribe(received.append)
    if subscriber_id is None:
        monkeypatch.undo()
        pytest.skip("loopback sockets are unavailable in this execution sandbox")
    try:
        assert shared.grpc_endpoint is not None

        import grpc

        # Loopback-only channel: ambient proxy settings (http_proxy et al.)
        # must not hijack a 127.0.0.1 connection. grpc wants host:port — strip
        # the http:// scheme the OTLP endpoint carries.
        target = shared.grpc_endpoint.removeprefix("http://")
        channel = grpc.insecure_channel(
            target,
            options=(("grpc.enable_http_proxy", 0),),
        )
        trace_stub = TraceServiceStub(channel)
        logs_stub = LogsServiceStub(channel)

        trace_req = ExportTraceServiceRequest()
        resource_spans = trace_req.resource_spans.add()
        scope_spans = resource_spans.scope_spans.add()
        span = scope_spans.spans.add()
        span.name = "claude_code.llm_request"
        span.trace_id = bytes.fromhex("11" * 16)
        span.span_id = bytes.fromhex("22" * 8)
        span.start_time_unix_nano = 100
        span.end_time_unix_nano = 200
        span.attributes.add().CopyFrom(
            KeyValue(key="model", value=AnyValue(string_value="GLM-5.3")),
        )
        trace_stub.Export(trace_req)

        logs_req = ExportLogsServiceRequest()
        resource_logs = logs_req.resource_logs.add()
        scope_logs = resource_logs.scope_logs.add()
        record = scope_logs.log_records.add()
        record.body.string_value = "claude_code.api_response_body"
        record.trace_id = bytes.fromhex("11" * 16)
        record.attributes.add().CopyFrom(
            KeyValue(key="body", value=AnyValue(string_value='{"content": "hi"}')),
        )
        record.attributes.add().CopyFrom(
            KeyValue(key="request_id", value=AnyValue(string_value="req-1")),
        )
        logs_stub.Export(logs_req)
        channel.close()

        import time

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(received) < 2:
            await asyncio.sleep(0.05)

        signals = {event.get("signal") for event in received}
        assert signals == {"trace", "log"}
        trace_event = next(e for e in received if e.get("signal") == "trace")
        log_event = next(e for e in received if e.get("signal") == "log")
        assert trace_event["name"] == "claude_code.llm_request"
        assert trace_event["attributes"]["model"] == "GLM-5.3"
        assert log_event["name"] == "claude_code.api_response_body"
        assert log_event["attributes"]["request_id"] == "req-1"
    finally:
        await shared.aclose()
        monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_receiver_close_cancels_stalled_handlers(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from openjiuwen.agent_teams.observability.shared_otlp import SharedOtlpReceiver

    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.shared_otlp._receiver",
        None,
    )
    receiver = SharedOtlpReceiver()
    stalled = asyncio.create_task(asyncio.sleep(60))
    receiver._tasks.add(stalled)

    await receiver.aclose()

    assert stalled.cancelled()
