# coding: utf-8

"""Distributed worktree backend for remote nodes.

Enables worktree isolation across machines: the leader sends worktree
lifecycle requests via Messager; each remote node maintains its own
shallow clone and creates local worktrees within it.

See design.md Section 23 for the full architecture.
"""

import asyncio
import hashlib
import os
from typing import Any

from pydantic import BaseModel

from openjiuwen.agent_teams.worktree.git import _run_git, fetch_ref, get_default_branch
from openjiuwen.agent_teams.worktree.models import WorktreeConfig, WorktreeCreateResult
from openjiuwen.core.common.logging import team_logger


# -- Request / Response models ------------------------------------------------


class WorktreeRemoteRequest(BaseModel):
    """Request sent to a remote node to manage a worktree."""

    action: str
    """Operation: "create", "remove", or "exists"."""

    slug: str | None = None
    """Worktree slug (required for "create")."""

    repo_url: str | None = None
    """Git remote URL of the source repo (required for "create")."""

    base_branch: str | None = None
    """Branch to base the new worktree on (defaults to "main")."""

    worktree_path: str | None = None
    """Absolute path on the remote node (required for "remove" / "exists")."""

    config: dict[str, Any] | None = None
    """Serialized WorktreeConfig for the remote manager."""


class WorktreeRemoteResponse(BaseModel):
    """Response from a remote node after a worktree operation."""

    success: bool = True
    """Whether the operation succeeded."""

    worktree_path: str | None = None
    """Absolute path to the worktree on the remote node."""

    worktree_branch: str | None = None
    """Git branch name for the created worktree."""

    head_commit: str | None = None
    """HEAD commit SHA after creation."""

    existed: bool = False
    """True if the worktree already existed (fast recovery)."""

    exists: bool = False
    """True when answering an "exists" query positively."""

    error: str | None = None
    """Error message on failure."""


# -- Remote backend (leader side) ---------------------------------------------


class RemoteWorktreeBackend:
    """Worktree backend for remote nodes.

    Instead of ``git worktree add`` on a shared repo, each remote
    node maintains its own clone.  "Creating a worktree" on a remote
    means: ensure the repo is cloned, create a local worktree within
    that clone.

    Communication with the remote node happens via Messager (P2P).
    The leader calls ``create``/``remove``/``exists``; each call
    serializes a ``WorktreeRemoteRequest``, sends it to the target
    node, and awaits a ``WorktreeRemoteResponse``.
    """

    def __init__(self, config: WorktreeConfig, messager: Any, node_id: str):
        self._config = config
        self._messager = messager
        self._node_id = node_id
        self._pending: dict[str, asyncio.Future[WorktreeRemoteResponse]] = {}

    async def create(self, slug: str, repo_root: str) -> WorktreeCreateResult:
        """Request the remote node to create a worktree.

        Args:
            slug: Validated worktree name.
            repo_root: Local repository root (used to determine remote URL).

        Returns:
            WorktreeCreateResult with path and metadata from the remote node.
        """
        request = WorktreeRemoteRequest(
            action="create",
            slug=slug,
            repo_url=await self._get_repo_url(repo_root),
            base_branch=await get_default_branch(repo_root),
            config=self._config.model_dump(),
        )
        response = await self._send_and_wait(request)
        if not response.success:
            raise RuntimeError(f"Remote worktree creation failed: {response.error}")
        return WorktreeCreateResult(
            worktree_path=response.worktree_path or "",
            worktree_branch=response.worktree_branch,
            head_commit=response.head_commit,
            existed=response.existed,
        )

    async def remove(self, worktree_path: str, repo_root: str) -> bool:
        """Request the remote node to remove a worktree.

        Args:
            worktree_path: Absolute path on the remote node.
            repo_root: Local repository root (unused, kept for protocol).

        Returns:
            True if the remote reports success.
        """
        request = WorktreeRemoteRequest(
            action="remove",
            worktree_path=worktree_path,
        )
        response = await self._send_and_wait(request)
        return response.success

    async def exists(self, worktree_path: str) -> bool:
        """Ask the remote node whether a worktree exists.

        Args:
            worktree_path: Absolute path on the remote node.

        Returns:
            True if the remote reports the worktree exists.
        """
        request = WorktreeRemoteRequest(
            action="exists",
            worktree_path=worktree_path,
        )
        response = await self._send_and_wait(request)
        return response.exists

    # -- Internal helpers -----------------------------------------------------

    async def _send_and_wait(
        self,
        request: WorktreeRemoteRequest,
    ) -> WorktreeRemoteResponse:
        """Send request via Messager and await the response.

        Args:
            request: Serializable worktree request.

        Returns:
            Parsed response from the remote node.
        """
        payload = request.model_dump()
        team_logger.debug(
            "Sending worktree %s request to node %s",
            request.action,
            self._node_id,
        )
        response_data = await self._messager.send_and_wait(self._node_id, payload)
        return WorktreeRemoteResponse.model_validate(response_data)

    async def _get_repo_url(self, repo_root: str) -> str:
        """Resolve the origin remote URL for the local repo.

        Args:
            repo_root: Local repository root directory.

        Returns:
            Remote URL string.

        Raises:
            RuntimeError: If the origin URL cannot be determined.
        """
        r = await _run_git(["remote", "get-url", "origin"], cwd=repo_root)
        if not r.ok:
            raise RuntimeError("Cannot determine remote URL")
        return r.stdout


