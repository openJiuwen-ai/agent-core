# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public API for the agent_teams observability subsystem.

Team tracing is two rails, always mounted as a pair: the agent span itself
comes from the harness (``AgentObservabilityRail``, team-agnostic) and
:class:`TeamObservabilityRail` layers the ``agentteam.*`` identity onto it.
``maybe_observability_rails`` returns the pair, so no caller has to remember
both — mounting only one yields either a trace with no agent spans or agent
spans with no team identity.

Quickstart::

    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        attach_to_team_agent,
        init_observability,
        maybe_observability_rails,
    )

    init_observability(ObservabilityConfig(endpoint="http://localhost:4317"))
    team_agent = await create_agent_team(
        agents={"leader": ..., "teammate": [...]},
        rails=maybe_observability_rails(),
    )
    attach_to_team_agent(team_agent)
"""

from openjiuwen.agent_teams.observability.rail import (
    TeamObservabilityRail,
    maybe_observability_rails,
    maybe_team_observability_rail,
)
from openjiuwen.agent_teams.observability.setup import (
    acquire_observability,
    attach_to_team_agent,
    finalize_team_trace,
    init_observability,
    is_initialized,
    release_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.observability.span_context import (
    clear_ambient_team_span,
    set_ambient_team_span,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig

__all__ = [
    "ObservabilityConfig",
    "TeamObservabilityRail",
    "acquire_observability",
    "attach_to_team_agent",
    "clear_ambient_team_span",
    "finalize_team_trace",
    "init_observability",
    "is_initialized",
    "maybe_observability_rails",
    "maybe_team_observability_rail",
    "release_observability",
    "set_ambient_team_span",
    "shutdown_observability",
]
