# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the loopback receiver used by Codex native OTel logs."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest


def _api_request_payload() -> bytes:
    pytest.importorskip("opentelemetry.proto.collector.logs.v1.logs_service_pb2")
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
        ExportLogsServiceRequest,
    )

    request = ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_attribute = resource_logs.resource.attributes.add()
    resource_attribute.key = "service.name"
    resource_attribute.value.string_value = "codex-app-server"
    scope_logs = resource_logs.scope_logs.add()
    scope_logs.scope.name = "codex-app-server"
    record = scope_logs.log_records.add()
    record.time_unix_nano = 1_700_000_000_250_000_000
    for key, value in (
        ("event.name", "codex.api_request"),
        ("conversation.id", "thread-1"),
        ("model", "gpt-test"),
        ("duration_ms", 250),
        ("success", True),
    ):
        attribute = record.attributes.add()
        attribute.key = key
        if isinstance(value, bool):
            attribute.value.bool_value = value
        elif isinstance(value, int):
            attribute.value.int_value = value
        else:
            attribute.value.string_value = value

    return request.SerializeToString()


@pytest.mark.level0
def test_decode_codex_api_request_log():
    from openjiuwen.agent_teams.observability.codex_otel_receiver import (
        _decode_api_requests,
    )

    events = _decode_api_requests(_api_request_payload())

    assert len(events) == 1
    event = events[0]
    assert event["timestamp_ns"] == 1_700_000_000_250_000_000
    assert event["attributes"]["conversation.id"] == "thread-1"
    assert event["attributes"]["duration_ms"] == 250
    assert event["attributes"]["success"] is True
    assert event["resource_attributes"]["service.name"] == "codex-app-server"
    assert event["scope_name"] == "codex-app-server"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_loopback_receiver_accepts_binary_otlp_logs():
    from openjiuwen.agent_teams.observability.codex_otel_receiver import (
        CodexOtelLogReceiver,
    )

    received = []
    receiver = await CodexOtelLogReceiver.start(received.append)
    assert receiver is not None
    assert receiver.endpoint is not None
    endpoint = urlsplit(receiver.endpoint)
    payload = _api_request_payload()
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
    assert received[0]["attributes"]["conversation.id"] == "thread-1"
