# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow worker worktree lifecycle helpers."""

from __future__ import annotations

import os
from typing import Any

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.paths import project_worktree_hash, team_session_worktrees_dir
from openjiuwen.agent_teams.worktree.lifecycle import MemberWorktreeInfo
from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.tools.worktree.models import WorktreeSession


class SwarmflowWorkerWorktrees:
    """Create and finalize owner-scoped worktrees for swarmflow workers."""

    def __init__(
        self,
        *,
        team_name: str,
        build_context: Any = None,
        session_id: str | None = None,
    ) -> None:
        self._team_name = team_name
        self._build_context = build_context
        self._session_id = session_id
        self._active: dict[str, MemberWorktreeInfo] = {}
        self._orphans_reconciled = False

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
        worktrees_root = self._managed_root()
        if hasattr(manager, "with_worktrees_dir"):
            return manager.with_worktrees_dir(worktrees_root)
        if hasattr(manager, "with_base_dir"):
            return manager.with_base_dir(worktrees_root)
        return manager

    def _resolved_session_id(self) -> str:
        return self._session_id or get_session_id() or "default"

    def _managed_root(self) -> str:
        return str(team_session_worktrees_dir(self._team_name, self._resolved_session_id()))

    def _project_dir(self) -> str | None:
        project_dir = getattr(self._build_context, "project_dir", None)
        if isinstance(project_dir, str) and project_dir.strip() and os.path.isdir(project_dir):
            return os.path.realpath(project_dir)
        return None

    def _project_hash(self) -> str:
        project_dir = self._project_dir()
        if project_dir is None:
            return "no-project"
        return project_worktree_hash(project_dir)

    async def ensure(self, member_name: str, opts: dict) -> MemberWorktreeInfo | None:
        """Create an owner-scoped worktree for ``agent(options={"isolation": "worktree"})``."""
        if not self.needs_worktree(opts):
            return None
        await self._reconcile_orphans_once()
        manager = self._manager()
        project_dir = self._project_dir()
        project_hash = self._project_hash()
        slug = build_teammate_worktree_name(
            team_name=self._team_name,
            member_name=member_name,
            session_id=self._resolved_session_id(),
            project_hash=project_hash,
        )
        result = await manager.create_owner_worktree(slug, source_dir=project_dir)
        info = MemberWorktreeInfo(
            worktree_path=result.worktree_path,
            worktree_name=slug,
            worktree_branch=result.worktree_branch,
            head_commit=result.head_commit,
            hook_based=bool(getattr(result, "hook_based", False)),
            session_id=self._resolved_session_id(),
            project_hash=project_hash,
            managed_root=self._managed_root(),
        )
        self._active[member_name] = info
        team_logger.info(
            "Created worktree for swarmflow worker {}: {} ({})",
            member_name,
            result.worktree_path,
            result.worktree_branch,
        )
        return info

    async def _reconcile_orphans_once(self) -> None:
        """Remove clean unclaimed worktrees left by earlier runs (once per run).

        Worktree slugs embed the worker member name, which carries the run_id, so
        every relaunch builds fresh slugs and worktrees a previous run kept
        ("Keeping … with N changed files") are never reclaimed by anyone. Before
        this run creates its first worktree, sweep the session worktrees root:
        anything not in ``self._active`` is checked like ``finalize`` does — clean
        ones are removed, dirty ones (uncommitted changes / commits) stay for the
        leader to merge, and unverifiable ones stay (fail-closed). Dirty
        accumulation is bounded by the eventual ``delete_team`` sweep; removing
        only clean residue is lossless.
        """
        if self._orphans_reconciled:
            return
        self._orphans_reconciled = True
        if self._active:  # mid-run call: only sweep before the first creation
            return
        try:
            entries = os.listdir(self._managed_root())
        except FileNotFoundError:
            return
        except OSError as exc:
            team_logger.debug("Skipping swarmflow worktree reconcile: %s", exc)
            return
        if not entries:
            return
        from openjiuwen.harness.tools.worktree.git import (
            find_canonical_git_root,
            is_ref_ancestor,
            rev_parse,
            status_porcelain,
        )

        try:
            manager = self._manager()
        except BackendError:
            return  # no manager wired: nothing to sweep with (and nothing was created either)
        for slug in entries:
            wt_path = os.path.join(self._managed_root(), slug)
            if not os.path.isdir(wt_path):
                continue
            if any(info.worktree_name == slug for info in self._active.values()):
                continue
            repo_root = await find_canonical_git_root(wt_path)
            if repo_root is None:
                team_logger.debug(
                    "Skipping unclaimed swarmflow worktree without a git root: %s", wt_path
                )
                continue
            try:
                dirty = await status_porcelain(wt_path)
                repo_head = await rev_parse("HEAD", repo_root)
                wt_head = await rev_parse("HEAD", wt_path)
                ancestor = None
                if repo_head and wt_head:
                    ancestor = await is_ref_ancestor(wt_head, repo_head, wt_path)
            except Exception as exc:  # noqa: BLE001 - reconcile is best-effort
                team_logger.debug("Skipping unclaimed swarmflow worktree %s: %s", slug, exc)
                continue
            # Remove only provably clean residue: no uncommitted changes and no
            # commits of its own (its HEAD is contained in the repo's HEAD).
            # Anything unverifiable or dirty stays (fail-closed) for the leader.
            if dirty or ancestor is not True:
                continue
            if await manager.remove_worktree(wt_path, repo_root):
                team_logger.info(
                    "Removed unclaimed clean swarmflow worktree from a prior run: {}", wt_path
                )

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
