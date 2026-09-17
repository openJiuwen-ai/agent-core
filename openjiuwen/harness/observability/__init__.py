# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public API for single-agent observability and the agent-tier span.

Two things live here. :class:`AgentObservabilityRail` is the agent tier of the
span tree for **every** DeepAgent — single agent, team member or dispatched
sub-agent — and knows nothing about teams; a layer that needs more on the same
span mounts its own rail alongside it and contributes through
:class:`AgentSpanDecoration` (see
:mod:`openjiuwen.agent_teams.observability.rail` for the Team one). The rest is
the single-agent counterpart of :mod:`openjiuwen.agent_teams.observability`:
the run root span, the session-keyed fallback that keeps it reachable from
supervisor tasks, and the provider lifecycle.

Quickstart::

    from openjiuwen.harness.observability import (
        acquire_observability,
        close_agent_run_span,
        install_subagent_observability_hook,
        open_agent_run_span,
    )

    install_subagent_observability_hook()          # once per process
    acquire_observability(ObservabilityConfig(endpoint="http://localhost:4317"))
    handle = open_agent_run_span(session_id=session_id, mode="agent.fast")
    try:
        ...  # Runner.run_agent_streaming / Runner.run_agent
    finally:
        close_agent_run_span(handle, session_id=session_id, output=answer)

Provider caveat: OpenTelemetry allows exactly ONE global ``TracerProvider`` per
process. In a process where both Team and single-agent observability are
enabled, whichever initializes first wins and the other reuses it (its
exporter/endpoint/service_name are ignored). Demands are coordinated in
:mod:`openjiuwen.extensions.observability.demand`, so releasing one runtime
never tears down a provider the other still needs.
"""

from openjiuwen.harness.observability.rail import (
    AgentObservabilityRail,
    AgentSpanDecoration,
    AgentSpanScope,
    maybe_agent_observability_rail,
)
from openjiuwen.harness.observability.run_span import (
    build_run_span_name,
    close_agent_run_span,
    open_agent_run_span,
)
from openjiuwen.harness.observability.setup import (
    acquire_observability,
    get_config,
    is_initialized,
    is_tracing_enabled,
    release_observability,
)
from openjiuwen.harness.observability.span_context import (
    current_session_id,
    install_root_span_fallback,
    resolve_run_root_span,
)
from openjiuwen.harness.observability.subagent import (
    attach_subagent_observability,
    install_subagent_observability_hook,
)

__all__ = [
    "AgentObservabilityRail",
    "AgentSpanDecoration",
    "AgentSpanScope",
    "acquire_observability",
    "attach_subagent_observability",
    "build_run_span_name",
    "close_agent_run_span",
    "current_session_id",
    "get_config",
    "install_root_span_fallback",
    "install_subagent_observability_hook",
    "is_initialized",
    "is_tracing_enabled",
    "maybe_agent_observability_rail",
    "open_agent_run_span",
    "release_observability",
    "resolve_run_root_span",
]
