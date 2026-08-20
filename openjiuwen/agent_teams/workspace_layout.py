# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace layout helpers shared by team members and swarmflow workers."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from openjiuwen.agent_teams.paths import independent_member_workspace, team_member_workspace_dir


def team_member_workspace_path(team_name: str, member_name: str) -> Path:
    """Return the stable workspace path for one team member.

    The member workspace holds the member's artifacts, memory, the ``.team``
    mount and its Skill visibility declaration. It never holds a Skill library
    of its own: Skills live in exactly one physical directory
    (``openjiuwen.agent_teams.paths.global_skills_dir``).
    """
    return team_member_workspace_dir(team_name, member_name)


def ensure_team_member_workspace_link(team_name: str, member_name: str) -> str:
    """Ensure an existing independent member workspace is visible under the team.

    Standalone DeepAgent workspaces live outside the team tree. When such a
    workspace already exists, expose it at the stable team workspace path via a
    symlink; otherwise return the stable path for the normal workspace setup to
    create/use later.

    Backward-incompatible change: a runtime that forbids symlink creation no
    longer gets a ``copytree`` of the standalone workspace into the team tree.
    That fallback duplicated the whole workspace -- the agent's Skill directory
    included -- and the duplicate then drifted from the original. The member now
    simply keeps running in its own independent workspace, which this function
    returns unchanged.

    Args:
        team_name: Team identifier.
        member_name: Member identifier.

    Returns:
        Absolute path of the workspace directory the member should run in.
    """
    workspace_path = team_member_workspace_path(team_name, member_name)
    independent_workspace = independent_member_workspace(member_name)
    if independent_workspace.is_dir() and not workspace_path.exists():
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(
                str(independent_workspace),
                str(workspace_path),
                target_is_directory=True,
            )
        except OSError as exc:
            if getattr(exc, "errno", None) not in (errno.EACCES, errno.EPERM):
                raise
            return str(independent_workspace)
    return str(workspace_path)


__all__ = [
    "ensure_team_member_workspace_link",
    "team_member_workspace_path",
]
