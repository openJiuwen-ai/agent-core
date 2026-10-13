# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-wide lifecycle facade for shared observability primitives."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime


_runtime = ObservabilityRuntime()


def init_observability(
    config: ObservabilityConfig,
    *,
    span_exporter_override: SpanExporter | None = None,
    additional_span_processors: Sequence[SpanProcessor] = (),
) -> None:
    """Initialize the shared runtime or attach additional processors."""

    _runtime.initialize(
        config,
        span_exporter_override=span_exporter_override,
        additional_span_processors=additional_span_processors,
    )


def shutdown_observability() -> None:
    """Shut down the shared runtime."""

    _runtime.shutdown()


def force_flush_provider(timeout_millis: int = 5000) -> None:
    """Force flush the shared runtime."""

    _runtime.force_flush(timeout_millis)


def get_tracer(name: str) -> Any:
    """Return a tracer bound to the shared runtime."""

    return _runtime.get_tracer(name)


def get_config() -> ObservabilityConfig | None:
    """Return the active observability configuration."""

    return _runtime.get_config()


def is_initialized() -> bool:
    """Return whether the shared runtime is initialized."""

    return _runtime.is_initialized()


def get_observability_runtime() -> ObservabilityRuntime:
    """Return the shared runtime for integration-specific lifecycle coordination."""

    return _runtime


__all__ = [
    "force_flush_provider",
    "get_config",
    "get_observability_runtime",
    "get_tracer",
    "init_observability",
    "is_initialized",
    "shutdown_observability",
]
