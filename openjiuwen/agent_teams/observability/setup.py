# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-specific monitor lifecycle over the shared observability runtime."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter

from openjiuwen.agent_teams.observability.monitor_handler import OtelTeamMonitorHandler
from openjiuwen.agent_teams.observability.span_context import finalize_trace, reset_all
from openjiuwen.core.common.logging import team_logger
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import (
    acquire_observability_demand,
    release_observability_demand,
)
from openjiuwen.extensions.observability.setup import (
    force_flush_provider,
    get_config as get_shared_config,
    get_observability_runtime,
    get_tracer,
    init_observability as init_shared_observability,
    is_initialized as is_shared_observability_initialized,
    shutdown_observability as shutdown_shared_observability,
)

_MONITOR_TRACER_NAME = "openjiuwen.agent_teams.observability.monitor"


_runtime = get_observability_runtime()
_monitor_handler: OtelTeamMonitorHandler | None = None
_lifecycle_lock = threading.RLock()
_initializing = False


def init_observability(
    config: ObservabilityConfig,
    *,
    span_exporter_override: SpanExporter | None = None,
    additional_span_processors: Sequence[SpanProcessor] = (),
) -> None:
    """Initialize the shared runtime and register the Team monitor only."""
    global _monitor_handler, _initializing

    with _lifecycle_lock:
        if not config.enabled:
            init_shared_observability(config)
            return
        if _initializing:
            raise RuntimeError("observability initialization is already in progress")

        _initializing = True
        try:
            init_shared_observability(
                config,
                span_exporter_override=span_exporter_override,
                additional_span_processors=additional_span_processors,
            )
            if _monitor_handler is None:
                _monitor_handler = OtelTeamMonitorHandler(
                    config,
                    tracer=get_tracer(_MONITOR_TRACER_NAME),
                )
        except Exception:
            shutdown_shared_observability()
            _monitor_handler = None
            reset_all()
            raise
        finally:
            _initializing = False


def _init_team_runtime(
    config: ObservabilityConfig,
    additional_span_processors: Sequence[SpanProcessor],
) -> None:
    """Initialize the Team runtime with the coordinator's shared processors."""
    init_observability(config, additional_span_processors=additional_span_processors)


def acquire_observability(config: ObservabilityConfig) -> bool:
    """Initialize Team observability while holding its provider demand.

    Use this instead of :func:`init_observability` in a process that may also
    run single-agent observability: the demand bookkeeping is what keeps either
    subsystem's shutdown from tearing down a provider the other still uses.

    Args:
        config: Observability configuration for the Team runtime. Ignored when
            a provider already exists — OpenTelemetry keeps the first one.

    Returns:
        Whether a provider already existed, i.e. Team is reusing one owned by
        another subsystem and its exporter settings were not applied.
    """
    return acquire_observability_demand(
        "team",
        observability_config=config,
        initializer=_init_team_runtime,
    )


def release_observability() -> None:
    """Release the Team provider demand, shutting down when it is the last."""
    release_observability_demand("team", finalizer=shutdown_observability)


def finalize_team_trace(team_name: str) -> None:
    """Close Team monitor spans and the Team root trace."""
    with _lifecycle_lock:
        if not team_name:
            return

        team_logger.info("otel: finalize_team_trace for team={}", team_name)
        if _monitor_handler is not None:
            _monitor_handler.close_team_spans(team_name)

        finalize_trace(team_name)
        force_flush_provider()


def shutdown_observability() -> None:
    """Close Team monitor spans and shut down the shared runtime."""
    global _monitor_handler

    with _lifecycle_lock:
        try:
            if _monitor_handler is not None:
                try:
                    _monitor_handler.close_all_spans()
                except Exception as exc:
                    team_logger.warning("otel: monitor span cleanup failed - {}", exc)
            shutdown_shared_observability()
        finally:
            _monitor_handler = None
            reset_all()


def get_config() -> ObservabilityConfig | None:
    """Return the active shared observability configuration."""

    return get_shared_config()


def is_initialized() -> bool:
    """Return whether the shared observability runtime is initialized."""

    return is_shared_observability_initialized()


def attach_to_team_agent(team_agent: Any) -> None:
    """Register the monitor handler on a leader TeamAgent once."""
    with _lifecycle_lock:
        if _monitor_handler is None:
            team_logger.warning("attach_to_team_agent called before init_observability")
            return
        state = getattr(team_agent, "_state", None)
        listeners = getattr(state, "event_listeners", None) if state is not None else None
        if listeners is not None and _monitor_handler in listeners:
            return
        team_agent.add_event_listener(_monitor_handler)
