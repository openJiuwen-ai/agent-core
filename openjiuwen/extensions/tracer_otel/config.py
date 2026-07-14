# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel tracer configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

_SAMPLE_RATE_RANGE = (0.0, 1.0)


@dataclass(frozen=True)
class OtelTracerConfig:
    """Immutable configuration for the OTel tracer extension.

    Independent from ``ObservabilityConfig`` — tracer_otel lives in extensions
    and should not depend on agent_teams.
    """

    tracer_name: str = "openjiuwen.tracer.otel"
    exporter_type: str = "otlp"  # otlp / console
    exporter_endpoint: str | None = None
    protocol: str = "grpc"  # grpc / http — OTLP transport protocol
    headers: dict[str, str] = field(default_factory=dict)  # custom headers for OTLP exporter (auth, etc.)
    service_name: str = "openjiuwen"
    service_version: str | None = None
    sample_rate: float = 1.0  # 0.0 ~ 1.0, trace sampling probability
    schedule_delay_millis: int = 5000  # BatchSpanProcessor export interval (ms)
    export_timeout_ms: int = 30000  # BatchSpanProcessor export timeout
    max_export_batch_size: int = 512  # BatchSpanProcessor max batch size
    redaction_enabled: bool = True  # SHA-256 hash when True (backward compat; see redact_prompts / redact_completions)
    redact_prompts: bool | None = None  # None → fallback to redaction_enabled; True/False overrides
    redact_completions: bool | None = None  # None → fallback to redaction_enabled; True/False overrides
    max_attr_length: int = 4096  # truncation cap for attribute values

    def __post_init__(self):
        if not (_SAMPLE_RATE_RANGE[0] <= self.sample_rate <= _SAMPLE_RATE_RANGE[1]):
            raise ValueError(f"sample_rate must be between {_SAMPLE_RATE_RANGE[0]} and {_SAMPLE_RATE_RANGE[1]},"
                             f" got {self.sample_rate}")
