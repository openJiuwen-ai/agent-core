# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-wide provider demand coordination shared by every runtime.

OpenTelemetry allows exactly ONE global ``TracerProvider`` per process, and
initialization is a no-op once it exists. In a process where several runtimes
(single agent, Team) enable observability, whichever initializes first wins and
the others silently reuse its provider — so no single runtime may tear the
provider down while another still depends on it.

This module holds that shared bookkeeping: each runtime *acquires* a demand
before use and *releases* it on shutdown, and the provider is only shut down
once the last demand is gone. It stays runtime-agnostic — the runtime-specific
initializer is supplied by the caller, so the layer that owns a runtime
(``openjiuwen.harness.observability`` / ``openjiuwen.agent_teams.observability``)
keeps owning its own lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import threading
from typing import Any

from opentelemetry.sdk.trace import SpanProcessor

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.config import ObservabilityConfig

# Runtimes allowed to hold a demand. A closed set on purpose: a typo in the
# runtime key would otherwise silently create a demand nobody ever releases,
# pinning the provider for the life of the process.
_RUNTIMES = frozenset({"agent", "team"})
_ACTIVE_RUNTIMES: set[str] = set()
_DEMAND_LOCK = threading.RLock()
# Only shut down a provider that this coordinator created. A provider may be
# initialized by an RL collector or another SDK entry point before any runtime
# acquires a demand; those callers retain ownership of its lifecycle.
_PROVIDER_OWNED = False
_TRAJECTORY_SPAN_PROCESSOR: Any | None = None
_SPAN_RECORD_PROCESSOR: Any | None = None

# Initializes one runtime's observability over the shared provider. Receives
# the runtime's config and the span processors the coordinator wants attached.
ObservabilityInitializer = Callable[[ObservabilityConfig, Sequence[SpanProcessor]], None]

# Tears one runtime's observability down together with the shared provider.
ObservabilityFinalizer = Callable[[], None]


def get_trajectory_span_processor() -> Any:
    """Return the process-wide trajectory span processor.

    Evolution captures trajectories off the same spans observability exports,
    so every runtime must attach the *same* processor instance — a second one
    would duplicate every trajectory.

    Returns:
        The shared :class:`TrajectorySpanProcessor`, created on first use.
    """
    global _TRAJECTORY_SPAN_PROCESSOR
    with _DEMAND_LOCK:
        if _TRAJECTORY_SPAN_PROCESSOR is not None:
            return _TRAJECTORY_SPAN_PROCESSOR

        from openjiuwen.agent_evolving.trajectory.processor import (
            TrajectorySpanProcessor,
        )

        _TRAJECTORY_SPAN_PROCESSOR = TrajectorySpanProcessor()
        return _TRAJECTORY_SPAN_PROCESSOR


def get_span_record_processor() -> Any:
    """Return the process-wide complete OTLP span-record processor."""
    global _SPAN_RECORD_PROCESSOR
    with _DEMAND_LOCK:
        if _SPAN_RECORD_PROCESSOR is not None:
            return _SPAN_RECORD_PROCESSOR

        from openjiuwen.extensions.observability.span_record_processor import (
            SpanRecordProcessor,
        )

        _SPAN_RECORD_PROCESSOR = SpanRecordProcessor()
        return _SPAN_RECORD_PROCESSOR


def publish_span_snapshot(span: Any, update_kind: str) -> None:
    """Publish one recording-span snapshot through the shared processor."""
    with _DEMAND_LOCK:
        processor = _SPAN_RECORD_PROCESSOR
    if processor is None:
        return
    try:
        processor.publish_snapshot(span, update_kind)
    except Exception as exc:
        # Snapshot delivery is optional observability and must not escape into
        # the model, tool, or agent execution path.
        logger.warning("otel: recording span snapshot publish failed - {}", exc)


def acquire_observability_demand(
    runtime: str,
    *,
    observability_config: ObservabilityConfig,
    initializer: ObservabilityInitializer,
) -> bool:
    """Acquire one runtime's demand for the shared provider.

    Initialization errors are deliberately propagated so the caller can
    distinguish an optional tracing failure from a required evolution capture
    failure.

    Args:
        runtime: Runtime key, one of ``"agent"`` / ``"team"``.
        observability_config: Config handed to *initializer*; ignored when the
            provider already exists (OTel keeps the first provider).
        initializer: Runtime-specific initialization, called with the config
            and the span processors this coordinator shares across runtimes.

    Returns:
        Whether a provider already existed before this call — the caller owns
        nothing new in that case and its exporter settings were not applied.

    Raises:
        ValueError: If *runtime* is not a known runtime key.
        RuntimeError: If initialization completed without creating a provider.
    """
    global _PROVIDER_OWNED
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")

        from openjiuwen.extensions.observability.setup import is_initialized

        if runtime in _ACTIVE_RUNTIMES and is_initialized():
            return True

        provider_existed = is_initialized()
        initializer(
            observability_config,
            (get_trajectory_span_processor(), get_span_record_processor()),
        )
        if not is_initialized():
            raise RuntimeError(
                f"{runtime} observability initialization did not create a provider"
            )
        if not provider_existed:
            _PROVIDER_OWNED = True
        _ACTIVE_RUNTIMES.add(runtime)
        return provider_existed


def release_observability_demand(
    runtime: str,
    *,
    finalizer: ObservabilityFinalizer | None = None,
) -> None:
    """Release one runtime's demand, shutting the provider down when it is the last.

    Args:
        runtime: Runtime key, one of ``"agent"`` / ``"team"``.
        finalizer: Runtime-specific teardown, invoked only when this release
            drops the last demand on a provider this coordinator created.
            Defaults to the shared runtime shutdown.

    Raises:
        ValueError: If *runtime* is not a known runtime key.
    """
    global _PROVIDER_OWNED
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")
        _ACTIVE_RUNTIMES.discard(runtime)
        if _ACTIVE_RUNTIMES or not _PROVIDER_OWNED:
            return

        from openjiuwen.extensions.observability.setup import (
            is_initialized,
            shutdown_observability,
        )

        if is_initialized():
            if finalizer is None:
                shutdown_observability()
            else:
                finalizer()
        _PROVIDER_OWNED = False


def reset_observability_demands() -> None:
    """Reset demand bookkeeping for isolated tests."""
    global _PROVIDER_OWNED
    with _DEMAND_LOCK:
        _ACTIVE_RUNTIMES.clear()
        _PROVIDER_OWNED = False


__all__ = [
    "ObservabilityFinalizer",
    "ObservabilityInitializer",
    "acquire_observability_demand",
    "get_span_record_processor",
    "get_trajectory_span_processor",
    "publish_span_snapshot",
    "release_observability_demand",
    "reset_observability_demands",
]
