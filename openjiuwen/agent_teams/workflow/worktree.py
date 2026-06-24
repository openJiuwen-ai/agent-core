# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow worker worktree lifecycle helpers."""

from __future__ import annotations

import os
from typing import Any

from openjiuwen.agent_teams.worktree.lifecycle import MemberWorktreeInfo
from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.tools.worktree.models import WorktreeSession


class SwarmflowWorkerWorktrees:
    """Create and finalize owner-scoped worktrees for swarmflow workers."""

    def __init__(self, *, team_name: str, build_context: Any = None) -> None:
        self._team_name = team_name
        self._build_context = build_context
        self._active: dict[str, MemberWorktreeInfo] = {}

    def get(self, member_name: str) -> MemberWorktreeInfo | None:
        """Return the active worker worktree metadata, if any."""
        return self._active.get(member_name)

    @staticmethod
    def needs_worktree(opts: dict) -> bool:
        """Return whether a swarmflow agent call requested worktree isolation."""
        return opts.get("isolation") == "worktree"

    def _manager(self) -> Any:
        from openjiuwen.agent_teams.rails.team_context import get_worktree_manager

        manager = get_worktree_manager(self._build_context)
        if manager is None:
            raise BackendError(
                "agent(options={'isolation': 'worktree'}) requires a host-provided worktree manager"
            )
        return manager

    async def ensure(self, member_name: str, opts: dict) -> MemberWorktreeInfo | None:
        """Create an owner-scoped worktree for ``agent(options={"isolation": "worktree"})``."""
        if not self.needs_worktree(opts):
            return None
        slug = build_teammate_worktree_name(
            team_name=self._team_name,
            member_name=member_name,
        )
        manager = self._manager()
        result = await manager.create_owner_worktree(slug)
        info = MemberWorktreeInfo(
            worktree_path=result.worktree_path,
            worktree_name=slug,
            worktree_branch=result.worktree_branch,
            head_commit=result.head_commit,
            hook_based=bool(getattr(result, "hook_based", False)),
        )
        self._active[member_name] = info
        team_logger.info(
            "Created worktree for swarmflow worker {}: {} ({})",
            member_name,
            result.worktree_path,
            result.worktree_branch,
        )
        return info

    async def finalize(self, member_name: str) -> None:
        """Remove a clean worker worktree, preserving changed or unverifiable ones."""
        worktree_info = self._active.pop(member_name, None)
        if worktree_info is None:
            return
        if not os.path.isdir(worktree_info.worktree_path):
            team_logger.info(
                "Skipping swarmflow worker worktree cleanup because path is missing for {}: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return
        if worktree_info.hook_based:
            team_logger.info(
                "Keeping hook-based swarmflow worker worktree for {}: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        from openjiuwen.harness.tools.worktree.git import find_canonical_git_root

        manager = self._manager()
        repo_root = await find_canonical_git_root(worktree_info.worktree_path)
        session = WorktreeSession(
            original_cwd=repo_root or worktree_info.worktree_path,
            worktree_path=worktree_info.worktree_path,
            worktree_name=worktree_info.worktree_name,
            worktree_branch=worktree_info.worktree_branch,
            original_head_commit=worktree_info.head_commit,
            member_name=member_name,
            team_name=self._team_name,
        )
        summary = await manager.count_changes(session)
        if summary is None:
            team_logger.warning(
                "Keeping swarmflow worker worktree for {} because changes could not be verified: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return
        if summary.changed_files > 0 or summary.commits > 0:
            team_logger.info(
                "Keeping swarmflow worker worktree for {} with {} changed files and {} commits: {} ({})",
                member_name,
                summary.changed_files,
                summary.commits,
                worktree_info.worktree_path,
                worktree_info.worktree_branch,
            )
            return
        if repo_root is None:
            team_logger.warning(
                "Keeping clean swarmflow worker worktree for {} because repo root could not be resolved: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return
        removed = await manager.remove_worktree(worktree_info.worktree_path, repo_root)
        if removed:
            team_logger.info(
                "Removed clean swarmflow worker worktree for {}: {}",
                member_name,
                worktree_info.worktree_path,
            )


__all__ = ["SwarmflowWorkerWorktrees"]
