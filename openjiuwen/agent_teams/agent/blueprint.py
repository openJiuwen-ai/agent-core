# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamAgent static blueprint.

Frozen dataclass holding configuration that is determined at construction
time and never mutates during the agent lifecycle. This is the first
quadrant of the four-quadrant TeamAgent decomposition: static data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@dataclass(frozen=True, slots=True)
class TeamAgentBlueprint:
    """Immutable assembly blueprint for a TeamAgent.

    All fields are determined when the blueprint is built and remain
    read-only for the lifetime of the agent. Runtime-mutable state lives
    in TeamAgentState; runtime resources live in TeamInfra and
    PrivateAgentResources.
    """

    card: AgentCard
    spec: TeamAgentSpec
    ctx: TeamRuntimeContext
    role_policy: str
    language: str

    @property
    def role(self) -> TeamRole:
        """Return the team role from the runtime context."""
        return self.ctx.role

    @property
    def member_name(self) -> Optional[str]:
        """Return the member name from the runtime context."""
        return self.ctx.member_name

    @property
    def lifecycle(self) -> str:
        """Return the team lifecycle mode from the spec."""
        return self.spec.lifecycle

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        """Return the team spec from the runtime context."""
        return self.ctx.team_spec


__all__ = ["TeamAgentBlueprint"]
