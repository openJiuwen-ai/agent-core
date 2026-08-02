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

import hashlib
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

_configured_openjiuwen_home: Path | None = None
# Per-task override so team files land under the same project root as plan mode
# (``effective_project_dir`` / thread ``projectPath``). Concurrent sessions with
# different projects stay isolated because ContextVar is task-local.
_agent_teams_home_override: ContextVar[Path | None] = ContextVar(
    "agent_teams_home_override",
    default=None,
)


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
    """Return the root directory for agent-team-owned state.

    Default: ``{openjiuwen_home}/.agent_teams``.
    When :func:`agent_teams_home_scope` is active (plan/project-bound team
    run): ``{project_dir}/.agent_teams`` so team artifacts share the plan root.
    """
    override = _agent_teams_home_override.get()
    if override is not None:
        return override
    return get_openjiuwen_home() / ".agent_teams"


def project_agent_teams_home(project_dir: str | Path) -> Path:
    """Return ``{project_dir}/.agent_teams`` — team home nested under a plan root."""
    return Path(project_dir).expanduser().resolve() / ".agent_teams"


@contextmanager
def agent_teams_home_scope(project_dir: str | Path | None) -> Iterator[Path | None]:
    """Scope :func:`get_agent_teams_home` / :func:`team_home` under *project_dir*.

    Used by ENT team runs that share the plan-mode project root
    (``effective_project_dir``). When *project_dir* is empty/None, yields
    without changing the home (legacy ``{user_data}/.agent_teams`` layout).

    Yields:
        The active agent-teams home path, or ``None`` when unscoped.
    """
    raw = str(project_dir or "").strip()
    if not raw:
        yield None
        return
    home = project_agent_teams_home(raw)
    home.mkdir(parents=True, exist_ok=True)
    token = _agent_teams_home_override.set(home)
    try:
        yield home
    finally:
        _agent_teams_home_override.reset(token)


def __getattr__(name: str) -> Path:
    """Preserve backward-compatible module attributes for path constants."""
    if name == "OPENJIUWEN_HOME":
        return get_openjiuwen_home()
    if name == "AGENT_TEAMS_HOME":
        return get_agent_teams_home()
    raise AttributeError(name)


# Relative mount point of the shared team workspace inside a member cwd.
# The junction/symlink itself is ``{member_cwd}/.team`` → ``team-workspace/``,
# so agents read/write ``.team/artifacts/...`` without embedding ``team_name``.
TEAM_WORKSPACE_MOUNT = ".team"


def team_home(team_name: str) -> Path:
    """Return the per-team root directory.

    Layout (under :func:`get_agent_teams_home`, which may be project-scoped)::

        {agent_teams_home}/{team_name}/
            team-workspace/         # shared artifacts (DEV layout)
            workspaces/             # leader + member DeepAgent workspaces
              {member_name}/
                .team/              # mount → ../team-workspace
            sessions/               # per-session state (swarmflow journals)
              {session_id}/workflows/{workflow_name}/journal.jsonl

    When a plan/project root is active via :func:`agent_teams_home_scope`,
    ``agent_teams_home`` is ``{project_dir}/.agent_teams`` so team files share
    the same root as plan-mode ``files/`` deliverables.

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
    joining and leaving teams. (Standalone keeps the ``_workspace`` suffix;
    in-team member dirs under ``team_home/.../workspaces/`` do not.)

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


def project_worktree_hash(project_dir: str) -> str:
    """Return the stable project hash segment for session-scoped worktrees.

    The path must already exist. Team-managed worktree isolation is anchored to
    an explicit project directory; callers should not silently substitute cwd or
    workspace when this value is missing.
    """
    resolved = os.path.realpath(project_dir)
    if not os.path.isdir(resolved):
        raise ValueError(f"project_dir does not exist: {project_dir}")
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def team_session_worktrees_dir(
    team_name: str,
    session_id: str,
) -> Path:
    """Return the session-owned worktree root for a team.

    Layout:
        ``{team_home}/sessions/{session_id}/worktrees/``
    """
    return team_session_dir(team_name, session_id) / "worktrees"


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
