# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member handle factory.

Centralizes TeamMember construction so leader and teammate paths share
one implementation. The two paths still differ in *when* they call this
factory:

* teammate: synchronous during ``configure()`` — the team row already
  exists in the database by the time a teammate is spawned, so the
  handle can be created immediately.
* leader: lazy from ``_on_teammate_created(self.member_name)`` — the
  leader's own team row only materializes after ``BuildTeamTool`` runs,
  so the handle has to wait for the resulting member-created callback.

This timing asymmetry is a *data* dependency (team_row.exists), not a
code-organization issue, so it cannot be unified — only the construction
call itself can.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Optional,
)

from openjiuwen.agent_teams.agent.member import TeamMember

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
    from openjiuwen.agent_teams.agent.infra import TeamInfra
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def create_member_handle(
    *,
    member_name: str,
    blueprint: "TeamAgentBlueprint",
    infra: "TeamInfra",
    agent_card: "AgentCard",
) -> Optional[TeamMember]:
    """Create a TeamMember handle, or None when the backend is missing.

    Returns None if no team backend is bound in ``infra`` — callers must
    handle this case rather than relying on a guaranteed instance.
    """
    if infra.team_backend is None:
        return None
    return TeamMember(
        member_name=member_name,
        team_name=infra.team_backend.team_name,
        agent_card=agent_card,
        db=infra.team_backend.db,
        messager=infra.messager,
        desc=blueprint.ctx.persona,
    )


__all__ = ["create_member_handle"]
