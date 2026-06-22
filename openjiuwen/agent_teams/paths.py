# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared filesystem path helpers for agent teams.

Single source of truth for the on-disk layout used by team workspaces,
member workspaces, swarmflow run journals, and the default sqlite db.
Centralizing it here keeps creation (``team_agent.py``, ``blueprint.py``)
and cleanup (``TeamBackend.clean_team``) in sync: a future move of the
root only needs to update this module.
"""

from __future__ import annotations

import re
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
            sessions/               # per-session state (swarmflow journals)
              {session_id}/workflows/{workflow_name}/journal.jsonl
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


def _safe_segment(value: str, fallback: str = "_") -> str:
    """Sanitize an untrusted string into a single safe path segment.

    Replaces every character outside ``[A-Za-z0-9_.-]`` with ``_`` and
    strips leading/trailing separators so the result can never escape its
    parent directory (no ``/``, no ``..``). Used for path components that
    come from untrusted input (a script's ``META`` name, a session id).

    Args:
        value: Raw segment value.
        fallback: Returned when sanitizing yields an empty string.

    Returns:
        A filesystem-safe path segment.
    """
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    normalized = normalized.strip("._-")
    return normalized[:96] or fallback


def team_sessions_dir(team_name: str) -> Path:
    """Return the directory holding all per-session state for a team.

    Layout: ``{team_home}/sessions/``
    """
    return team_home(team_name) / "sessions"


def team_session_dir(team_name: str, session_id: str) -> Path:
    """Return the per-session directory under a team.

    Layout: ``{team_home}/sessions/{session_id}/``

    Args:
        team_name: Team identifier.
        session_id: Session identifier (sanitized into one path segment).
    """
    return team_sessions_dir(team_name) / _safe_segment(session_id)


def workflow_run_dir(team_name: str, session_id: str, workflow_name: str) -> Path:
    """Return the per-workflow directory under a team session.

    Layout: ``{team_home}/sessions/{session_id}/workflows/{workflow_name}/``

    Args:
        team_name: Team identifier.
        session_id: Session identifier (sanitized into one path segment).
        workflow_name: Workflow name from the script ``META`` (sanitized).
    """
    return team_session_dir(team_name, session_id) / "workflows" / _safe_segment(workflow_name)


def workflow_journal_path(team_name: str, session_id: str, workflow_name: str) -> Path:
    """Return the resume-journal file path for a swarmflow run.

    Layout:
        ``{team_home}/sessions/{session_id}/workflows/{workflow_name}/journal.jsonl``

    Args:
        team_name: Team identifier.
        session_id: Session identifier.
        workflow_name: Workflow name from the script ``META``.
    """
    return workflow_run_dir(team_name, session_id, workflow_name) / "journal.jsonl"


def async_tool_output_dir(team_name: str, session_id: str) -> Path:
    """Return the directory holding async-tool spilled outputs for a session.

    Layout: ``{team_home}/sessions/{session_id}/async_tools/``

    Oversized async-tool results spill here as ``{task_id}.output`` files so a
    large report does not blow the leader's context. The directory is removed
    on team cleanup via ``TeamBackend.register_cleanup_path``.

    Args:
        team_name: Team identifier.
        session_id: Session identifier (sanitized into one path segment).
    """
    return team_session_dir(team_name, session_id) / "async_tools"
