# coding: utf-8

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
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from openjiuwen.agent_teams.schema.events import (
    BaseEventMessage,
    WorkspaceLockRequestEvent,
    WorkspaceLockResponseEvent,
)
from openjiuwen.agent_teams.worktree.git import _run_git, rev_parse
from openjiuwen.agent_teams.worktree.models import (
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from openjiuwen.core.common.logging import team_logger


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
        team_id: str,
        *,
        mode: WorkspaceMode = WorkspaceMode.LOCAL,
        messager: Any | None = None,
        leader_id: str | None = None,
        node_id: str | None = None,
        publish_event: Callable[[str, BaseEventMessage], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.workspace_path = workspace_path
        self.team_id = team_id
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

        LOCAL mode: git init a fresh repo with an empty initial commit.
        DISTRIBUTED mode:
          - Leader: git init + add remote origin (if remote_url provided).
          - Remote node: git clone from remote_url.

        Skips initialization if .git directory already exists.

        Args:
            remote_url: Git remote URL for distributed workspace repo.
        """
        os.makedirs(self.workspace_path, exist_ok=True)

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

        # Create artifact directories
        for d in self.config.artifact_dirs:
            os.makedirs(os.path.join(self.workspace_path, d), exist_ok=True)

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

    # ── Worktree mount ───────────────────────────────────────

    def mount_into_worktree(self, worktree_path: str) -> None:
        """Create .team symlink inside a worktree pointing to this workspace.

        Also appends .agent/ and .team/ to the worktree's .gitignore if
        not already present.

        Args:
            worktree_path: Absolute path to the worktree directory.
        """
        link_path = os.path.join(worktree_path, ".team")
        if not os.path.exists(link_path):
            os.symlink(self.workspace_path, link_path, target_is_directory=True)

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
            Always returns False in LOCAL mode.
        """
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
            True on success or in LOCAL mode (no-op). False on push failure.
        """
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

    async def auto_commit(self, relative_path: str, member_id: str) -> str | None:
        """Auto-commit a file change, then push if distributed.

        Stages the file, checks for actual changes, commits, and pushes
        in distributed mode with one retry on push failure.

        Args:
            relative_path: File path relative to workspace root.
            member_id: ID of the member making the change.

        Returns:
            Commit SHA on success, None if nothing to commit or commit failed.
        """
        await _run_git(["add", relative_path], cwd=self.workspace_path)

        status = await _run_git(["diff", "--cached", "--quiet"], cwd=self.workspace_path)
        if status.ok:
            return None  # Nothing staged

        msg = f"[{member_id}] Update {relative_path}"
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

        Args:
            relative_path: File path relative to workspace root.
            limit: Maximum number of history entries to return.

        Returns:
            List of dicts with keys: commit, author, date, message.
        """
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
        member_id: str,
        member_name: str,
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
            member_id: ID of the member requesting the lock.
            member_name: Display name of the requesting member.
            timeout_seconds: Lock timeout in seconds.

        Returns:
            True if lock was acquired, False if held by another member.
        """
        if self.mode == WorkspaceMode.DISTRIBUTED and self._leader_id != self._node_id:
            return await self._remote_acquire_lock(
                file_path,
                member_id,
                member_name,
                timeout_seconds=timeout_seconds,
            )

        # LOCAL mode, or this IS the leader in DISTRIBUTED
        async with self._lock_mutex:
            existing = self._locks.get(file_path)

            if existing and not existing.is_expired() and existing.holder_id != member_id:
                return False

            now = datetime.now(timezone.utc)
            self._locks[file_path] = WorkspaceFileLock(
                file_path=file_path,
                holder_id=member_id,
                holder_name=member_name,
                acquired_at=now.isoformat(),
                timeout_seconds=timeout_seconds,
            )
            return True

    async def release_lock(self, file_path: str, member_id: str) -> bool:
        """Release a file lock.

        LOCAL mode or leader: direct release from in-memory dict.
        DISTRIBUTED non-leader: delegates to _remote_release_lock (Phase 3).

        Args:
            file_path: File path relative to workspace root.
            member_id: ID of the member releasing the lock.

        Returns:
            True if lock was released, False if not held by this member.
        """
        if self.mode == WorkspaceMode.DISTRIBUTED and self._leader_id != self._node_id:
            return await self._remote_release_lock(file_path, member_id)

        async with self._lock_mutex:
            existing = self._locks.get(file_path)
            if not existing or existing.holder_id != member_id:
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
        member_id: str,
        member_name: str,
        *,
        timeout_seconds: int = 300,
    ) -> bool:
        """Send lock acquire request to leader via Messager.

        Phase 3 implementation. Currently raises NotImplementedError.

        Args:
            file_path: File path relative to workspace root.
            member_id: ID of the requesting member.
            member_name: Display name of the requesting member.
            timeout_seconds: Lock timeout in seconds.

        Raises:
            NotImplementedError: Distributed lock coordination is Phase 3.
        """
        raise NotImplementedError("Distributed lock acquire is Phase 3")

    async def _remote_release_lock(self, file_path: str, member_id: str) -> bool:
        """Send lock release request to leader via Messager.

        Phase 3 implementation. Currently raises NotImplementedError.

        Args:
            file_path: File path relative to workspace root.
            member_id: ID of the member releasing the lock.

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
                request.member_id or "",
                request.holder_name or request.member_id or "",
                timeout_seconds=request.timeout_seconds or 300,
            )
        elif request.action == "release":
            granted = await self.release_lock(
                request.file_path,
                request.member_id or "",
            )

        holder_dict = None
        if not granted:
            existing = self._locks.get(request.file_path)
            if existing:
                holder_dict = existing.model_dump()

        return WorkspaceLockResponseEvent(
            team_id=self.team_id,
            member_id=request.member_id or "",
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
