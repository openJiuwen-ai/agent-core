# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member workspace path rules (design-v5, block C).

Pure functions for where a member's *real* directory lives on disk:

- leader:     ``<team>/workspaces/<member>_workspace/`` (inside the team, no link)
- predefined: ``{openjiuwen_home}/<member>_workspace/``  (shared across teams)
- dynamic:    ``.agent_teams/<team>#<member>/`` (prefix on) or
              ``.agent_teams/<member>/`` (prefix off)

The link inside the team is *always* ``team_member_workspace_dir``
(``workspaces/<member>_workspace``), so A/B code keeps using that path
regardless of the switch. This module owns only the *real* directory
formula; the link path is never forwarded here (v3 R3).
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.agent_teams.paths import (
    get_agent_teams_home,
    team_member_workspace_dir,
)

MEMBER_MODE_LEADER = "leader"
MEMBER_MODE_PREDEFINED = "predefined"
MEMBER_MODE_DYNAMIC = "dynamic"


def member_dir_name(
    team_name: str,
    member_name: str,
    *,
    member_workspace_prefix: bool = True,
) -> str:
    """Return the dynamic real-directory name under ``.agent_teams/``.

    ``member_workspace_prefix=True`` isolates the directory per team
    (``team#member``); ``False`` shares the plain ``member`` shape. Only
    dynamic directories use this formula — leader and predefined real
    directories are computed directly by :func:`member_real_dir`.
    """
    if member_workspace_prefix:
        return f"{team_name}#{member_name}"
    return member_name


def member_real_dir(
    team_name: str,
    member_name: str,
    mode: str,
    *,
    member_workspace_prefix: bool = True,
) -> Path:
    """Return the member's real (team-external or in-team) directory.

    - leader:     ``team_member_workspace_dir`` (in-team, no link)
    - predefined: ``.agent_teams/<member>`` (shared across teams, same level as dynamic)
    - dynamic:    ``.agent_teams/<member_dir_name>``
    """
    if mode == MEMBER_MODE_LEADER:
        return team_member_workspace_dir(team_name, member_name)
    if mode == MEMBER_MODE_PREDEFINED:
        return get_agent_teams_home() / member_name
    return get_agent_teams_home() / member_dir_name(
        team_name,
        member_name,
        member_workspace_prefix=member_workspace_prefix,
    )


__all__ = [
    "MEMBER_MODE_DYNAMIC",
    "MEMBER_MODE_LEADER",
    "MEMBER_MODE_PREDEFINED",
    "member_dir_name",
    "member_real_dir",
]