# -- Remote handler (node side) -----------------------------------------------


class WorktreeRemoteHandler:
    """Handles worktree requests on a remote node.

    Registered as a direct-message handler on the Messager.
    Ensures the repo is cloned locally, then delegates to
    the local WorktreeManager.
    """

    def __init__(self, manager: "WorktreeManager"):
        from openjiuwen.agent_teams.worktree.manager import WorktreeManager as _WM

        self._manager: _WM = manager
        self._cloned_repos: dict[str, str] = {}

    async def handle(self, request: WorktreeRemoteRequest) -> WorktreeRemoteResponse:
        """Dispatch a worktree request to the appropriate handler.

        Args:
            request: Incoming worktree request from the leader.

        Returns:
            Response with operation results or error details.
        """
        try:
            if request.action == "create":
                return await self._handle_create(request)
            if request.action == "remove":
                return await self._handle_remove(request)
            if request.action == "exists":
                return await self._handle_exists(request)
            return WorktreeRemoteResponse(success=False, error=f"Unknown action: {request.action}")
        except Exception as exc:
            team_logger.exception("Remote worktree handler failed for action=%s", request.action)
            return WorktreeRemoteResponse(success=False, error=str(exc))

    async def _handle_create(self, request: WorktreeRemoteRequest) -> WorktreeRemoteResponse:
        """Clone repo if needed, fetch base branch, create local worktree.

        Args:
            request: Must have slug and repo_url populated.

        Returns:
            Response with created worktree metadata.
        """
        repo_root = await self._ensure_repo(request.repo_url or "")
        await fetch_ref(repo_root, request.base_branch or "main")
        result = await self._manager.create_agent_worktree(request.slug or "")
        return WorktreeRemoteResponse(
            worktree_path=result.worktree_path,
            worktree_branch=result.worktree_branch,
            head_commit=result.head_commit,
            existed=result.existed,
        )

    async def _handle_remove(self, request: WorktreeRemoteRequest) -> WorktreeRemoteResponse:
        """Remove a worktree on this node.

        Args:
            request: Must have worktree_path populated.

        Returns:
            Response indicating success or failure.
        """
        wt_path = request.worktree_path or ""
        # Find the repo root that owns this worktree
        from openjiuwen.agent_teams.worktree.git import find_canonical_git_root

        repo_root = await find_canonical_git_root(wt_path)
        if not repo_root:
            return WorktreeRemoteResponse(success=False, error="Cannot find repo root for worktree")
        ok = await self._manager._backend.remove(wt_path, repo_root)
        return WorktreeRemoteResponse(success=ok)

    async def _handle_exists(self, request: WorktreeRemoteRequest) -> WorktreeRemoteResponse:
        """Check whether a worktree exists on this node.

        Args:
            request: Must have worktree_path populated.

        Returns:
            Response with exists flag.
        """
        wt_path = request.worktree_path or ""
        found = await self._manager._backend.exists(wt_path)
        return WorktreeRemoteResponse(exists=found)

    async def _ensure_repo(self, repo_url: str) -> str:
        """Clone the repo if not already present on this node.

        Uses a content-addressable directory under ~/.agent_teams/remote_repos/
        so the same URL always maps to the same local path.

        Args:
            repo_url: Git remote URL to clone.

        Returns:
            Local path to the cloned repository root.
        """
        if repo_url in self._cloned_repos:
            return self._cloned_repos[repo_url]

        repo_hash = hashlib.sha256(repo_url.encode()).hexdigest()[:12]
        local_path = os.path.join(
            os.path.expanduser("~"),
            ".agent_teams",
            "remote_repos",
            repo_hash,
        )

        if not os.path.isdir(os.path.join(local_path, ".git")):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            team_logger.info("Cloning %s to %s", repo_url, local_path)
            await _run_git(["clone", "--depth=1", repo_url, local_path], check=True)

        self._cloned_repos[repo_url] = local_path
        return local_path
