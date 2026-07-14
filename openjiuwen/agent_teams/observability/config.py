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
        exporter: Exporter backend type.
        endpoint: OTLP endpoint URL (gRPC default localhost:4317; HTTP 4318).
        sample_rate: Parent-based ratio sampler rate (0.0 - 1.0).
        redact_prompts: When True, hash/truncate prompt contents.
        redact_completions: When True, hash/truncate completion contents.
        attribute_value_max_length: Hard cap on string attribute length.
        export_timeout_ms: Span exporter shutdown timeout.
    """

    enabled: bool = True
    service_name: str = "openjiuwen-agent-teams"
    exporter: Literal["otlp_grpc", "otlp_http", "console"] = "otlp_grpc"
    endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    redact_prompts: bool = False
    redact_completions: bool = False
    attribute_value_max_length: int = 8192
    export_timeout_ms: int = 5000
