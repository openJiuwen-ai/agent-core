# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member workspace path rules (design-v5, block C).

Pure functions for where a member's *real* directory lives on disk:

- leader:     ``<team>/workspaces/<member>_workspace/`` (inside the team, no link)
- predefined: ``{agent_teams_home}/jiuwen_team_members/<member>/``  (shared across teams)
- dynamic:    ``jiuwen_team_members/<team>#<member>/`` (prefix on) or
              ``jiuwen_team_members/<member>/`` (prefix off)

``jiuwen_team_members/`` is a dedicated subdirectory of ``.agent_teams/`` so
member real dirs no longer sit mixed with team dirs at the root (the
``jiuwen_`` prefix keeps the name clear of any realistic team name).
Directories created by the original layout directly at the ``.agent_teams/``
root are still resolved (probe order: current layout, root) — the binder
migrates them into ``jiuwen_team_members/`` on the next spawn (best effort);
probing first means a dir that fails to migrate keeps working in place.

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

MEMBERS_DIR_NAME = "jiuwen_team_members"
"""Name of the dedicated member real-dir subdirectory under ``.agent_teams/``."""


def members_home() -> Path:
    """Return the root directory holding member real dirs.

    Layout: ``{agent_teams_home}/jiuwen_team_members/``
    """
    return get_agent_teams_home() / MEMBERS_DIR_NAME


def member_dir_name(
    team_name: str,
    member_name: str,
    *,
    member_workspace_prefix: bool = True,
) -> str:
    """Return the dynamic real-directory name under ``members/``.

    ``member_workspace_prefix=True`` isolates the directory per team
    (``team#member``); ``False`` shares the plain ``member`` shape. Only
    dynamic directories use this formula — leader and predefined real
    directories are computed directly by :func:`member_real_dir`.
    """
    if member_workspace_prefix:
        return f"{team_name}#{member_name}"
    return member_name


def _probe_member_dir(dir_name: str) -> Path:
    """Return the first existing real dir for ``dir_name``, else the current path.

    Probe order: ``jiuwen_team_members/<dir_name>`` first (the current
    layout), then the ``.agent_teams/`` root (the original layout). When
    neither exists the current-layout path is returned — that is where a
    new directory will be created.
    """
    members_dir = members_home() / dir_name
    if members_dir.is_dir():
        return members_dir
    legacy = get_agent_teams_home() / dir_name
    if legacy.is_dir():
        return legacy
    return members_dir


def member_real_dir(
    team_name: str,
    member_name: str,
    mode: str,
    *,
    member_workspace_prefix: bool = True,
) -> Path:
    """Return the member's real (team-external or in-team) directory.

    - leader:     ``team_member_workspace_dir`` (in-team, no link)
    - predefined: ``jiuwen_team_members/<member>`` (shared across teams, same level as dynamic)
    - dynamic:    ``jiuwen_team_members/<member_dir_name>``

    For predefined/dynamic the result probes the current layout first, then
    older layouts (``members/``, root), so a directory left behind by an
    earlier version is resolved in place (the binder migrates it on the next
    spawn).
    """
    if mode == MEMBER_MODE_LEADER:
        return team_member_workspace_dir(team_name, member_name)
    if mode == MEMBER_MODE_PREDEFINED:
        return _probe_member_dir(member_name)
    return _probe_member_dir(
        member_dir_name(
            team_name,
            member_name,
            member_workspace_prefix=member_workspace_prefix,
        )
    )


__all__ = [
    "MEMBERS_DIR_NAME",
    "MEMBER_MODE_DYNAMIC",
    "MEMBER_MODE_LEADER",
    "MEMBER_MODE_PREDEFINED",
    "member_dir_name",
    "member_real_dir",
    "members_home",
]
