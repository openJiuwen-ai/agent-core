# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-hosted lifecycle management for teammate worktrees."""

from __future__ import annotations

import os
import shutil
from enum import Enum
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.member_options import (
    MemberWorktreeOptions,
    get_member_worktree,
)
from openjiuwen.agent_teams.worktree.member_state import (
    MemberWorktreeInfo,
    clear_member_worktree_metadata,
    info_from_options,
    matches_scope,
    resolve_current_session_member_worktree,
    team_context,
    teammate_member_names,
)
from openjiuwen.agent_teams.worktree.session_scope import (
    WorktreeOwnerScope,
    build_worktree_owner_scope,
    enter_project_worktree_context,
    manager_for_worktree_scope,
    restore_project_worktree_context,
)
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.harness.tools.worktree import WorktreeManager

_TEAM_RUNTIME_FILES = frozenset(
    {
        ".DS_Store",
        "AGENT.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "USER.md",
    }
)

_TEAM_RUNTIME_DIRS = frozenset(
    {
        ".agent_history",
        ".team",
        "agents",
        "coding_memory",
        "context",
        "memory",
        "messages",
        "skills",
        "todo",
    }
)


class WorktreeContributionState(str, Enum):
    """Contribution classification for a teammate worktree branch."""

    CONTRIBUTED = "contributed"
    NOT_CONTRIBUTED = "not_contributed"
    UNKNOWN = "unknown"


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

    def _owner_scope(self, team_name: str, member_name: str) -> WorktreeOwnerScope:
        return build_worktree_owner_scope(
            team_name=team_name,
            member_name=member_name,
            spec=self._configurator.spec,
        )

    @staticmethod
    def _status_path(line: str) -> str:
        """Extract the path part from one ``git status --porcelain`` line."""
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        return path.strip('"').rstrip("/")

    @staticmethod
    def _is_team_runtime_status(line: str) -> bool:
        path = TeammateWorktreeLifecycle._status_path(line)
        if not path:
            return False
        if path in _TEAM_RUNTIME_FILES:
            return True
        root = path.split("/", 1)[0]
        return root in _TEAM_RUNTIME_DIRS

    @classmethod
    async def _count_user_changed_files(cls, worktree_path: str) -> int:
        from openjiuwen.harness.tools.worktree import git as worktree_git

        changes = await worktree_git.status_porcelain(worktree_path)
        return sum(1 for line in changes if not cls._is_team_runtime_status(line))

    @staticmethod
    async def _classify_worktree_contribution(
        worktree_info: MemberWorktreeInfo,
        repo_root: str,
    ) -> WorktreeContributionState:
        from openjiuwen.harness.tools.worktree.git import is_ref_ancestor, merge_base, rev_parse

        if worktree_info.head_commit:
            base_head = await rev_parse(worktree_info.head_commit, repo_root)
            if base_head:
                actual_head = await rev_parse("HEAD", worktree_info.worktree_path)
                if actual_head:
                    if actual_head != base_head:
                        is_actual_descendant = await is_ref_ancestor(
                            worktree_info.head_commit,
                            "HEAD",
                            worktree_info.worktree_path,
                        )
                        if is_actual_descendant is True:
                            return WorktreeContributionState.CONTRIBUTED
                        if is_actual_descendant is None:
                            return WorktreeContributionState.UNKNOWN
                    elif not worktree_info.worktree_branch:
                        return WorktreeContributionState.NOT_CONTRIBUTED

                if not worktree_info.worktree_branch:
                    return WorktreeContributionState.UNKNOWN

                branch_head = await rev_parse(worktree_info.worktree_branch, repo_root)
                if not branch_head:
                    return WorktreeContributionState.UNKNOWN
                if branch_head == base_head:
                    return WorktreeContributionState.NOT_CONTRIBUTED
                is_descendant = await is_ref_ancestor(
                    worktree_info.head_commit,
                    worktree_info.worktree_branch,
                    repo_root,
                )
                if is_descendant is True:
                    return WorktreeContributionState.CONTRIBUTED
                if is_descendant is None:
                    return WorktreeContributionState.UNKNOWN

        if not worktree_info.worktree_branch:
            return WorktreeContributionState.UNKNOWN

        branch_head = await rev_parse(worktree_info.worktree_branch, repo_root)
        if not branch_head:
            return WorktreeContributionState.UNKNOWN

        fallback_base = await merge_base("HEAD", worktree_info.worktree_branch, repo_root)
        if fallback_base is None:
            return WorktreeContributionState.UNKNOWN
        if branch_head == fallback_base:
            return WorktreeContributionState.NOT_CONTRIBUTED
        return WorktreeContributionState.CONTRIBUTED

    async def ensure_member_worktree(
        self,
        teammate: Any,
        role: TeamRole,
    ) -> str | None:
        """Create or reuse the current-session worktree for a teammate."""
        if not self._member_needs_worktree(teammate, role):
            return None

        team_backend = self._configurator.team_backend
        if team_backend is None:
            raise RuntimeError("Team worktree isolation requires a team backend")

        team_name = team_backend.team_name
        scope = self._owner_scope(team_name, teammate.member_name)
        worktree = get_member_worktree(teammate)
        worktree_path = worktree.path if worktree is not None else None
        if worktree is not None and worktree_path and os.path.isdir(worktree_path):
            if matches_scope(worktree, scope):
                info = info_from_options(
                    worktree,
                    member_name=teammate.member_name,
                    scope=scope,
                )
                if info is not None:
                    self._member_worktree_info[teammate.member_name] = info
                    return worktree_path
            team_logger.info(
                "Not reusing foreign or legacy worktree for teammate {}: {}",
                teammate.member_name,
                worktree_path,
            )
        elif worktree is not None and worktree_path and matches_scope(worktree, scope):
            await team_backend.db.member.update_member_worktree(
                teammate.member_name,
                team_name,
                MemberWorktreeOptions(isolation="worktree"),
            )
            self._member_worktree_info.pop(teammate.member_name, None)
            team_logger.info(
                "Cleared missing current-session worktree metadata before recreating teammate {}: {}",
                teammate.member_name,
                worktree_path,
            )

        manager = manager_for_worktree_scope(self._get_worktree_manager(), scope)
        snapshot = enter_project_worktree_context(scope.project_dir, scope.managed_root)
        try:
            result = await manager.create_owner_worktree(
                scope.worktree_name,
                source_dir=scope.project_dir,
            )
        finally:
            restore_project_worktree_context(snapshot)

        await team_backend.db.member.update_member_worktree(
            teammate.member_name,
            team_name,
            MemberWorktreeOptions(
                isolation="worktree",
                path=result.worktree_path,
                session_id=scope.session_id,
                project_hash=scope.project_hash,
                managed_root=scope.managed_root,
                worktree_branch=result.worktree_branch,
                head_commit=result.head_commit,
            ),
        )
        self._member_worktree_info[teammate.member_name] = MemberWorktreeInfo(
            worktree_path=result.worktree_path,
            worktree_name=scope.worktree_name,
            worktree_branch=result.worktree_branch,
            head_commit=result.head_commit,
            hook_based=bool(getattr(result, "hook_based", False)),
            session_id=scope.session_id,
            project_hash=scope.project_hash,
            managed_root=scope.managed_root,
        )
        team_logger.info(
            "Created worktree for teammate {}: {} ({})",
            teammate.member_name,
            result.worktree_path,
            result.worktree_branch,
        )
        return result.worktree_path

    async def _finalize_member_worktree(
        self,
        member_name: str,
    ) -> None:
        """Remove a clean current-session teammate worktree."""
        resolved = await resolve_current_session_member_worktree(
            self._configurator,
            self._member_worktree_info,
            member_name,
            self._owner_scope,
            log_foreign=True,
        )
        if resolved is None:
            return
        worktree_info = resolved.worktree_info

        if not os.path.isdir(worktree_info.worktree_path):
            await clear_member_worktree_metadata(member_name, resolved, self._member_worktree_info)
            team_logger.info("Cleared missing current-session worktree metadata for teammate {}", member_name)
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

        manager = manager_for_worktree_scope(self._get_worktree_manager(), resolved.scope)
        repo_root = await find_canonical_git_root(worktree_info.worktree_path)
        session = WorktreeSession(
            original_cwd=repo_root or worktree_info.worktree_path,
            worktree_path=worktree_info.worktree_path,
            worktree_name=worktree_info.worktree_name,
            worktree_branch=worktree_info.worktree_branch,
            original_head_commit=worktree_info.head_commit,
            member_name=member_name,
            team_name=resolved.team_name,
        )
        summary = await manager.count_changes(session)
        if summary is None:
            team_logger.warning(
                "Keeping worktree for teammate {} because changes could not be verified: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        changed_files = summary.changed_files
        remove_force = False
        if changed_files > 0:
            changed_files = await self._count_user_changed_files(worktree_info.worktree_path)
            remove_force = changed_files == 0

        if changed_files > 0:
            team_logger.info(
                "Keeping worktree for teammate {} with {} changed files: {} ({})",
                member_name,
                changed_files,
                worktree_info.worktree_path,
                worktree_info.worktree_branch,
            )
            return

        if repo_root is None:
            team_logger.warning(
                "Keeping worktree for teammate {} because repo root could not be resolved: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        contribution_state = await self._classify_worktree_contribution(worktree_info, repo_root)
        if contribution_state is WorktreeContributionState.UNKNOWN:
            team_logger.warning(
                "Keeping worktree for teammate {} because branch commit state could not be verified: {} ({})",
                member_name,
                worktree_info.worktree_path,
                worktree_info.worktree_branch,
            )
            return

        if contribution_state is WorktreeContributionState.CONTRIBUTED:
            team_logger.info(
                "Keeping worktree for teammate {} with branch commits while team lifecycle is still active: {} ({})",
                member_name,
                worktree_info.worktree_path,
                worktree_info.worktree_branch,
            )
            return

        removed = await manager.remove_worktree(worktree_info.worktree_path, repo_root, force=remove_force)
        if removed:
            await clear_member_worktree_metadata(member_name, resolved, self._member_worktree_info)
            team_logger.info(
                "Removed clean current-session worktree for teammate {}: {}",
                member_name,
                worktree_info.worktree_path,
            )

    async def finalize_non_contributing_member_worktrees(self) -> None:
        """Finalize current-session worktrees whose branches did not contribute commits."""
        await self._finalize_all_member_worktrees()

    async def finalize_all_member_worktrees_for_team_clean(self) -> None:
        """Finalize teammate worktrees before the team is permanently cleaned."""
        await self._remove_all_current_session_member_worktrees()

    async def _finalize_all_member_worktrees(self) -> None:
        """Finalize every teammate worktree owned by the current session."""
        context = team_context(self._configurator)
        if context is None:
            return
        team_backend, team_name = context
        for member_name in await teammate_member_names(team_backend, team_name):
            await self._finalize_member_worktree(member_name)

    async def _remove_all_current_session_member_worktrees(self) -> None:
        """Remove every teammate worktree owned by this team."""
        context = team_context(self._configurator)
        if context is None:
            return
        team_backend, team_name = context
        for member_name in await teammate_member_names(team_backend, team_name):
            await self._remove_current_session_member_worktree(member_name)
        await self._remove_orphaned_team_session_worktrees(team_name)

    async def _remove_orphaned_team_session_worktrees(self, team_name: str) -> None:
        """Remove team-owned worktrees that no longer have DB member metadata."""
        from openjiuwen.agent_teams.paths import team_sessions_dir
        from openjiuwen.harness.tools.worktree.git import find_canonical_git_root

        sessions_root = team_sessions_dir(team_name)
        if not sessions_root.is_dir():
            return

        sessions_root_real = os.path.realpath(str(sessions_root))
        manager = self._get_worktree_manager()
        for session_dir in sessions_root.iterdir():
            worktrees_root = session_dir / "worktrees"
            if not worktrees_root.is_dir():
                continue
            for worktree_path in worktrees_root.iterdir():
                if worktree_path.is_symlink() or not worktree_path.is_dir():
                    continue

                worktree_path_str = str(worktree_path)
                worktree_real = os.path.realpath(worktree_path_str)
                try:
                    if os.path.commonpath([sessions_root_real, worktree_real]) != sessions_root_real:
                        team_logger.warning(
                            "Keeping team worktree path outside session root during team clean: {}",
                            worktree_path_str,
                        )
                        continue
                except ValueError:
                    team_logger.warning(
                        "Keeping team worktree path with incompatible root during team clean: {}",
                        worktree_path_str,
                    )
                    continue

                repo_root = await find_canonical_git_root(worktree_path_str)
                if repo_root is None:
                    shutil.rmtree(worktree_path_str, ignore_errors=True)
                    team_logger.info(
                        "Removed orphaned team worktree directory during team clean: {}",
                        worktree_path_str,
                    )
                    continue

                removed = await manager.remove_worktree(worktree_path_str, repo_root, force=True)
                if removed:
                    team_logger.info(
                        "Removed orphaned team worktree during team clean: {}",
                        worktree_path_str,
                    )
                else:
                    team_logger.warning(
                        "Failed to remove orphaned team worktree during team clean: {}",
                        worktree_path_str,
                    )

    async def _remove_current_session_member_worktree(self, member_name: str) -> None:
        """Remove one current-session teammate worktree without contribution checks."""
        resolved = await resolve_current_session_member_worktree(
            self._configurator,
            self._member_worktree_info,
            member_name,
            self._owner_scope,
        )
        if resolved is None:
            return
        worktree_info = resolved.worktree_info

        if not os.path.isdir(worktree_info.worktree_path):
            await clear_member_worktree_metadata(member_name, resolved, self._member_worktree_info)
            return
        if worktree_info.hook_based:
            team_logger.info(
                "Keeping hook-based worktree for teammate {} during team clean: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        from openjiuwen.harness.tools.worktree.git import find_canonical_git_root

        repo_root = await find_canonical_git_root(worktree_info.worktree_path)
        if repo_root is None:
            shutil.rmtree(worktree_info.worktree_path, ignore_errors=True)
            await clear_member_worktree_metadata(member_name, resolved, self._member_worktree_info)
            team_logger.info(
                "Removed current-session worktree directory for teammate {} during team clean "
                "because git metadata could not be resolved: {}",
                member_name,
                worktree_info.worktree_path,
            )
            return

        manager = manager_for_worktree_scope(self._get_worktree_manager(), resolved.scope)
        removed = await manager.remove_worktree(worktree_info.worktree_path, repo_root, force=True)
        if removed:
            await clear_member_worktree_metadata(member_name, resolved, self._member_worktree_info)
            team_logger.info(
                "Removed current-session worktree for teammate {} during team clean: {}",
                member_name,
                worktree_info.worktree_path,
            )
