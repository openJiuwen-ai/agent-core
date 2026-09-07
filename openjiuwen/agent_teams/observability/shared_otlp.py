# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-wide loopback OTLP/HTTP trace receiver shared by external CLI members.

Codex and Claude runtimes both ingest native OTel spans from their CLI
subprocesses over a loopback OTLP endpoint. Binding one receiver per member
scales the listening sockets (and ports) with team size; this module keeps a
single process-wide server and fans decoded span events out to subscriber
callbacks. Subscribers filter events by their own active turn/trace context,
so one socket serves every member.

The receiver is lazy: the first subscriber starts it, and it stays up for the
process lifetime. A bind failure disables native span ingestion process-wide
(observability is best-effort and must never block member startup).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import io
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.logging import team_logger

_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
_REQUEST_READ_TIMEOUT_S = 5.0
_RESPONSE_WRITE_TIMEOUT_S = 2.0
_CLOSE_TIMEOUT_S = 1.0
# Resource attribute injected into each external CLI process so subscribers
# can distinguish concurrent members that intentionally share one team trace.
OTEL_RESOURCE_SOURCE_ID = "openjiuwen.agent_teams.source.id"


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


def _decode_spans(payload: bytes) -> list[dict[str, Any]]:
    """Decode every span from one binary OTLP ExportTraceServiceRequest."""
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
                events.append(
                    {
                        "signal": "trace",
                        "name": str(span.name or ""),
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


def _decode_log_records(payload: bytes) -> list[dict[str, Any]]:
    """Decode every log record from one binary OTLP ExportLogsServiceRequest.

    Claude Code's raw API body events (``claude_code.api_request_body`` /
    ``claude_code.api_response_body``) arrive on the OTLP logs signal; each
    becomes one event dict shaped like a span event plus ``body`` text.
    """
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
            for log_record in scope_logs.log_records:
                events.append(
                    {
                        "signal": "log",
                        "name": str(log_record.body.string_value or ""),
                        "time_ns": int(log_record.time_unix_nano
                                       or log_record.observed_time_unix_nano or 0),
                        "attributes": _attributes(log_record.attributes),
                        "resource_attributes": resource_attributes,
                        "scope_name": scope_name,
                        "trace_id": bytes(log_record.trace_id).hex(),
                        "span_id": bytes(log_record.span_id).hex(),
                    },
                )
    return events


class SharedOtlpReceiver:
    """One loopback OTLP/HTTP server fanning decoded spans to subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._next_subscriber_id = 0
        self._server: asyncio.AbstractServer | None = None
        self._grpc_server: Any | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._start_lock = asyncio.Lock()
        self._starting = False
        self._disabled = False
        self.endpoint: str | None = None
        self.grpc_endpoint: str | None = None

    async def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> int | None:
        """Register a span-event callback and return its subscriber id.

        Starts the receiver on first use. Returns ``None`` when OTLP protobuf
        support is missing or the receiver cannot bind, so callers fall back to
        running without native spans. The returned id feeds ``unsubscribe``.
        """
        if self._disabled:
            return None
        async with self._start_lock:
            if self._disabled:
                return None
            if self._server is None and not self._starting:
                self._starting = True
                try:
                    await self._start()
                finally:
                    self._starting = False
        if self._server is None:
            return None
        self._next_subscriber_id += 1
        self._subscribers[self._next_subscriber_id] = callback
        return self._next_subscriber_id

    def unsubscribe(self, subscriber_id: int) -> None:
        """Drop one subscriber callback; the shared receiver keeps serving."""
        self._subscribers.pop(subscriber_id, None)

    @property
    def endpoint_url(self) -> str | None:
        """The OTLP/HTTP endpoint the CLI subprocesses should export to."""
        return self.endpoint

    def _fanout(self, events: list[dict[str, Any]]) -> None:
        """Broadcast decoded events to every subscriber callback."""
        for event in events:
            for callback in tuple(self._subscribers.values()):
                try:
                    callback(event)
                except Exception as exc:  # noqa: BLE001 - telemetry is best effort
                    team_logger.warning(
                        "otel: shared OTLP receiver subscriber callback failed: {}",
                        exc,
                    )

    async def _start(self) -> None:
        """Bind the loopback servers; disable self permanently on failure."""
        try:
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceRequest as _ExportTraceServiceRequest,
            )
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
                ExportLogsServiceRequest as _ExportLogsServiceRequest,
            )
        except ImportError:
            self._disabled = True
            return

        del _ExportTraceServiceRequest, _ExportLogsServiceRequest
        try:
            self._server = await asyncio.start_server(
                self._accept,
                host="127.0.0.1",
                port=0,
            )
        except OSError as exc:
            team_logger.warning(
                "otel: shared loopback OTLP receiver could not start; native spans disabled: {}",
                exc,
            )
            self._disabled = True
            return
        sockets = self._server.sockets or ()
        if not sockets:
            await self.aclose()
            self._disabled = True
            return
        port = int(sockets[0].getsockname()[1])
        self.endpoint = f"http://127.0.0.1:{port}/v1/traces"
        # Claude Code's OTLP exporter only speaks gRPC (its http/protobuf
        # support silently connects and sends nothing), so a gRPC listener
        # rides alongside the HTTP one. Failure is non-fatal: the HTTP side
        # keeps serving exporters that speak it.
        self._start_grpc()
        team_logger.info(
            "otel: shared loopback OTLP receiver started endpoint={} grpc_endpoint={} subscribers={}",
            self.endpoint,
            self.grpc_endpoint,
            len(self._subscribers),
        )

    def _start_grpc(self) -> None:
        """Start the gRPC OTLP listener next to the HTTP one."""
        from concurrent import futures

        import grpc

        from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

        fanout = self._fanout

        class _TraceServicer(trace_service_pb2_grpc.TraceServiceServicer):
            # gRPC dispatch requires the generated RPC method name.
            # pylint: disable-next=huawei-invalid-name
            def Export(self, request: Any, context: Any) -> Any:  # noqa: ARG002
                fanout(_decode_spans(request.SerializeToString()))
                return trace_service_pb2.ExportTraceServiceResponse()

        class _LogsServicer(logs_service_pb2_grpc.LogsServiceServicer):
            # gRPC dispatch requires the generated RPC method name.
            # pylint: disable-next=huawei-invalid-name
            def Export(self, request: Any, context: Any) -> Any:  # noqa: ARG002
                fanout(_decode_log_records(request.SerializeToString()))
                return logs_service_pb2.ExportLogsServiceResponse()

        try:
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
            trace_service_pb2_grpc.add_TraceServiceServicer_to_server(_TraceServicer(), server)
            logs_service_pb2_grpc.add_LogsServiceServicer_to_server(_LogsServicer(), server)
            port = server.add_insecure_port("127.0.0.1:0")
            if port == 0:
                team_logger.warning("otel: shared OTLP gRPC listener could not bind; claude native spans disabled")
                return
            server.start()
            self._grpc_server = server
            self.grpc_endpoint = f"http://127.0.0.1:{port}"
        except Exception as exc:  # noqa: BLE001 - gRPC is an optional side channel
            team_logger.warning("otel: shared OTLP gRPC listener failed to start: {}", exc)

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
        except Exception as exc:  # noqa: BLE001 - telemetry must not affect members
            team_logger.warning("otel: shared OTLP receiver rejected a request: {}", exc)
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
        path = request_line[1].split("?", maxsplit=1)[0]
        if path not in ("/v1/traces", "/v1/logs"):
            await self._respond(writer, status="404 Not Found")
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
        if path == "/v1/traces":
            events = _decode_spans(payload)
        else:
            events = _decode_log_records(payload)
        self._fanout(events)
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
        """Stop accepting traces and wait for active requests.

        The receiver is process-wide and normally lives as long as the process;
        this exists for tests and explicit shutdown paths.
        """
        server = self._server
        self._server = None
        self.endpoint = None
        grpc_server = self._grpc_server
        self._grpc_server = None
        self.grpc_endpoint = None
        if server is not None:
            server.close()
            await server.wait_closed()
        if grpc_server is not None:
            grpc_server.stop(0)
        tasks = [task for task in self._tasks if task is not asyncio.current_task() and not task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_CLOSE_TIMEOUT_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._subscribers.clear()
        self._disabled = True


_receiver: SharedOtlpReceiver | None = None


def get_shared_otlp_receiver() -> SharedOtlpReceiver:
    """Return the process-wide receiver singleton."""
    global _receiver
    if _receiver is None:
        _receiver = SharedOtlpReceiver()
    return _receiver


__all__ = ["OTEL_RESOURCE_SOURCE_ID", "SharedOtlpReceiver", "get_shared_otlp_receiver"]
