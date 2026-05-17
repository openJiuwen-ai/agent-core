# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member handle factory.

Centralizes TeamMember construction so every role shares one
implementation and one call site.

``create_member_handle`` is a pure constructor: it only needs a bound
``team_backend`` (``setup_infra`` provides that for all roles) and never
touches the database. It is therefore called once per agent during
``configure()`` -- ``_setup_agent`` -- identically for leader, teammate,
and human agent.

The leader's own DB row only materializes after ``BuildTeamTool`` runs,
so a freshly built leader holds a handle whose row does not exist yet.
That is fine: ``TeamMember`` tolerates a missing row (status reads
return ``None``, writes return ``False`` silently), so there is no need
to defer construction until the row exists.
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
