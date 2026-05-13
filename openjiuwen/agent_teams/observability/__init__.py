# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public API for the agent_teams observability subsystem.

Quickstart::

    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        ObservabilityRail,
        attach_to_team_agent,
        init_observability,
        shutdown_observability,
    )

    init_observability(ObservabilityConfig(endpoint="http://localhost:4317"))
    team_agent = await create_agent_team(
        agents={"leader": ..., "teammate": [...]},
        rails=[ObservabilityRail()],
    )
    attach_to_team_agent(team_agent)
"""

from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.rail import ObservabilityRail
from openjiuwen.agent_teams.observability.setup import (
    attach_to_team_agent,
    detach_from_team_agent,
    init_observability,
    shutdown_observability,
)

__all__ = [
    "ObservabilityConfig",
    "ObservabilityRail",
    "attach_to_team_agent",
    "detach_from_team_agent",
    "init_observability",
    "shutdown_observability",
]
