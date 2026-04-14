# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared filesystem path constants for agent teams.

Single source of truth for the on-disk layout used by team workspaces,
member workspaces, and the default sqlite db.  Centralizing it here
keeps creation (``team_agent.py``, ``blueprint.py``) and cleanup
(``TeamBackend.clean_team``) in sync: a future move of the root only
needs to update this module.
"""

from pathlib import Path


OPENJIUWEN_HOME: Path = Path.home() / ".openjiuwen"
"""Root for all openJiuWen local state under the user's home directory."""

AGENT_TEAMS_HOME: Path = OPENJIUWEN_HOME / ".agent_teams"
"""Root for all agent-team-owned state (per-team subdirectories)."""


def team_home(team_name: str) -> Path:
    """Return the per-team root directory.

    Layout:
        ``{AGENT_TEAMS_HOME}/{team_name}/``
            ├── team-workspace/         # default team shared workspace
            ├── workspaces/             # stable_base member workspaces
            │   └── {member}_workspace/
            └── team.db                 # default sqlite db

    Args:
        team_name: Team identifier.

    Returns:
        Absolute path to the team-named parent directory.
    """
    return AGENT_TEAMS_HOME / team_name


def independent_member_workspace(member_name: str) -> Path:
    """Return the path of a standalone DeepAgent workspace.

    Predefined independent DeepAgents keep their workspace at
    ``{OPENJIUWEN_HOME}/{member_name}_workspace/`` so it survives joining
    and leaving teams.

    Args:
        member_name: Member identifier.

    Returns:
        Absolute path to the independent workspace directory.
    """
    return OPENJIUWEN_HOME / f"{member_name}_workspace"
