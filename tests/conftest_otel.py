# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared OTel test infrastructure: InMemorySpanExporter + TracerProvider.

Creates a private ``TracerProvider`` with an ``InMemorySpanExporter`` for
unit tests to inspect spans in-process. An optional OTLP exporter is added
for e2e tests to send spans to Jaeger.

Does NOT call ``trace.set_tracer_provider()`` — that API is one-shot per
process and conflicts with ``agent_teams.observability.init_observability()``.
Instead the tracer is obtained directly via ``_PROVIDER.get_tracer()`` which
binds to this module's processor chain, so all spans flow to ``_EXPORTER``
without relying on global provider state. This mirrors the design of
``init_otel_tracer()`` in ``tracer_otel/setup.py``.
"""

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor


class InMemorySpanExporter(SpanExporter):
    """Simple in-memory span exporter for testing (OTel SDK doesn't ship one)."""

    def __init__(self):
        self._finished: list[ReadableSpan] = []

    def export(self, spans):
        self._finished.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True

    def get_finished_spans(self) -> list[ReadableSpan]:
        return list(self._finished)

    def clear(self):
        self._finished.clear()


# Module-level singleton — one provider + exporter per process.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider(resource=Resource.create({"service.name": "openjiuwen"}))
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))

# Conditionally add OTLP exporter for Jaeger e2e tests.
# Only attached if opentelemetry-exporter-otlp-proto-grpc is installed.
_OTLP_EXPORTER = None
try:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    _OTLP_EXPORTER = OTLPSpanExporter(endpoint="http://localhost:4317")
    _PROVIDER.add_span_processor(BatchSpanProcessor(_OTLP_EXPORTER))
except ImportError:
    pass

# Tracer bound to _PROVIDER — spans flow to _EXPORTER without global state.
_OTEL_TRACER = _PROVIDER.get_tracer("openjiuwen.tracer_otel.test")


def jaeger_is_available() -> bool:
    """Check if Jaeger is reachable at localhost:16686."""
    try:
        import requests
        resp = requests.get("http://localhost:16686/api/services", timeout=1)
        return resp.status_code == 200
    except Exception:
        return False
