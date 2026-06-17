# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-hosted lifecycle management for teammate worktrees."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.member_options import get_member_worktree
from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.harness.tools.worktree import WorktreeManager


class MemberWorktreeInfo(BaseModel):
    """Leader-host metadata for one teammate worktree."""

    worktree_path: str
    worktree_name: str
    worktree_branch: str | None = None
    head_commit: str | None = None
    hook_based: bool = False


class TeammateWorktreeLifecycle:
    """Create and finalize leader-owned worktrees for teammates."""

    def __init__(self, configurator: "AgentConfigurator") -> None:
        self._configurator = configurator
        self._member_worktree_info: dict[str, MemberWorktreeInfo] = {}

    @staticmethod
    def _member_needs_worktree(teammate: Any, role: TeamRole) -> bool:
        """Return whether the DB member should be spawned in a worktree."""
        if role != TeamRole.TEAMMATE:
            return False
        worktree = get_member_worktree(teammate)
        return worktree is not None and worktree.isolation == "worktree"

    def _get_worktree_manager(self) -> "WorktreeManager":
        """Return a manager for leader-owned teammate worktrees."""
        manager = self._configurator.worktree_manager
        if manager is not None:
            return manager

        spec = self._configurator.spec
        if spec is not None and spec.worktree is not None:
            manager = self._configurator.create_worktree_manager(spec)
        else:
            from openjiuwen.harness.tools.worktree import WorktreeConfig, WorktreeManager

            manager = WorktreeManager(WorktreeConfig(enabled=True))
        self._configurator.worktree_manager = manager
        return manager

    async def ensure_member_worktree(
        self,
        teammate: Any,
        role: TeamRole,
    ) -> str | None:
        """Create or reuse the leader-owned worktree for a teammate."""
        if not self._member_needs_worktree(teammate, role):
            return None

        worktree = get_member_worktree(teammate)
        worktree_path = worktree.path if worktree is not None else None
        if worktree_path:
            return worktree_path

        team_backend = self._configurator.team_backend
        if team_backend is None:
            return worktree_path

        team_name = team_backend.team_name
        slug = build_teammate_worktree_name(
            team_name=team_name,
            member_name=teammate.member_name,
        )
        manager = self._get_worktree_manager()
        result = await manager.create_owner_worktree(slug)
        await team_backend.db.member.update_member_worktree(
            teammate.member_name,
            team_name,
            isolation="worktree",
            worktree_path=result.worktree_path,
        )
        self._member_worktree_info[teammate.member_name] = MemberWorktreeInfo(
            worktree_path=result.worktree_path,
            worktree_name=slug,
            worktree_branch=result.worktree_branch,
            head_commit=result.head_commit,
            hook_based=bool(getattr(result, "hook_based", False)),
        )
        team_logger.info(
            "Created worktree for teammate {}: {} ({})",
            teammate.member_name,
            result.worktree_path,
            result.worktree_branch,
        )
        return result.worktree_path

    async def finalize_member_worktree(self, member_name: str) -> None:
        """Remove a clean teammate worktree, preserve one with changes."""
        team_backend = getattr(self._configurator, "team_backend", None)
        team_name = getattr(self._configurator, "team_name", None) or (
            team_backend.team_name if team_backend else None
        )
        if team_backend is None or team_name is None:
            return

        teammate = await team_backend.db.member.get_member(member_name, team_name)
        if teammate is None:
            return
        worktree = get_member_worktree(teammate)
        if worktree is None or worktree.isolation != "worktree":
            return

        worktree_path = worktree.path
        if not worktree_path:
            return

        if not os.path.isdir(worktree_path):
            await team_backend.db.member.update_member_worktree(
                member_name,
                team_name,
                isolation="worktree",
                worktree_path=None,
            )
            self._member_worktree_info.pop(member_name, None)
            team_logger.info("Cleared missing worktree metadata for teammate {}", member_name)
            return

        worktree_info = self._member_worktree_info.get(member_name)
        if worktree_info is None:
            team_logger.warning(
                "Keeping worktree for teammate {} because host worktree metadata is not available: {}",
                member_name,
                worktree_path,
            )
            return
        if worktree_info.hook_based:
            team_logger.info(
                "Keeping hook-based worktree for teammate {}: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        from openjiuwen.harness.tools.worktree.git import find_canonical_git_root
        from openjiuwen.harness.tools.worktree.models import WorktreeSession

        manager = self._get_worktree_manager()
        repo_root = await find_canonical_git_root(worktree_info.worktree_path)
        session = WorktreeSession(
            original_cwd=repo_root or worktree_info.worktree_path,
            worktree_path=worktree_info.worktree_path,
            worktree_name=worktree_info.worktree_name,
            worktree_branch=worktree_info.worktree_branch,
            original_head_commit=worktree_info.head_commit,
            member_name=member_name,
            team_name=team_name,
        )
        summary = await manager.count_changes(session)
        if summary is None:
            team_logger.warning(
                "Keeping worktree for teammate {} because changes could not be verified: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        if summary.changed_files > 0 or summary.commits > 0:
            team_logger.info(
                "Keeping worktree for teammate {} with {} changed files and {} commits: {} ({})",
                member_name,
                summary.changed_files,
                summary.commits,
                worktree_info.worktree_path,
                worktree_info.worktree_branch,
            )
            return

        if repo_root is None:
            team_logger.warning(
                "Keeping clean worktree for teammate {} because repo root could not be resolved: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        removed = await manager.remove_worktree(worktree_info.worktree_path, repo_root)
        if removed:
            await team_backend.db.member.update_member_worktree(
                member_name,
                team_name,
                isolation="worktree",
                worktree_path=None,
            )
            self._member_worktree_info.pop(member_name, None)
            team_logger.info(
                "Removed clean worktree for teammate {}: {}",
                member_name,
                worktree_info.worktree_path,
            )
