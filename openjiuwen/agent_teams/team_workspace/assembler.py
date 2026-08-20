# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member workspace assembly (design-v5, block C).

Combines the one-time legacy-layout migration with the member-directory
binder: classify the member (leader / predefined / dynamic), migrate any
legacy in-team real directory, then bind. Returns the in-team root
(``team_member_workspace_dir``) so callers never resolve the link themselves.
"""

from __future__ import annotations

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_workspace.binder import (
    MemberWorkspaceBinder,
    TeamMemberBinding,
)
from openjiuwen.agent_teams.team_workspace.migrator import TeamWorkspaceMigrator
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
)


def prepare_member_workspace(
    *,
    team_name: str,
    member_name: str,
    role: TeamRole,
    leader_member_name: str | None,
    predefined_members: set[str],
    member_workspace_prefix: bool = True,
) -> str:
    """Ensure the member workspace exists; return the in-team root path.

    Classification: a leader (by role or name) keeps its real directory
    in-team; a predefined member shares the independent workspace across
    teams; everything else is a dynamic member (``.agent_teams/<team>#<m>/``).
    The legacy in-team layout is migrated first (idempotent), then the binder
    creates the real directory + link. The returned path is always
    ``team_member_workspace_dir`` — when the link exists it is transparent;
    when link creation fails the real directory retreats into the team tree
    (v3 R2). A/B code never notices the link.
    """
    if role == TeamRole.LEADER or member_name == leader_member_name:
        mode = MEMBER_MODE_LEADER
    elif member_name in predefined_members:
        mode = MEMBER_MODE_PREDEFINED
    else:
        mode = MEMBER_MODE_DYNAMIC

    TeamWorkspaceMigrator().migrate(
        team_name,
        leader_member_name=leader_member_name,
        predefined_members=predefined_members,
        member_workspace_prefix=member_workspace_prefix,
    )

    root = MemberWorkspaceBinder().setup(
        TeamMemberBinding(
            team_name=team_name,
            member_name=member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )
    )
    return str(root)


__all__ = ["prepare_member_workspace"]
