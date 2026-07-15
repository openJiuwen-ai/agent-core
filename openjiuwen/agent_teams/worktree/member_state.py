# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB/options state helpers for team-managed teammate worktrees."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.member_options import (
    MemberWorktreeOptions,
    get_member_worktree,
)
from openjiuwen.agent_teams.worktree.session_scope import WorktreeOwnerScope
from openjiuwen.core.common.logging import team_logger


class MemberWorktreeInfo(BaseModel):
    """Leader-host metadata for one teammate worktree."""

    worktree_path: str
    worktree_name: str
    worktree_branch: str | None = None
    head_commit: str | None = None
    hook_based: bool = False
    session_id: str | None = None
    project_hash: str | None = None
    managed_root: str | None = None


@dataclass(slots=True)
class ResolvedMemberWorktree:
    """Resolved DB/cache metadata for a teammate worktree owned by this session."""

    team_backend: Any
    team_name: str
    scope: WorktreeOwnerScope
    db_matches_scope: bool
    worktree_info: MemberWorktreeInfo


def team_context(configurator: Any) -> tuple[Any, str] | None:
    """Return the team backend/name pair from an agent configurator."""
    team_backend = getattr(configurator, "team_backend", None)
    team_name = getattr(configurator, "team_name", None)
    if team_name is None and team_backend is not None:
        team_name = team_backend.team_name
    if team_backend is None or team_name is None:
        return None
    return team_backend, team_name


async def teammate_member_names(team_backend: Any, team_name: str) -> list[str]:
    """Return teammate member names for a team."""
    members = await team_backend.db.member.get_team_members(team_name)
    return [
        member.member_name
        for member in members
        if getattr(member, "role", None) == TeamRole.TEAMMATE.value
    ]


def matches_scope(
    worktree: MemberWorktreeOptions,
    scope: WorktreeOwnerScope,
) -> bool:
    """Return whether DB worktree metadata belongs to the owner scope."""
    return (
        worktree.session_id == scope.session_id
        and worktree.project_hash == scope.project_hash
        and worktree.managed_root == scope.managed_root
    )


def info_matches_scope(
    worktree_info: MemberWorktreeInfo,
    scope: WorktreeOwnerScope,
) -> bool:
    """Return whether cached host metadata belongs to the owner scope."""
    return (
        worktree_info.session_id == scope.session_id
        and worktree_info.project_hash == scope.project_hash
        and worktree_info.managed_root == scope.managed_root
        and bool(worktree_info.worktree_path)
    )


def info_from_options(
    worktree: MemberWorktreeOptions,
    *,
    member_name: str,
    scope: WorktreeOwnerScope,
) -> MemberWorktreeInfo | None:
    """Build host worktree metadata from persisted DB member options."""
    if not (
        worktree.path
        and worktree.session_id
        and worktree.project_hash
        and worktree.managed_root
    ):
        team_logger.warning(
            "Keeping worktree for teammate {} because worktree ownership metadata is incomplete: {}",
            member_name,
            worktree.path,
        )
        return None
    if not matches_scope(worktree, scope):
        return None
    return MemberWorktreeInfo(
        worktree_path=worktree.path,
        worktree_name=scope.worktree_name,
        worktree_branch=worktree.worktree_branch,
        head_commit=worktree.head_commit,
        session_id=worktree.session_id,
        project_hash=worktree.project_hash,
        managed_root=worktree.managed_root,
    )


def _should_log_foreign_worktree(
    worktree: MemberWorktreeOptions | None,
    *,
    db_matches_scope: bool,
    log_foreign: bool,
) -> bool:
    """Return whether foreign worktree metadata should be logged."""
    if not log_foreign or worktree is None or db_matches_scope:
        return False
    return worktree.isolation == "worktree" and bool(worktree.path)


async def resolve_current_session_member_worktree(
    configurator: Any,
    member_worktree_info: MutableMapping[str, MemberWorktreeInfo],
    member_name: str,
    owner_scope: Callable[[str, str], WorktreeOwnerScope],
    *,
    log_foreign: bool = False,
) -> ResolvedMemberWorktree | None:
    """Resolve a current-session teammate worktree from DB options and host cache."""
    context = team_context(configurator)
    if context is None:
        return None
    team_backend, team_name = context

    teammate = await team_backend.db.member.get_member(member_name, team_name)
    if teammate is None:
        return None
    worktree = get_member_worktree(teammate)
    worktree_info = member_worktree_info.get(member_name)
    has_db_worktree = (
        worktree is not None
        and worktree.isolation == "worktree"
        and bool(worktree.path)
    )
    if worktree_info is None and not has_db_worktree:
        return None

    scope = owner_scope(team_name, member_name)

    db_matches_scope = worktree is not None and has_db_worktree and matches_scope(worktree, scope)
    if worktree_info is not None and not info_matches_scope(worktree_info, scope):
        worktree_info = None

    if _should_log_foreign_worktree(
        worktree,
        db_matches_scope=db_matches_scope,
        log_foreign=log_foreign,
    ):
        team_logger.info(
            "Keeping worktree for teammate {} because it is not owned by the current session: {}",
            member_name,
            worktree.path,
        )

    if worktree_info is None:
        if not db_matches_scope or worktree is None:
            return None
        worktree_info = info_from_options(worktree, member_name=member_name, scope=scope)
        if worktree_info is None:
            return None

    return ResolvedMemberWorktree(
        team_backend=team_backend,
        team_name=team_name,
        scope=scope,
        db_matches_scope=db_matches_scope,
        worktree_info=worktree_info,
    )


async def clear_member_worktree_metadata(
    member_name: str,
    resolved: ResolvedMemberWorktree,
    member_worktree_info: MutableMapping[str, MemberWorktreeInfo],
) -> None:
    """Clear current-session worktree metadata from DB and host cache."""
    if resolved.db_matches_scope:
        await resolved.team_backend.db.member.update_member_worktree(
            member_name,
            resolved.team_name,
            MemberWorktreeOptions(isolation="worktree"),
        )
    member_worktree_info.pop(member_name, None)


__all__ = [
    "MemberWorktreeInfo",
    "ResolvedMemberWorktree",
    "clear_member_worktree_metadata",
    "info_from_options",
    "matches_scope",
    "resolve_current_session_member_worktree",
    "team_context",
    "teammate_member_names",
]
