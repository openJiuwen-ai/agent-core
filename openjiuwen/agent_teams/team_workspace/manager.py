# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""Team shared workspace manager.

Handles locking, versioning, sync, and conflict detection for the team
shared workspace directory. File I/O is delegated to SysOperation tools
via the .team/ symlink mount — this module manages only metadata and
version control.

Two operating modes:
- LOCAL: single _team_workspace/ directory, symlink mount, in-memory locks.
- DISTRIBUTED: per-node clone, git push/pull sync, leader-coordinated locks
  (Phase 3).
"""

import asyncio
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from openjiuwen.agent_teams.schema.events import (
    BaseEventMessage,
    WorkspaceLockRequestEvent,
    WorkspaceLockResponseEvent,
)
from openjiuwen.agent_teams.team_workspace.models import (
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from openjiuwen.harness.tools.worktree.git import _run_git, rev_parse
from openjiuwen.core.common.logging import team_logger

try:
    import winerror
except ImportError:  # pragma: no cover - unavailable outside Windows
    ERROR_PRIVILEGE_NOT_HELD = 1314
else:
    ERROR_PRIVILEGE_NOT_HELD = winerror.ERROR_PRIVILEGE_NOT_HELD


class TeamWorkspaceManager:
    """Manages team shared workspace metadata and version control.

    File I/O is handled by standard SysOperation tools via .team/ mount.
    This manager handles locking, versioning, sync, and conflict detection.

    Operates in two modes:
    - LOCAL: single _team_workspace/ dir, symlink mount, in-memory locks
    - DISTRIBUTED: per-node clone, git push/pull sync, Messager-coordinated locks
    """

    def __init__(
        self,
        config: TeamWorkspaceConfig,
        workspace_path: str,
        team_name: str,
        *,
        mode: WorkspaceMode = WorkspaceMode.LOCAL,
        messager: Any | None = None,
        leader_id: str | None = None,
        node_id: str | None = None,
        publish_event: Callable[[str, BaseEventMessage], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.workspace_path = workspace_path
        self.team_name = team_name
        self.mode = mode
        self.publish_event = publish_event

        # Local lock state (LOCAL mode, or leader's authority in DISTRIBUTED)
        self._locks: dict[str, WorkspaceFileLock] = {}
        self._lock_mutex = asyncio.Lock()

        # Distributed coordination (Phase 3)
        self._messager = messager
        self._leader_id = leader_id
        self._node_id = node_id
        self._pending_lock_requests: dict[str, asyncio.Future[WorkspaceLockResponseEvent]] = {}

    # ── Initialization ───────────────────────────────────────

    async def initialize(self, *, remote_url: str | None = None) -> None:
        """Initialize workspace directory and git repo.

        When ``config.version_control`` is False, only the workspace and
        artifact directories are created — no git repo, no remote, no
        initial commit. The workspace behaves as a plain shared directory.

        When ``config.version_control`` is True:
        - LOCAL mode: git init a fresh repo with an empty initial commit.
        - DISTRIBUTED mode:
          - Leader: git init + add remote origin (if remote_url provided).
          - Remote node: git clone from remote_url.
        Skips git init if .git directory already exists.

        Args:
            remote_url: Git remote URL for distributed workspace repo.
        """
        os.makedirs(self.workspace_path, exist_ok=True)

        # Create artifact directories regardless of version_control mode
        for d in self.config.artifact_dirs:
            os.makedirs(os.path.join(self.workspace_path, d), exist_ok=True)

        # Shared skills directory; each member agent's SkillUseRail picks
        # this up via the ``.team/{team_name}`` mount so team-authored
        # skills are visible everywhere.
        os.makedirs(os.path.join(self.workspace_path, "skills"), exist_ok=True)

        if not self.config.version_control:
            team_logger.info(
                "Workspace %s initialized as plain shared directory (version_control disabled)",
                self.workspace_path,
            )
            return

        git_dir = os.path.join(self.workspace_path, ".git")
        if os.path.isdir(git_dir):
            team_logger.debug("Workspace already initialized at %s", self.workspace_path)
            return

        if self.mode == WorkspaceMode.DISTRIBUTED and remote_url and self._leader_id != self._node_id:
            # Remote node: clone the workspace repo
            parent = os.path.dirname(self.workspace_path)
            name = os.path.basename(self.workspace_path)
            await _run_git(["clone", remote_url, name], cwd=parent, check=True)
            team_logger.info("Cloned workspace repo from %s", remote_url)
        else:
            # Leader or LOCAL: init fresh repo
            await _run_git(["init"], cwd=self.workspace_path, check=True)
            await _run_git(
                ["commit", "--allow-empty", "-m", "Initialize team workspace"],
                cwd=self.workspace_path,
                check=True,
            )
            team_logger.info("Initialized workspace git repo at %s", self.workspace_path)

        # Leader in DISTRIBUTED: set up remote if provided
        if self.mode == WorkspaceMode.DISTRIBUTED and remote_url and self._leader_id == self._node_id:
            existing_remote = await _run_git(
                ["remote", "get-url", "origin"],
                cwd=self.workspace_path,
            )
            if not existing_remote.ok:
                await _run_git(
                    ["remote", "add", "origin", remote_url],
                    cwd=self.workspace_path,
                )
                team_logger.info("Added remote origin %s", remote_url)

    # ── Workspace / worktree mount ─────────────────────────────

    def _mount_directory(self, target_path: str, link_path: str) -> None:
        """Create a directory link, falling back to junctions on Windows.

        Windows requires elevated privileges or Developer Mode for directory
        symlinks. When that privilege is unavailable, fall back to a junction
        so team workspace mounts still work for normal users.

        Args:
            target_path: Existing directory to expose.
            link_path: Link path to create.
        """
        try:
            os.symlink(target_path, link_path, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) != ERROR_PRIVILEGE_NOT_HELD:
                raise
            self._create_windows_junction(target_path, link_path)
            team_logger.info(
                "Symlink privilege unavailable on Windows; mounted %s via junction at %s",
                target_path,
                link_path,
            )

    @staticmethod
    def _create_windows_junction(target_path: str, link_path: str) -> None:
        """Create a directory junction using mklink /J on Windows."""
        cmd_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        result = subprocess.run(
            [cmd_path, "/c", "mklink", "/J", link_path, target_path],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip()
            raise OSError(f"Failed to create junction {link_path} -> {target_path}: {error_output}")

    def _is_mounted_to_workspace(self, link_path: str) -> bool:
        try:
            return os.path.samefile(link_path, self.workspace_path)
        except OSError:
            return False

    def _merge_existing_mount_contents(self, link_path: str) -> None:
        """Copy files from a stale mount directory into the canonical workspace.

        A stale real ``.team/<team_name>`` directory can be created when file
        tools write before the mount exists.  Merge missing files so user
        artifacts are not stranded before the directory is replaced by a mount.
        Existing canonical files win to avoid overwriting newer workspace data.
        """
        if not os.path.isdir(link_path) or os.path.islink(link_path):
            return
        for root, dirs, files in os.walk(link_path):
            rel_root = os.path.relpath(root, link_path)
            dst_root = self.workspace_path if rel_root == "." else os.path.join(self.workspace_path, rel_root)
            os.makedirs(dst_root, exist_ok=True)
            for dirname in dirs:
                os.makedirs(os.path.join(dst_root, dirname), exist_ok=True)
            for filename in files:
                src = os.path.join(root, filename)
                dst = os.path.join(dst_root, filename)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)

    @staticmethod
    def _backup_existing_mount_path(link_path: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = f"{link_path}.stale-{stamp}"
        counter = 1
        while os.path.exists(backup_path) or os.path.islink(backup_path):
            counter += 1
            backup_path = f"{link_path}.stale-{stamp}-{counter}"
        os.rename(link_path, backup_path)
        return backup_path

    def _prepare_mount_path(self, link_path: str) -> bool:
        """Return True when a mount should be created at ``link_path``."""
        if not os.path.exists(link_path) and not os.path.islink(link_path):
            return True
        if self._is_mounted_to_workspace(link_path):
            return False

        self._merge_existing_mount_contents(link_path)
        backup_path = self._backup_existing_mount_path(link_path)
        team_logger.warning(
            "Replaced stale team workspace mount path %s; previous contents moved to %s",
            link_path,
            backup_path,
        )
        return True

    def mount_into_workspace(self, workspace_root: str) -> None:
        """Create .team/{team_name} symlink in an agent workspace.

        Mounts this team workspace into the agent's workspace hub so
        the agent can access shared files via ``.team/{team_name}/...``.

        Args:
            workspace_root: Absolute path to the agent workspace root.
        """
        team_dir = os.path.join(workspace_root, ".team")
        os.makedirs(team_dir, exist_ok=True)
        link_path = os.path.join(team_dir, self.team_name)
        if self._prepare_mount_path(link_path):
            self._mount_directory(self.workspace_path, link_path)
            team_logger.debug(
                "Mounted team workspace %s into %s",
                self.team_name,
                link_path,
            )

    def mount_worktree(self, slug: str, worktree_path: str) -> None:
        """Expose a worktree in the team workspace at ``.worktree/{slug}``.

        Creates a stable navigation entry so leader / teammate agents
        sharing this workspace can see the per-member worktrees at a
        glance via ``.worktree/<slug>``. Stale symlinks (e.g. pointing
        at a removed worktree) are replaced; non-symlink collisions are
        skipped with a warning so this never clobbers user data.

        Args:
            slug: Worktree slug used both as the symlink basename and
                the identifier referenced by ``unmount_worktree``.
            worktree_path: Absolute path the symlink should resolve to.
        """
        wt_dir = os.path.join(self.workspace_path, ".worktree")
        os.makedirs(wt_dir, exist_ok=True)
        link_path = os.path.join(wt_dir, slug)
        if os.path.lexists(link_path):
            if os.path.islink(link_path):
                os.unlink(link_path)
            else:
                team_logger.warning(
                    "Worktree mount path '%s' exists and is not a symlink -- skipping",
                    link_path,
                )
                return
        self._mount_directory(worktree_path, link_path)
        team_logger.debug("Mounted worktree '%s' at %s", slug, link_path)

    def unmount_worktree(self, slug: str) -> None:
        """Remove the ``.worktree/{slug}`` entry if it exists.

        Args:
            slug: Worktree slug previously passed to ``mount_worktree``.
        """
        link_path = os.path.join(self.workspace_path, ".worktree", slug)
        if os.path.islink(link_path):
            os.unlink(link_path)
            team_logger.debug("Unmounted worktree '%s' from %s", slug, link_path)

    def mount_into_worktree(self, worktree_path: str) -> None:
        """Create .team symlink inside a worktree pointing to this workspace.

        Also appends .agent/ and .team/ to the worktree's .gitignore if
        not already present.

        Args:
            worktree_path: Absolute path to the worktree directory.
        """
        link_path = os.path.join(worktree_path, ".team")
        if self._prepare_mount_path(link_path):
            self._mount_directory(self.workspace_path, link_path)

        gitignore_path = os.path.join(worktree_path, ".gitignore")
        entries_to_add = [".agent/", ".team/"]
        existing = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path) as f:
                existing = f.read()

        additions = [e for e in entries_to_add if e not in existing]
        if additions:
            with open(gitignore_path, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write("# Agent Teams managed\n")
                for entry in additions:
                    f.write(f"{entry}\n")

    # ── Distributed sync ─────────────────────────────────────

    async def pull(self) -> bool:
        """Pull latest changes from remote (DISTRIBUTED only).

        Returns:
            True if new changes were pulled, False otherwise.
            Always returns False in LOCAL mode or when version_control is off.
        """
        if not self.config.version_control:
            return False
        if self.mode != WorkspaceMode.DISTRIBUTED:
            return False

        r = await _run_git(
            ["pull", "--rebase", "--autostash", "origin", "main"],
            cwd=self.workspace_path,
        )
        return r.ok and "Already up to date" not in r.stdout

    async def push(self) -> bool:
        """Push local commits to remote (DISTRIBUTED only).

        Returns:
            True on success, in LOCAL mode, or when version_control is off
            (all no-ops). False on push failure.
        """
        if not self.config.version_control:
            return True
        if self.mode != WorkspaceMode.DISTRIBUTED:
            return True

        r = await _run_git(
            ["push", "origin", "main"],
            cwd=self.workspace_path,
        )
        if not r.ok:
            team_logger.warning("Workspace push failed: %s. Will retry on next write.", r.stderr)
        return r.ok

    # ── Version control ──────────────────────────────────────

    async def auto_commit(self, relative_path: str, member_name: str) -> str | None:
        """Auto-commit a file change, then push if distributed.

        Stages the file, checks for actual changes, commits, and pushes
        in distributed mode with one retry on push failure.

        Args:
            relative_path: File path relative to workspace root.
            member_name: ID of the member making the change.

        Returns:
            Commit SHA on success, None if nothing to commit, commit
            failed, or ``version_control`` is disabled.
        """
        if not self.config.version_control:
            return None

        await _run_git(["add", relative_path], cwd=self.workspace_path)

        status = await _run_git(["diff", "--cached", "--quiet"], cwd=self.workspace_path)
        if status.ok:
            return None  # Nothing staged

        msg = f"[{member_name}] Update {relative_path}"
        result = await _run_git(["commit", "-m", msg], cwd=self.workspace_path)
        if not result.ok:
            return None

        sha = await rev_parse("HEAD", self.workspace_path)

        if self.mode == WorkspaceMode.DISTRIBUTED:
            pushed = await self.push()
            if not pushed:
                await self.pull()
                retry = await self.push()
                if not retry:
                    team_logger.error("Workspace push failed after retry for %s", relative_path)

        return sha

    async def get_history(self, relative_path: str, limit: int = 10) -> list[dict]:
        """Get version history for a file.

        In distributed mode, pulls latest before querying history.
        Returns an empty list when ``version_control`` is disabled.

        Args:
            relative_path: File path relative to workspace root.
            limit: Maximum number of history entries to return.

        Returns:
            List of dicts with keys: commit, author, date, message.
        """
        if not self.config.version_control:
            return []

        if self.mode == WorkspaceMode.DISTRIBUTED:
            await self.pull()

        r = await _run_git(
            ["log", f"--max-count={limit}", "--format=%H|%an|%ai|%s", "--", relative_path],
            cwd=self.workspace_path,
        )
        if not r.ok or not r.stdout:
            return []

        history = []
        for line in r.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                history.append(
                    {
                        "commit": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                )
        return history

    # ── File locks (local + distributed) ─────────────────────

    def get_lock(self, file_path: str) -> WorkspaceFileLock | None:
        """Get lock for a file from local cache.

        No network round-trip; returns cached lock state only.

        Args:
            file_path: File path relative to workspace root.

        Returns:
            Lock entry if held and not expired, None otherwise.
        """
        lock = self._locks.get(file_path)
        if lock and lock.is_expired():
            self._locks.pop(file_path, None)
            return None
        return lock

    async def acquire_lock(
        self,
        file_path: str,
        member_name: str,
        display_name: str,
        *,
        timeout_seconds: int = 300,
    ) -> bool:
        """Acquire file-level lock.

        LOCAL mode or leader node: in-memory lock with async mutex.
        DISTRIBUTED non-leader: delegates to _remote_acquire_lock (Phase 3).

        Re-entrant for the same holder: refreshes the lock silently.
        Expired locks from other holders are reclaimed.

        Args:
            file_path: File path relative to workspace root.
            member_name: ID of the member requesting the lock.
            display_name: Display name of the requesting member.
            timeout_seconds: Lock timeout in seconds.

        Returns:
            True if lock was acquired, False if held by another member.
        """
        if self.mode == WorkspaceMode.DISTRIBUTED and self._leader_id != self._node_id:
            return await self._remote_acquire_lock(
                file_path,
                member_name,
                display_name,
                timeout_seconds=timeout_seconds,
            )

        # LOCAL mode, or this IS the leader in DISTRIBUTED
        async with self._lock_mutex:
            existing = self._locks.get(file_path)

            if existing and not existing.is_expired() and existing.holder_id != member_name:
                return False

            now = datetime.now(timezone.utc)
            self._locks[file_path] = WorkspaceFileLock(
                file_path=file_path,
                holder_id=member_name,
                holder_name=display_name,
                acquired_at=now.isoformat(),
                timeout_seconds=timeout_seconds,
            )
            return True

    async def release_lock(self, file_path: str, member_name: str) -> bool:
        """Release a file lock.

        LOCAL mode or leader: direct release from in-memory dict.
        DISTRIBUTED non-leader: delegates to _remote_release_lock (Phase 3).

        Args:
            file_path: File path relative to workspace root.
            member_name: ID of the member releasing the lock.

        Returns:
            True if lock was released, False if not held by this member.
        """
        if self.mode == WorkspaceMode.DISTRIBUTED and self._leader_id != self._node_id:
            return await self._remote_release_lock(file_path, member_name)

        async with self._lock_mutex:
            existing = self._locks.get(file_path)
            if not existing or existing.holder_id != member_name:
                return False
            del self._locks[file_path]
            return True

    async def list_locks(self) -> list[WorkspaceFileLock]:
        """List all active (non-expired) locks.

        In distributed mode, remote nodes see their local cache only
        (populated by lock responses from leader).

        Returns:
            List of non-expired lock entries.
        """
        async with self._lock_mutex:
            expired_keys = [k for k, v in self._locks.items() if v.is_expired()]
            for k in expired_keys:
                del self._locks[k]
            return list(self._locks.values())

    # ── Distributed lock coordination (Phase 3 placeholders) ─

    async def _remote_acquire_lock(
        self,
        file_path: str,
        member_name: str,
        display_name: str,
        *,
        timeout_seconds: int = 300,
    ) -> bool:
        """Send lock acquire request to leader via Messager.

        Phase 3 implementation. Currently raises NotImplementedError.

        Args:
            file_path: File path relative to workspace root.
            member_name: ID of the requesting member.
            display_name: Display name of the requesting member.
            timeout_seconds: Lock timeout in seconds.

        Raises:
            NotImplementedError: Distributed lock coordination is Phase 3.
        """
        raise NotImplementedError("Distributed lock acquire is Phase 3")

    async def _remote_release_lock(self, file_path: str, member_name: str) -> bool:
        """Send lock release request to leader via Messager.

        Phase 3 implementation. Currently raises NotImplementedError.

        Args:
            file_path: File path relative to workspace root.
            member_name: ID of the member releasing the lock.

        Raises:
            NotImplementedError: Distributed lock coordination is Phase 3.
        """
        raise NotImplementedError("Distributed lock release is Phase 3")

    async def _send_lock_request(self, request: WorkspaceLockRequestEvent) -> WorkspaceLockResponseEvent | None:
        """Send lock request to leader and wait for response.

        Phase 3 implementation. Currently raises NotImplementedError.

        Args:
            request: Lock request event to send.

        Raises:
            NotImplementedError: Distributed lock messaging is Phase 3.
        """
        raise NotImplementedError("Distributed lock messaging is Phase 3")

    # ── Leader-side lock request handling ─────────────────────

    async def handle_lock_request(self, request: WorkspaceLockRequestEvent) -> WorkspaceLockResponseEvent:
        """Handle incoming lock request (called on LEADER node only).

        Leader is the single authority for lock state. Evaluates the
        request against its local _locks dict and returns a response.

        Args:
            request: Lock request event from a remote node.

        Returns:
            Lock response event indicating whether the request was granted.
        """
        granted = False
        if request.action == "acquire":
            granted = await self.acquire_lock(
                request.file_path,
                request.member_name or "",
                request.holder_name or request.member_name or "",
                timeout_seconds=request.timeout_seconds or 300,
            )
        elif request.action == "release":
            granted = await self.release_lock(
                request.file_path,
                request.member_name or "",
            )

        holder_dict = None
        if not granted:
            existing = self._locks.get(request.file_path)
            if existing:
                holder_dict = existing.model_dump()

        return WorkspaceLockResponseEvent(
            team_name=self.team_name,
            member_name=request.member_name or "",
            file_path=request.file_path,
            granted=granted,
            holder=holder_dict,
        )

    def handle_lock_response(self, response: WorkspaceLockResponseEvent) -> None:
        """Handle incoming lock response (called on REMOTE node).

        Resolves the pending future so the caller of acquire_lock or
        release_lock gets unblocked.

        Args:
            response: Lock response event from the leader.
        """
        for act in ("acquire", "release"):
            key = f"{act}:{response.file_path}"
            future = self._pending_lock_requests.get(key)
            if future and not future.done():
                future.set_result(response)
                return
