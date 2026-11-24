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
# Runtimes that also want their captured spans exported. A runtime may hold a
# demand purely for capture (for example a trajectory store) without opening
# the export gate.
_EXPORT_RUNTIMES: set[str] = set()
_DEMAND_LOCK = threading.RLock()
# True while a demand-holding provider this coordinator created is being torn
# down: the releasing runtime's export demand is held through the finalizer so
# spans flushed during teardown keep the gate state they were captured under.
_TEARDOWN: bool = False
# True once a coordinated acquire finds a provider it did not create: that
# owner's export intent predates any coordinated demand and cannot be taken
# away by a capture-only acquire.
_EXTERNAL_EXPORT_OWNER: bool = False
# Published export-gate decision, recomputed under ``_DEMAND_LOCK`` whenever
# the bookkeeping above reaches a consistent state. ``is_export_enabled``
# reads it without the lock: it runs inside every ``span.end``, and taking the
# lock there would block business threads behind a provider teardown (which
# holds the lock through exporter flush) and invert the lock order against
# ``ObservabilityRuntime`` (its shutdown ends spans while holding the runtime
# lock, whereas acquire/release take this lock first, then the runtime lock).
_EXPORT_GATE_OPEN: bool = True
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
    # Lock-free read of a write-once reference: business threads publish here
    # and must not wait behind a provider teardown holding ``_DEMAND_LOCK``.
    processor = _SPAN_RECORD_PROCESSOR
    if processor is None:
        return
    try:
        processor.publish_snapshot(span, update_kind)
    except Exception as exc:
        # Snapshot delivery is optional observability and must not escape into
        # the model, tool, or agent execution path.
        logger.warning("otel: recording span snapshot publish failed - {}", exc)


def publish_stream_frame(record: Any) -> None:
    """Publish one model-stream frame through the shared processor."""
    # Lock-free for the same reason as ``publish_span_snapshot``.
    processor = _SPAN_RECORD_PROCESSOR
    if processor is None:
        return
    try:
        processor.publish_stream_frame(record)
    except Exception as exc:
        # Frame delivery is optional observability and must not escape into
        # the model, tool, or agent execution path.
        logger.warning("otel: stream frame publish failed - {}", exc)


def set_export_demand(runtime: str, wanted: bool) -> None:
    """Record whether *runtime* wants captured spans exported.

    Called on every acquire so a config hot-reload flips the export gate in
    place — the provider keeps the exporter it was created with (OTel allows
    no processor removal), so re-acquiring with a new *wanted* value is the
    only way to change whether ended spans reach it.

    Args:
        runtime: Runtime key, one of ``"agent"`` / ``"team"``.
        wanted: Whether this runtime's requests should reach the exporter.

    Raises:
        ValueError: If *runtime* is not a known runtime key.
    """
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")
        if wanted:
            _EXPORT_RUNTIMES.add(runtime)
        else:
            _EXPORT_RUNTIMES.discard(runtime)
        _refresh_export_gate()


def _compute_export_gate() -> bool:
    """Derive the gate decision from the bookkeeping; caller holds the lock."""
    if _TEARDOWN:
        return bool(_EXPORT_RUNTIMES)
    if not _ACTIVE_RUNTIMES:
        # No coordinated runtime controls this provider: legacy behaviour
        # for direct ``init_observability`` callers (RL collectors, RSI
        # evaluators) is preserved.
        return True
    if _EXTERNAL_EXPORT_OWNER:
        return True
    return bool(_EXPORT_RUNTIMES & _ACTIVE_RUNTIMES)


def _refresh_export_gate() -> None:
    """Publish the gate decision; call under the lock at consistent states only."""
    global _EXPORT_GATE_OPEN
    _EXPORT_GATE_OPEN = _compute_export_gate()


def is_export_enabled() -> bool:
    """Return whether ended spans should reach the config-built exporter.

    The gate never silences an externally owned provider: a caller that
    initialized the provider directly keeps the export behaviour it was
    created with, and a capture-only demand from a coordinated runtime cannot
    take that away — it did not create the provider. For a provider the
    coordinator created, export follows the coordinated demands of runtimes
    that are actually active. During provider teardown the releasing
    runtime's last demand is held, so spans flushed by the finalizer keep
    that runtime's export decision.

    Lock-free by design: this runs synchronously inside every ``span.end``,
    so it reads the decision published under ``_DEMAND_LOCK`` instead of
    taking the lock (see ``_EXPORT_GATE_OPEN``).
    """
    return _EXPORT_GATE_OPEN


