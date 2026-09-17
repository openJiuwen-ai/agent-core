# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-agent lifecycle over the shared observability runtime.

The counterpart of :mod:`openjiuwen.agent_teams.observability.setup`: it owns
what a single-agent run needs on top of the shared provider — the run-root
fallback and the agent-tier rail wiring — and holds its share of the
process-wide provider demand so a single-agent shutdown never tears down a
provider the Team subsystem still depends on.

Once the provider is up, the generic ``OtelCallbackHandler`` is registered
against the global ``Runner.callback_framework`` and LLM/tool events are emitted
from the shared foundation layer for *every* agent, team or not — so having the
provider initialized is already enough for automatic LLM/tool span tracing. The
team-only ``OtelTeamMonitorHandler`` (team/member/task/message spans) is
intentionally never attached here.
"""

from __future__ import annotations

from collections.abc import Sequence
import threading

from opentelemetry.sdk.trace import SpanProcessor

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import (
    acquire_observability_demand,
    release_observability_demand,
)
from openjiuwen.extensions.observability.setup import (
    get_config as get_shared_config,
    init_observability as init_shared_observability,
    is_initialized as is_shared_observability_initialized,
    shutdown_observability as shutdown_shared_observability,
)
from openjiuwen.extensions.observability.span_context import reset_state
from openjiuwen.harness.observability.span_context import (
    install_root_span_fallback,
    reset_run_root_spans,
)

_RUNTIME_KEY = "agent"

_lifecycle_lock = threading.RLock()
# Whether single-agent runs should open root spans. Distinct from "a provider
# exists": the Team subsystem may hold the provider up in the same process
# while single-agent tracing is off, and single-agent spans must not appear
# in that case.
_tracing_enabled = False


def _init_agent_runtime(
    config: ObservabilityConfig,
    additional_span_processors: Sequence[SpanProcessor],
) -> None:
    """Initialize the shared runtime and the single-agent-only wiring."""
    init_shared_observability(
        config,
        additional_span_processors=additional_span_processors,
    )
    install_root_span_fallback()


def _shutdown_agent_runtime() -> None:
    """Shut the shared runtime down and drop the span state it left behind."""
    shutdown_shared_observability()
    reset_state()


def acquire_observability(config: ObservabilityConfig) -> bool:
    """Turn single-agent tracing on, initializing the provider if needed.

    Initialization errors are propagated so the caller can distinguish an
    optional tracing failure from a required evolution capture failure.

    Args:
        config: Observability configuration for this runtime. Ignored when a
            provider already exists — OpenTelemetry keeps the first one.

    Returns:
        Whether a provider already existed, i.e. this runtime is reusing one
        owned by another subsystem and its exporter settings were not applied.
    """
    global _tracing_enabled
    with _lifecycle_lock:
        provider_existed = acquire_observability_demand(
            _RUNTIME_KEY,
            observability_config=config,
            initializer=_init_agent_runtime,
        )
        _tracing_enabled = True
        return provider_existed


def release_observability() -> None:
    """Turn single-agent tracing off and release its provider demand.

    The provider itself is only shut down when no other runtime still holds a
    demand on it.
    """
    global _tracing_enabled
    with _lifecycle_lock:
        _tracing_enabled = False
        reset_run_root_spans()
        release_observability_demand(_RUNTIME_KEY, finalizer=_shutdown_agent_runtime)


def is_tracing_enabled() -> bool:
    """Return whether single-agent runs should open root spans."""
    return _tracing_enabled


def is_initialized() -> bool:
    """Return whether the shared observability runtime is initialized."""
    return is_shared_observability_initialized()


def get_config() -> ObservabilityConfig | None:
    """Return the active shared observability configuration."""
    return get_shared_config()


__all__ = [
    "acquire_observability",
    "get_config",
    "is_initialized",
    "is_tracing_enabled",
    "release_observability",
]
