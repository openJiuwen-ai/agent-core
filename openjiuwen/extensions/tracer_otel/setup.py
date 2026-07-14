# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel tracer provider initialization.

Creates a ``TracerProvider`` from ``OtelTracerConfig`` and returns an
OTel ``Tracer`` instance.  Independent from ``observability`` — the
resulting provider is used solely by tracer_otel extension handlers.

Does NOT call ``trace.set_tracer_provider()`` so that it never conflicts
with ``agent_teams.observability.init_observability()`` (which manages
the global TracerProvider). The returned tracer is bound directly to
this module's TracerProvider, so OtelAgentHandler / OtelWorkflowHandler
work correctly without relying on global state.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig


def init_otel_tracer(config: OtelTracerConfig) -> trace.Tracer:
    """Initialize an OTel TracerProvider and return a Tracer instance.

    Supported ``exporter_type`` values:
    - ``console``: ``ConsoleSpanExporter`` (debugging)
    - ``otlp``: ``OTLPSpanExporter`` (requires endpoint)

    Supported ``protocol`` values (for otlp exporter):
    - ``grpc``: uses ``opentelemetry.exporter.otlp.proto.grpc`` exporter
    - ``http``: uses ``opentelemetry.exporter.otlp.proto.http`` exporter

    Args:
        config: Immutable ``OtelTracerConfig`` instance.

    Returns:
        An ``opentelemetry.trace.Tracer`` ready for use in handlers.
    """
    resource = Resource.create({
        "service.name": config.service_name,
        "service.version": config.service_version or "unknown",
    })

    sampler = ParentBasedTraceIdRatio(rate=config.sample_rate)
    provider = TracerProvider(sampler=sampler, resource=resource)

    if config.exporter_type == "console":
        exporter = ConsoleSpanExporter()
        processor = SimpleSpanProcessor(exporter)
    elif config.exporter_type == "otlp":
        exporter = _create_otlp_exporter(config)
        processor = BatchSpanProcessor(
            exporter,
            schedule_delay_millis=config.schedule_delay_millis,
            max_export_batch_size=config.max_export_batch_size,
            export_timeout_millis=config.export_timeout_ms,
        )
    else:
        raise_error(StatusCode.COMMON_TASK_CONFIG_ERROR,
                     error_msg=f"unknown exporter_type '{config.exporter_type}', supported: console, otlp")

    provider.add_span_processor(processor)

    # Intentionally NOT calling trace.set_tracer_provider() here.
    # tracer_otel handlers hold a direct tracer reference, so global
    # provider state is not needed. This avoids conflicts with
    # agent_teams.observability.init_observability() which also calls
    # set_tracer_provider (one-shot per process).
    return provider.get_tracer(config.tracer_name)


def _create_otlp_exporter(config: OtelTracerConfig) -> SpanExporter:
    """Create OTLP span exporter based on protocol and headers config.

    ``protocol="grpc"`` → ``OTLPSpanExporter`` from grpc package.
    ``protocol="http"`` → ``OTLPSpanExporter`` from http package.
    ``headers`` are passed to the exporter for authentication.
    """
    endpoint = config.exporter_endpoint
    headers = config.headers or {}

    if config.protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        # HTTP exporter expects endpoint to include path: /v1/traces
        if endpoint and not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)
    elif config.protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)
    else:
        raise_error(StatusCode.COMMON_TASK_CONFIG_ERROR,
                     error_msg=f"unknown otlp protocol '{config.protocol}', supported: grpc, http")
        return None