def acquire_observability_demand(
    runtime: str,
    *,
    observability_config: ObservabilityConfig,
    initializer: ObservabilityInitializer,
    export: bool = True,
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
        export: Whether this runtime also wants captured spans exported.
            Recorded before the already-active early return below, so every
            acquire refreshes the export gate — a capture-only runtime passes
            False and later config changes can flip it without a provider
            rebuild. Rolled back when initialization fails: an acquire that
            did not complete must not change shared state.

    Returns:
        Whether a provider already existed before this call — the caller owns
        nothing new in that case and its exporter settings were not applied.

    Raises:
        ValueError: If *runtime* is not a known runtime key.
        RuntimeError: If initialization completed without creating a provider.
    """
    global _PROVIDER_OWNED, _EXTERNAL_EXPORT_OWNER, _TEARDOWN
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")

        previous_export = runtime in _EXPORT_RUNTIMES
        set_export_demand(runtime, export)

        from openjiuwen.extensions.observability.setup import (
            is_initialized,
            shutdown_observability,
        )

        if runtime in _ACTIVE_RUNTIMES and is_initialized():
            return True

        provider_existed = is_initialized()
        try:
            initializer(
                observability_config,
                (get_trajectory_span_processor(), get_span_record_processor()),
            )
            if not is_initialized():
                raise RuntimeError(
                    f"{runtime} observability initialization did not create a provider"
                )
        except BaseException:
            # Runtime wiring may fail after shared initialization succeeded.
            # Only dispose of a provider created by this attempt; an existing
            # provider still belongs to its external owner or other runtimes.
            _EXPORT_RUNTIMES.discard(runtime)
            if not provider_existed and is_initialized():
                # Keep failed initialization's leftover spans out of the
                # exporter when shutdown force-ends them with no active demand.
                _TEARDOWN = True
                _refresh_export_gate()
                try:
                    shutdown_observability()
                except Exception as exc:
                    logger.warning("otel: failed acquire provider cleanup failed - {}", exc)
                finally:
                    _TEARDOWN = False
            # Restores the caller's previous demand and republishes the gate.
            set_export_demand(runtime, previous_export)
            raise
        if provider_existed:
            if not _PROVIDER_OWNED:
                # The provider was created by a direct init caller; its export
                # intent predates this demand and outlives it.
                _EXTERNAL_EXPORT_OWNER = True
        else:
            _PROVIDER_OWNED = True
            _EXTERNAL_EXPORT_OWNER = False
        _ACTIVE_RUNTIMES.add(runtime)
        _refresh_export_gate()
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
    global _PROVIDER_OWNED, _TEARDOWN, _EXTERNAL_EXPORT_OWNER
    with _DEMAND_LOCK:
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown observability runtime: {runtime}")
        _ACTIVE_RUNTIMES.discard(runtime)
        if _ACTIVE_RUNTIMES or not _PROVIDER_OWNED:
            # Others still hold the provider, or it belongs to an external
            # owner that keeps its own export behaviour.
            _EXPORT_RUNTIMES.discard(runtime)
            _refresh_export_gate()
            return

        from openjiuwen.extensions.observability.setup import (
            is_initialized,
            shutdown_observability,
        )

        # Last demand on a provider this coordinator created. Hold the
        # releasing runtime's export demand through the finalizer: spans the
        # teardown force-ends keep the gate state they were captured under,
        # so capture-only leftovers are dropped while a runtime that asked for
        # export still drains. Only then clear the coordinator bookkeeping.
        # The gate is published only after ``_TEARDOWN`` is set, so lock-free
        # readers never see the transient "no active runtime" open state.
        _TEARDOWN = True
        _refresh_export_gate()
        try:
            if is_initialized():
                if finalizer is None:
                    shutdown_observability()
                else:
                    finalizer()
        finally:
            _TEARDOWN = False
            _EXPORT_RUNTIMES.clear()
            _PROVIDER_OWNED = False
            _EXTERNAL_EXPORT_OWNER = False
            _refresh_export_gate()


def reset_observability_demands() -> None:
    """Reset demand bookkeeping for isolated tests."""
    global _PROVIDER_OWNED, _TEARDOWN, _EXTERNAL_EXPORT_OWNER
    with _DEMAND_LOCK:
        _ACTIVE_RUNTIMES.clear()
        _EXPORT_RUNTIMES.clear()
        _TEARDOWN = False
        _EXTERNAL_EXPORT_OWNER = False
        _PROVIDER_OWNED = False
        _refresh_export_gate()


__all__ = [
    "ObservabilityFinalizer",
    "ObservabilityInitializer",
    "acquire_observability_demand",
    "get_span_record_processor",
    "get_trajectory_span_processor",
    "is_export_enabled",
    "publish_span_snapshot",
    "release_observability_demand",
    "reset_observability_demands",
    "set_export_demand",
]
