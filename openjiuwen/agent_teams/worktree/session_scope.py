# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strict session-scoped ownership for team-managed worktrees."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.paths import (
    project_worktree_hash,
    team_session_worktrees_dir,
)
from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name

_ALLOWED_MODE_NAMESPACES = frozenset({"team", "code.team", "team.plan"})


@dataclass(frozen=True)
class WorktreeOwnerScope:
    """Resolved owner identity for one managed team worktree."""

    team_name: str
    member_name: str
    session_id: str
    mode_namespace: str
    project_dir: str
    project_hash: str
    managed_root: str
    worktree_name: str


def _context_attr(context: Any, attr: str) -> Any:
    return getattr(context, attr, None) if context is not None else None


def _seed_attr(spec: Any, attr: str) -> Any:
    seed = getattr(spec, "build_context_seed", None) if spec is not None else None
    if isinstance(seed, dict):
        return seed.get(attr)
    return None


def _resolve_mode_namespace(*, spec: Any = None, build_context: Any = None) -> str:
    context = build_context if build_context is not None else getattr(spec, "build_context", None)
    mode = _context_attr(context, "mode")
    if mode is None:
        mode = _seed_attr(spec, "mode")
    if not isinstance(mode, str) or mode not in _ALLOWED_MODE_NAMESPACES:
        raise RuntimeError(
            "Team worktree isolation requires build_context.mode to be one of "
            f"{sorted(_ALLOWED_MODE_NAMESPACES)}; got {mode!r}"
        )
    if spec is not None and getattr(spec, "enable_team_plan", False) and mode != "team.plan":
        raise RuntimeError("team.plan worktree isolation requires build_context.mode='team.plan'")
    return mode


def _resolve_project_dir(*, spec: Any = None, build_context: Any = None) -> str:
    context = build_context if build_context is not None else getattr(spec, "build_context", None)
    project_dir = _context_attr(context, "project_dir")
    if project_dir is None:
        project_dir = _seed_attr(spec, "project_dir")
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise RuntimeError("Team worktree isolation requires explicit build_context.project_dir")
    resolved = os.path.realpath(project_dir)
    if not os.path.isdir(resolved):
        raise RuntimeError(f"Team worktree isolation project_dir does not exist: {project_dir}")
    return resolved


def build_worktree_owner_scope(
    *,
    team_name: str,
    member_name: str,
    spec: Any = None,
    build_context: Any = None,
) -> WorktreeOwnerScope:
    """Resolve the strict session/mode/project owner scope for one worktree."""
    session_id = get_session_id()
    if not session_id:
        raise RuntimeError("Team worktree isolation requires an active team session_id")
    mode_namespace = _resolve_mode_namespace(spec=spec, build_context=build_context)
    project_dir = _resolve_project_dir(spec=spec, build_context=build_context)
    project_hash = project_worktree_hash(project_dir)
    managed_root = str(
        team_session_worktrees_dir(
            team_name=team_name,
            session_id=session_id,
        )
    )
    worktree_name = build_teammate_worktree_name(
        team_name=team_name,
        member_name=member_name,
        session_id=session_id,
        mode_namespace=mode_namespace,
        project_hash=project_hash,
    )
    return WorktreeOwnerScope(
        team_name=team_name,
        member_name=member_name,
        session_id=session_id,
        mode_namespace=mode_namespace,
        project_dir=project_dir,
        project_hash=project_hash,
        managed_root=managed_root,
        worktree_name=worktree_name,
    )


def manager_for_worktree_scope(manager: Any, scope: WorktreeOwnerScope) -> Any:
    """Return a worktree manager pinned to ``scope.managed_root``."""
    if hasattr(manager, "with_worktrees_dir"):
        return manager.with_worktrees_dir(scope.managed_root)
    if not hasattr(manager, "with_base_dir"):
        raise RuntimeError("Team worktree isolation requires WorktreeManager.with_base_dir")
    return manager.with_base_dir(scope.managed_root)


def enter_project_worktree_context(
    project_dir: str,
    worktree_root: str,
) -> tuple[str, str, str, str | None, str | None]:
    """Temporarily anchor worktree git commands to the explicit project root."""
    from openjiuwen.core.sys_operation.cwd import (
        get_cwd,
        get_original_cwd,
        get_project_root,
        get_team_workspace,
        get_workspace,
        init_cwd,
    )

    snapshot = (
        get_cwd(),
        get_original_cwd(),
        get_project_root(),
        get_workspace(),
        get_team_workspace(),
    )
    init_cwd(project_dir, project_root=project_dir, workspace=worktree_root, team_workspace=snapshot[4])
    return snapshot


def restore_project_worktree_context(
    snapshot: tuple[str, str, str, str | None, str | None],
) -> None:
    """Restore cwd state after a session-scoped worktree operation."""
    from openjiuwen.core.sys_operation.cwd import init_cwd, set_cwd, set_original_cwd

    cwd, original_cwd, project_root, workspace, team_workspace = snapshot
    init_cwd(original_cwd, project_root=project_root, workspace=workspace, team_workspace=team_workspace)
    set_cwd(cwd)
    set_original_cwd(original_cwd)


__all__ = [
    "WorktreeOwnerScope",
    "build_worktree_owner_scope",
    "enter_project_worktree_context",
    "manager_for_worktree_scope",
    "restore_project_worktree_context",
]
