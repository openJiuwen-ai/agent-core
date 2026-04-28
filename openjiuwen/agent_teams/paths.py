# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared filesystem path helpers for agent teams.

Single source of truth for the on-disk layout used by team workspaces,
member workspaces, and the default sqlite db. Centralizing it here
keeps creation (``team_agent.py``, ``blueprint.py``) and cleanup
(``TeamBackend.clean_team``) in sync: a future move of the root only
needs to update this module.
"""

from __future__ import annotations

from pathlib import Path

_configured_openjiuwen_home: Path | None = None


def configure_openjiuwen_home(path: str | Path) -> None:
    """Override the runtime home directory used by agent teams."""
    global _configured_openjiuwen_home
    _configured_openjiuwen_home = Path(path)


def reset_openjiuwen_home() -> None:
    """Clear the runtime home override and restore the default layout."""
    global _configured_openjiuwen_home
    _configured_openjiuwen_home = None


def get_openjiuwen_home() -> Path:
    """Return the root directory for openJiuWen local state."""
    if _configured_openjiuwen_home is not None:
        return _configured_openjiuwen_home
    return Path.home() / ".openjiuwen"


def get_agent_teams_home() -> Path:
    """Return the root directory for agent-team-owned state."""
    return get_openjiuwen_home() / ".agent_teams"


def __getattr__(name: str) -> Path:
    """Preserve backward-compatible module attributes for path constants."""
    if name == "OPENJIUWEN_HOME":
        return get_openjiuwen_home()
    if name == "AGENT_TEAMS_HOME":
        return get_agent_teams_home()
    raise AttributeError(name)


def team_home(team_name: str) -> Path:
    """Return the per-team root directory.

    Layout:
        ``{get_agent_teams_home()}/{team_name}/``
            team-workspace/         # default team shared workspace
            workspaces/             # stable_base member workspaces
              {member}_workspace/
            team.db                 # default sqlite db

    Args:
        team_name: Team identifier.

    Returns:
        Absolute path to the team-named parent directory.
    """
    return get_agent_teams_home() / team_name


def independent_member_workspace(member_name: str) -> Path:
    """Return the path of a standalone DeepAgent workspace.

    Predefined independent DeepAgents keep their workspace at
    ``{get_openjiuwen_home()}/{member_name}_workspace/`` so it survives
    joining and leaving teams.

    Args:
        member_name: Member identifier.

    Returns:
        Absolute path to the independent workspace directory.
    """
    return get_openjiuwen_home() / f"{member_name}_workspace"


def team_memory_dir(team_name: str) -> Path:
    """Return the per-team shared memory directory.

    Layout: ``{AGENT_TEAMS_HOME}/{team_name}/team-memory/``
    """
    return team_home(team_name) / "team-workspace" / "team-memory"
