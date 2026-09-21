# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-host shared workspace for teams in one organization."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from openjiuwen.agent_teams.paths import organization_workspace_dir
from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
from openjiuwen.agent_teams.team_workspace.models import ConflictStrategy, TeamWorkspaceConfig
from openjiuwen.harness.tools.worktree.git import _run_git


class OrganizationWorkspaceConfig(BaseModel):
    """Phase-one, local-filesystem organization workspace configuration."""

    enabled: bool = True
    root_path: str | None = None
    version_control: bool = True
    conflict_strategy: ConflictStrategy = ConflictStrategy.LOCK
    preserve_on_dissolve: bool = True


class OrganizationWorkspaceManager(TeamWorkspaceManager):
    """Organization-scoped workspace reusing the proven local Team mechanics."""

    def __init__(
        self,
        *,
        organization_id: str,
        session_id: str,
        config: OrganizationWorkspaceConfig | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.session_id = session_id
        self.organization_config = config or OrganizationWorkspaceConfig()
        self._git_mutex = asyncio.Lock()
        root = self.organization_config.root_path or str(organization_workspace_dir(organization_id, session_id))
        super().__init__(
            config=TeamWorkspaceConfig(
                enabled=self.organization_config.enabled,
                root_path=root,
                artifact_dirs=["teams", "shared", "summary"],
                version_control=self.organization_config.version_control,
                conflict_strategy=self.organization_config.conflict_strategy,
            ),
            workspace_path=root,
            team_name=organization_id,
        )
        # Member assembly is synchronous and may mount before the Organization
        # runtime gets its first async initialization turn.
        os.makedirs(self.workspace_path, exist_ok=True)
        for directory in self.config.artifact_dirs:
            os.makedirs(os.path.join(self.workspace_path, directory), exist_ok=True)

    async def initialize(self, *, remote_url: str | None = None) -> None:
        """Create the local workspace and its Git repository, without Team metadata."""
        if remote_url:
            raise ValueError("organization workspace distributed mode is not supported")
        os.makedirs(self.workspace_path, exist_ok=True)
        for directory in self.config.artifact_dirs:
            os.makedirs(os.path.join(self.workspace_path, directory), exist_ok=True)
        if not self.config.version_control:
            return
        if os.path.isdir(os.path.join(self.workspace_path, ".git")):
            return
        await _run_git(["init"], cwd=self.workspace_path, check=True)
        await _run_git(
            ["commit", "--allow-empty", "-m", "Initialize organization workspace"],
            cwd=self.workspace_path,
            check=True,
        )

    def ensure_team_directory(self, team_id: str) -> Path:
        """Create and return the publishing directory owned by ``team_id``."""
        path = Path(self.workspace_path) / "teams" / self._safe_segment(team_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def mount_into_workspace(self, workspace_root: str) -> None:
        """Mount this workspace at ``.organization/{organization_id}``."""
        hub = os.path.join(workspace_root, ".organization")
        os.makedirs(hub, exist_ok=True)
        link_path = os.path.join(hub, self.organization_id)
        if self._prepare_mount_path(link_path):
            self._mount_directory(self.workspace_path, link_path)

    def unmount_from_workspace(self, workspace_root: str) -> None:
        """Remove this organization's managed mount from a member workspace."""
        link_path = os.path.join(workspace_root, ".organization", self.organization_id)
        if self._is_mounted_to_workspace(link_path):
            self._remove_directory_mount(link_path)

    def relative_path(self, mounted_path: str) -> str:
        """Resolve a mounted Organization path to a safe workspace-relative path."""
        normalized = str(mounted_path).replace("\\", "/")
        prefix = f".organization/{self.organization_id}/"
        if not normalized.startswith(prefix):
            raise ValueError("path is outside the current organization workspace")
        relative = normalized[len(prefix) :]
        pure = PurePosixPath(relative)
        if not relative or pure.is_absolute() or ".." in pure.parts:
            raise ValueError("invalid organization workspace path")
        return pure.as_posix()

    def can_write(self, relative_path: str, *, team_id: str, summary_team: bool) -> bool:
        """Apply the phase-one top-level ownership policy."""
        pure = PurePosixPath(relative_path)
        parts = pure.parts
        if not parts:
            return False
        if parts[0] == "shared":
            return True
        if summary_team:
            return parts[0] == "summary"
        return len(parts) >= 2 and parts[0] == "teams" and parts[1] == self._safe_segment(team_id)

    async def auto_commit_for_actor(self, relative_path: str, *, team_id: str, member_name: str) -> str | None:
        """Commit one change with an organization-wide actor identity."""
        # File locks permit concurrent writes to different files, but Git has
        # one shared index. Serialize add/commit so one actor never commits
        # another actor's staged change.
        async with self._git_mutex:
            return await super().auto_commit(relative_path, f"{team_id}/{member_name}")

    @staticmethod
    def _safe_segment(value: str) -> str:
        """Use the canonical path sanitizer without exposing it as public API."""
        from openjiuwen.agent_teams.paths import _safe_segment

        return _safe_segment(value)


_WORKSPACES: dict[tuple[str, str], OrganizationWorkspaceManager] = {}


def get_organization_workspace_manager(
    organization_id: str,
    session_id: str,
    *,
    config: OrganizationWorkspaceConfig | None = None,
) -> OrganizationWorkspaceManager:
    """Return the process-local manager for one Organization session."""
    key = (organization_id, session_id)
    manager = _WORKSPACES.get(key)
    if manager is None:
        manager = OrganizationWorkspaceManager(
            organization_id=organization_id,
            session_id=session_id,
            config=config,
        )
        _WORKSPACES[key] = manager
    return manager


def remove_organization_workspace_manager(organization_id: str, session_id: str) -> None:
    """Forget a dissolved Organization workspace manager without deleting artifacts."""
    _WORKSPACES.pop((organization_id, session_id), None)


__all__ = [
    "OrganizationWorkspaceConfig",
    "OrganizationWorkspaceManager",
    "get_organization_workspace_manager",
    "remove_organization_workspace_manager",
]
