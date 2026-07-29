# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receive Codex App Server OTLP logs without exporting its internal spans.

Codex exposes real model-transport timing through the ``codex.api_request``
structured log event.  The Python SDK notification stream does not carry that
event, so an SDK-backed member points Codex's OTLP/HTTP *log* exporter at this
small loopback receiver.  Only API-request events are forwarded to the
member's span bridge; all unrelated App Server logs are discarded.

The receiver is optional.  Importing this module does not require the
observability extra, and ``start`` returns ``None`` when the OTLP protobuf
package is unavailable.
"""

from __future__ import annotations

import asyncio
import gzip
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.logging import team_logger

_API_REQUEST_EVENT = "codex.api_request"
_MAX_REQUEST_BYTES = 16 * 1024 * 1024


def _any_value(value: Any) -> Any:
    """Convert one OTLP AnyValue protobuf into a plain Python value."""
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    if kind == "bytes_value":
        return bytes(value.bytes_value)
    if kind == "array_value":
        return [_any_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return {item.key: _any_value(item.value) for item in value.kvlist_value.values}
    return None


def _attributes(items: Any) -> dict[str, Any]:
    """Convert repeated OTLP KeyValue messages into a mapping."""
    return {item.key: _any_value(item.value) for item in items}


def _event_name(record: Any, attributes: dict[str, Any]) -> str:
    """Resolve an event name across old and new OTLP LogRecord encodings."""
    explicit = getattr(record, "event_name", "")
    if explicit:
        return str(explicit)
    for key in ("event.name", "event_name", "name"):
        value = attributes.get(key)
        if isinstance(value, str) and value:
            return value
    body = _any_value(record.body)
    return body if isinstance(body, str) else ""


def _decode_api_requests(payload: bytes) -> list[dict[str, Any]]:
    """Decode one binary OTLP ExportLogsServiceRequest."""
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
        ExportLogsServiceRequest,
    )

    request = ExportLogsServiceRequest()
    request.ParseFromString(payload)
    events: list[dict[str, Any]] = []
    for resource_logs in request.resource_logs:
        resource_attributes = _attributes(resource_logs.resource.attributes)
        for scope_logs in resource_logs.scope_logs:
            scope_name = str(scope_logs.scope.name or "")
            for record in scope_logs.log_records:
                attributes = _attributes(record.attributes)
                name = _event_name(record, attributes)
                if name != _API_REQUEST_EVENT:
                    continue
                events.append(
                    {
                        "name": name,
                        "timestamp_ns": int(record.time_unix_nano or record.observed_time_unix_nano or 0),
                        "observed_timestamp_ns": int(record.observed_time_unix_nano or 0),
                        "attributes": attributes,
                        "resource_attributes": resource_attributes,
                        "scope_name": scope_name,
                        "trace_id": bytes(record.trace_id).hex(),
                        "span_id": bytes(record.span_id).hex(),
                    }
                )
    return events


class CodexOtelLogReceiver:
    """Minimal loopback OTLP/HTTP receiver dedicated to one Codex member."""

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        self._callback = callback
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.endpoint: str | None = None

    @classmethod
    async def start(
        cls,
        callback: Callable[[dict[str, Any]], None],
    ) -> CodexOtelLogReceiver | None:
        """Start a receiver when OTLP protobuf support is installed."""
        try:
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
                ExportLogsServiceRequest as _ExportLogsServiceRequest,
            )
        except ImportError:
            return None

        del _ExportLogsServiceRequest
        receiver = cls(callback)
        try:
            receiver._server = await asyncio.start_server(
                receiver._accept,
                host="127.0.0.1",
                port=0,
            )
        except OSError as exc:
            team_logger.warning(
                "otel: Codex native API timing disabled because the loopback receiver could not start: {}",
                exc,
            )
            return None
        sockets = receiver._server.sockets or ()
        if not sockets:
            await receiver.aclose()
            return None
        port = int(sockets[0].getsockname()[1])
        receiver.endpoint = f"http://127.0.0.1:{port}/v1/logs"
        team_logger.info(
            "otel: Codex API-request receiver started endpoint={}",
            receiver.endpoint,
        )
        return receiver

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._handle_request(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 - telemetry must not affect Codex
            team_logger.warning("otel: Codex OTLP log receiver rejected a request: {}", exc)
            await self._respond(writer, status="400 Bad Request")
        finally:
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, RuntimeError):
                pass

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        raw_headers = await reader.readuntil(b"\r\n\r\n")
        if len(raw_headers) > 64 * 1024:
            raise ValueError("OTLP request headers are too large")
        lines = raw_headers.decode("latin-1").split("\r\n")
        request_line = lines[0].split()
        if len(request_line) < 2 or request_line[0] != "POST":
            await self._respond(writer, status="405 Method Not Allowed")
            return
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            headers[key.strip().lower()] = value.strip()
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("invalid OTLP Content-Length") from exc
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            raise ValueError("invalid OTLP request size")
        payload = await reader.readexactly(content_length)
        if headers.get("content-encoding", "").lower() == "gzip":
            payload = gzip.decompress(payload)
        events = _decode_api_requests(payload)
        if events:
            team_logger.info(
                "otel: Codex API-request receiver decoded {} model request(s)",
                len(events),
            )
        for event in events:
            try:
                self._callback(event)
            except Exception as exc:  # noqa: BLE001 - telemetry is best effort
                team_logger.warning(
                    "otel: Codex API request log callback failed: {}",
                    exc,
                )
        await self._respond(writer, status="200 OK")

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter,
        *,
        status: str,
    ) -> None:
        writer.write(
            (
                f"HTTP/1.1 {status}\r\n"
                "Content-Type: application/x-protobuf\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
        )
        await writer.drain()

    async def aclose(self) -> None:
        """Stop accepting logs and wait briefly for active requests."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = [task for task in self._tasks if task is not asyncio.current_task() and not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


__all__ = ["CodexOtelLogReceiver"]
