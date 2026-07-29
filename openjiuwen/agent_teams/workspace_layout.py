# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace layout helpers shared by team members and swarmflow workers."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from openjiuwen.agent_teams.paths import independent_member_workspace, team_home


def team_member_workspace_path(team_name: str, member_name: str) -> Path:
    """Return the stable workspace path for one team member."""
    return team_home(team_name) / "workspaces" / f"{member_name}_workspace"


def ensure_team_member_workspace_link(team_name: str, member_name: str) -> str:
    """Ensure an existing independent member workspace is visible under the team.

    Standalone DeepAgent workspaces live outside the team tree. When such a
    workspace already exists, expose it at the stable team workspace path via a
    symlink; otherwise return the stable path for the normal workspace setup to
    create/use later.
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
            shutil.copytree(
                str(independent_workspace),
                str(workspace_path),
                symlinks=False,
                copy_function=shutil.copy2,
                dirs_exist_ok=False,
            )
    return str(workspace_path)


__all__ = [
    "ensure_team_member_workspace_link",
    "team_member_workspace_path",
]
