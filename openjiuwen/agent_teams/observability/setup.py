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
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime

_MONITOR_TRACER_NAME = "openjiuwen.agent_teams.observability.monitor"


_runtime = ObservabilityRuntime()
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
            _runtime.initialize(config)
            return
        if _initializing:
            raise RuntimeError("observability initialization is already in progress")
        if _runtime.is_initialized():
            _runtime.add_span_processors(additional_span_processors)
            return

        _initializing = True
        try:
            _runtime.initialize(
                config,
                span_exporter_override=span_exporter_override,
                additional_span_processors=additional_span_processors,
            )
            _monitor_handler = OtelTeamMonitorHandler(
                config,
                tracer=_runtime.get_tracer(_MONITOR_TRACER_NAME),
            )
        except Exception:
            _runtime.shutdown()
            _monitor_handler = None
            reset_all()
            raise
        finally:
            _initializing = False


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


def force_flush_provider(timeout_millis: int = 5000) -> None:
    """Force flush the shared observability runtime."""
    _runtime.force_flush(timeout_millis)


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
            _runtime.shutdown()
        finally:
            _monitor_handler = None
            reset_all()


def get_tracer(name: str) -> Any:
    """Return a tracer bound to the active observability runtime."""
    return _runtime.get_tracer(name)


def get_config() -> ObservabilityConfig | None:
    """Return the active observability configuration."""
    return _runtime.get_config()


def is_initialized() -> bool:
    """Return whether Team observability is initialized."""
    return _runtime.is_initialized()


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
