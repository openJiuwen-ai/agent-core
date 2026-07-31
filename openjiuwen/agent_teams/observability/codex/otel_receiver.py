# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receive Codex's native logical model-call spans over OTLP/HTTP.

The Codex App Server emits many internal spans.  Forwarding its trace exporter
directly to the application's OTLP backend makes transport details such as
``auth`` and ``send_data`` appear as top-level observations.  This loopback
receiver decodes the native trace stream and keeps only
``run_sampling_request``: Codex's own span for one logical sampling request.

The selected span already contains its real start/end timestamps.  Jiuwen does
not pair it with SDK response notifications or infer its boundary from tool
events.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import io
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.logging import team_logger

_LOGICAL_MODEL_SPAN = "run_sampling_request"
_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
_REQUEST_READ_TIMEOUT_S = 5.0
_RESPONSE_WRITE_TIMEOUT_S = 2.0
_CLOSE_TIMEOUT_S = 1.0


def _decompress_gzip_limited(payload: bytes) -> bytes:
    """Decompress one gzip body without allowing unbounded expansion."""
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
        decompressed = stream.read(_MAX_DECOMPRESSED_BYTES + 1)
    if len(decompressed) > _MAX_DECOMPRESSED_BYTES:
        raise ValueError("decompressed OTLP request is too large")
    return decompressed


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


def _is_logical_model_span(name: str) -> bool:
    """Match the native span while tolerating a module-qualified name."""
    normalized = name.replace("::", ".")
    return normalized == _LOGICAL_MODEL_SPAN or normalized.endswith(
        f".{_LOGICAL_MODEL_SPAN}",
    )


def _decode_model_spans(payload: bytes) -> list[dict[str, Any]]:
    """Decode selected spans from one binary OTLP ExportTraceServiceRequest."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    request.ParseFromString(payload)
    events: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        resource_attributes = _attributes(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            scope_name = str(scope_spans.scope.name or "")
            for span in scope_spans.spans:
                name = str(span.name or "")
                if not _is_logical_model_span(name):
                    continue
                events.append(
                    {
                        "name": name,
                        "start_time_ns": int(span.start_time_unix_nano or 0),
                        "end_time_ns": int(span.end_time_unix_nano or 0),
                        "attributes": _attributes(span.attributes),
                        "resource_attributes": resource_attributes,
                        "scope_name": scope_name,
                        "trace_id": bytes(span.trace_id).hex(),
                        "span_id": bytes(span.span_id).hex(),
                        "parent_span_id": bytes(span.parent_span_id).hex(),
                        "status_code": int(span.status.code),
                        "status_message": str(span.status.message or ""),
                    },
                )
    return events


class CodexOtelTraceReceiver:
    """Minimal loopback OTLP/HTTP trace receiver for one Codex member."""

    def __init__(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._callback = callback
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.endpoint: str | None = None

    @classmethod
    async def start(
        cls,
        callback: Callable[[dict[str, Any]], None],
    ) -> CodexOtelTraceReceiver | None:
        """Start a receiver when OTLP trace protobuf support is installed."""
        try:
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceRequest as _ExportTraceServiceRequest,
            )
        except ImportError:
            return None

        del _ExportTraceServiceRequest
        receiver = cls(callback)
        try:
            receiver._server = await asyncio.start_server(
                receiver._accept,
                host="127.0.0.1",
                port=0,
            )
        except OSError as exc:
            team_logger.warning(
                "otel: Codex native model spans disabled because the loopback receiver could not start: {}",
                exc,
            )
            return None
        sockets = receiver._server.sockets or ()
        if not sockets:
            await receiver.aclose()
            return None
        port = int(sockets[0].getsockname()[1])
        receiver.endpoint = f"http://127.0.0.1:{port}/v1/traces"
        team_logger.info(
            "otel: Codex native model-span receiver started endpoint={}",
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
        except TimeoutError:
            with contextlib.suppress(ConnectionError, TimeoutError):
                await self._respond(writer, status="408 Request Timeout")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 - telemetry must not affect Codex
            team_logger.warning("otel: Codex OTLP trace receiver rejected a request: {}", exc)
            with contextlib.suppress(ConnectionError, TimeoutError):
                await self._respond(writer, status="400 Bad Request")
        finally:
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(ConnectionError, RuntimeError, TimeoutError):
                await asyncio.wait_for(
                    writer.wait_closed(),
                    timeout=_RESPONSE_WRITE_TIMEOUT_S,
                )

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        raw_headers = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=_REQUEST_READ_TIMEOUT_S,
        )
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
        payload = await asyncio.wait_for(
            reader.readexactly(content_length),
            timeout=_REQUEST_READ_TIMEOUT_S,
        )
        if headers.get("content-encoding", "").lower() == "gzip":
            payload = _decompress_gzip_limited(payload)
        elif len(payload) > _MAX_DECOMPRESSED_BYTES:
            raise ValueError("OTLP request is too large")
        events = _decode_model_spans(payload)
        if events:
            team_logger.info(
                "otel: Codex native receiver decoded {} logical model span(s)",
                len(events),
            )
        for event in events:
            try:
                self._callback(event)
            except Exception as exc:  # noqa: BLE001 - telemetry is best effort
                team_logger.warning(
                    "otel: Codex native model-span callback failed: {}",
                    exc,
                )
        await self._respond(writer, status="200 OK")

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, *, status: str) -> None:
        writer.write(
            (
                f"HTTP/1.1 {status}\r\n"
                "Content-Type: application/x-protobuf\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii"),
        )
        await asyncio.wait_for(
            writer.drain(),
            timeout=_RESPONSE_WRITE_TIMEOUT_S,
        )

    async def aclose(self) -> None:
        """Stop accepting traces and wait for active requests."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = [task for task in self._tasks if task is not asyncio.current_task() and not task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_CLOSE_TIMEOUT_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()


__all__ = ["CodexOtelTraceReceiver"]
