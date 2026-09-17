# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pydantic configuration for the OpenTelemetry observability subsystem."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObservabilityConfig(BaseModel):
    """Runtime knobs for the observability subsystem.

    Attributes:
        enabled: Master switch. When False, init_observability is a no-op.
        service_name: OTel resource attribute service.name.
        exporter: Exporter backend type. ``file`` writes OTLP JSON directly
            to ``traces_dir`` without a collector, carrying the same Langfuse
            projection as the ``langfuse`` exporter (it is the file WAL for
            Langfuse ingestion). ``langfuse`` exports via OTLP HTTP with
            Langfuse auth and ingestion-version headers.
        endpoint: OTLP endpoint URL (gRPC default localhost:4317; HTTP 4318).
            For ``langfuse`` it must point at the Langfuse OTLP HTTP route
            (``.../api/public/otel/v1/traces``). Ignored when ``exporter``
            is ``file``.
        sample_rate: Parent-based ratio sampler rate (0.0 - 1.0).
        redact_prompts: When True, hash/truncate prompt contents.
        redact_completions: When True, hash/truncate completion contents.
        attribute_value_max_length: General cap on string attribute length.
            Canonical system instructions are exempt so trajectory comparison
            always receives their complete value. Default 40960 (langfuse
            recommendation).
        max_attributes: Maximum number of attributes per span. Default 200.
            Passed to OTel SDK SpanLimits. OTel's BoundedAttributes uses FIFO
            eviction (oldest first), so attributes written before the prompt
            loop (operation.name / provider.name / request.model)
            would be evicted once the prompt attributes fill the budget.
            200 is the span-wide cap including a ~30-attribute reservation for
            non-prompt attrs (top system + request params + team context +
            output-stage completion/usage/finish_reason); only the trailing
            N prompt messages are written so the top attrs survive.
        backend: DEPRECATED. Kept only to translate legacy configs during
            initialization: ``backend="langfuse"`` becomes
            ``exporter="langfuse"`` with a deprecation warning. It never
            reaches the collection layer (callback/bridge/rail) and no longer
            influences telemetry shape.
        langfuse_ingestion_version: Langfuse ingestion protocol major
            version. v4 adds the ``x-langfuse-ingestion-version: 4`` header.
        langfuse_legacy_prompt_projection: Additionally emit the deprecated
            ``gen_ai.prompt.{i}.*`` / ``gen_ai.completion.{i}.*`` attributes
            in the Langfuse adapter for old self-hosted Langfuse versions.
        export_timeout_ms: Span exporter shutdown timeout.
        traces_dir: Root directory for the ``file`` exporter. One
            append-only ``traces-<YYYY-MM-DD>.jsonl`` file per calendar
            day, written directly under this dir; each line is a
            standalone single-span OTLP JSON request. Spans from all
            traces share the file — the collector splits them by
            ``traceId`` on ingest. Paired with BatchSpanProcessor so
            span-end does not block the business thread.
        file_retention_days: Trace files older than this (by mtime) are
            lazily deleted by the ``file`` exporter. Default 7 days.
    """

    enabled: bool = True
    service_name: str = "openjiuwen-agent-teams"
    exporter: Literal["otlp_grpc", "otlp_http", "langfuse", "console", "file"] = "otlp_grpc"
    endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    redact_prompts: bool = False
    redact_completions: bool = False
    attribute_value_max_length: int = 40960
    max_attributes: int = 200
    # Deprecated legacy selector; translated once at initialization.
    backend: Literal["langfuse", "otlp"] | None = None
    langfuse_ingestion_version: Literal[3, 4] = 4
    langfuse_legacy_prompt_projection: bool = False
    export_timeout_ms: int = 5000
    # Langfuse authentication (for OTLP export via Langfuse OTLP endpoint)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # file exporter
    traces_dir: str = "./traces"
    file_retention_days: int = 7
