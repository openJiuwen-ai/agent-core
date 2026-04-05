# coding: utf-8

"""Worktree backend protocol and git implementation.

Pluggable backend for worktree creation and removal. The default
GitBackend uses native git worktree commands with a four-phase
creation flow: fast recovery, base resolution, worktree add, and
optional sparse checkout.
"""

import os
from typing import Any, Callable, Protocol, runtime_checkable

from openjiuwen.agent_teams.worktree.git import (
    GitError,
    branch_delete,
    fetch_ref,
    get_current_branch,
    get_default_branch,
    read_worktree_head_sha,
    rev_parse,
    sparse_checkout_set,
    worktree_add,
    worktree_remove,
)
from openjiuwen.agent_teams.worktree.models import WorktreeConfig, WorktreeCreateResult
from openjiuwen.agent_teams.worktree.slug import worktree_branch_name, worktree_path_for, worktrees_dir


@runtime_checkable
class WorktreeBackend(Protocol):
    """Pluggable backend for worktree creation/removal.

    Default implementation uses native git worktree commands.
    Custom implementations can wrap other VCS systems or
    cloud-based isolation (containers, VMs).
    """

    async def create(self, slug: str, repo_root: str) -> WorktreeCreateResult:
        """Create a worktree for the given slug.

        Args:
            slug: Validated worktree name.
            repo_root: Repository root directory.

        Returns:
            WorktreeCreateResult with path and metadata.
        """
        ...

    async def remove(self, worktree_path: str, repo_root: str) -> bool:
        """Remove a worktree.

        Args:
            worktree_path: Absolute path to the worktree.
            repo_root: Repository root directory.

        Returns:
            True if removal succeeded.
        """
        ...

    async def exists(self, worktree_path: str) -> bool:
        """Check if a worktree exists and is valid.

        Args:
            worktree_path: Absolute path to the worktree.

        Returns:
            True if worktree exists and has a valid HEAD.
        """
        ...


class GitBackend:
    """Native git worktree backend.

    Implements the full creation flow:
    1. Fast recovery check (read HEAD without subprocess)
    2. Conditional fetch (skip if origin ref exists locally)
    3. git worktree add -B
    4. Optional sparse checkout
    """

    def __init__(self, config: WorktreeConfig | None = None):
        self._config = config or WorktreeConfig()

    async def create(self, slug: str, repo_root: str) -> WorktreeCreateResult:
        """Create or recover a worktree.

        Args:
            slug: Validated worktree name.
            repo_root: Repository root directory.

        Returns:
            WorktreeCreateResult with path, branch, and metadata.
        """
        wt_path = worktree_path_for(repo_root, slug)
        wt_branch = worktree_branch_name(slug)

        # Phase 1: fast recovery -- worktree already exists
        existing_head = await read_worktree_head_sha(wt_path)
        if existing_head:
            return WorktreeCreateResult(
                worktree_path=wt_path,
                worktree_branch=wt_branch,
                head_commit=existing_head,
                existed=True,
            )

        # Phase 2: resolve base branch
        os.makedirs(worktrees_dir(repo_root), exist_ok=True)
        base_branch, base_sha = await self._resolve_base(repo_root)

        # Phase 3: create worktree
        sparse = self._config.sparse_paths
        await worktree_add(
            repo_root,
            wt_path,
            wt_branch,
            base_branch,
            no_checkout=bool(sparse),
        )

        # Phase 4: sparse checkout (optional, rollback on failure)
        if sparse:
            try:
                await sparse_checkout_set(wt_path, sparse)
            except GitError as e:
                await worktree_remove(wt_path, repo_root=repo_root, force=True)
                raise GitError(
                    ["sparse-checkout"],
                    e.returncode,
                    f"Failed sparse checkout, worktree cleaned up: {e.stderr}",
                ) from e

        if not base_sha:
            base_sha = await rev_parse("HEAD", wt_path)

        return WorktreeCreateResult(
            worktree_path=wt_path,
            worktree_branch=wt_branch,
            head_commit=base_sha,
            base_branch=base_branch,
            existed=False,
        )

    async def remove(self, worktree_path: str, repo_root: str) -> bool:
        """Remove a worktree and its branch.

        Args:
            worktree_path: Absolute path to the worktree.
            repo_root: Repository root directory.

        Returns:
            True if removal succeeded.
        """
        branch = await get_current_branch(worktree_path)
        ok = await worktree_remove(worktree_path, repo_root=repo_root, force=True)
        if ok and branch and branch.startswith("worktree-"):
            await branch_delete(branch, repo_root)
        return ok

    async def exists(self, worktree_path: str) -> bool:
        """Check if a worktree exists via fast HEAD read.

        Args:
            worktree_path: Absolute path to the worktree.

        Returns:
            True if worktree has a valid HEAD.
        """
        return await read_worktree_head_sha(worktree_path) is not None

    async def _resolve_base(self, repo_root: str) -> tuple[str, str | None]:
        """Resolve the base branch and SHA for new worktree.

        Optimization: skip fetch if origin/<default> already exists locally.
        Saves 6-8s on large repos.

        Args:
            repo_root: Repository root directory.

        Returns:
            Tuple of (base_ref, sha_or_none).
        """
        default_branch = await get_default_branch(repo_root)
        origin_ref = f"origin/{default_branch}"

        # Try local resolution first
        sha = await rev_parse(origin_ref, repo_root)
        if sha:
            return origin_ref, sha

        # Fetch from remote
        fetched = await fetch_ref(repo_root, default_branch)
        if fetched:
            sha = await rev_parse(origin_ref, repo_root)
            return origin_ref, sha

        # Last resort: use current HEAD
        sha = await rev_parse("HEAD", repo_root)
        return "HEAD", sha


# -- Backend registry ---------------------------------------------------------

_BACKEND_REGISTRY: dict[str, Callable[..., WorktreeBackend]] = {
    "git": GitBackend,
}


def register_worktree_backend(
    name: str,
    factory: Callable[..., WorktreeBackend],
) -> None:
    """Register a custom worktree backend.

    Args:
        name: Backend identifier for lookup.
        factory: Callable that accepts a WorktreeConfig and returns
            a WorktreeBackend instance.
    """
    _BACKEND_REGISTRY[name] = factory


def create_backend(
    name: str = "git",
    config: WorktreeConfig | None = None,
    *,
    messager: Any = None,
    node_id: str | None = None,
) -> WorktreeBackend:
    """Create a worktree backend by name.

    When *messager* and *node_id* are provided (or *name* is ``"remote"``),
    a :class:`RemoteWorktreeBackend` is returned instead of looking up the
    registry.

    Args:
        name: Backend identifier (default "git").
        config: Worktree configuration to pass to the backend.
        messager: Optional Messager instance for remote communication.
        node_id: Optional target node identifier for the remote backend.

    Returns:
        A WorktreeBackend instance.

    Raises:
        ValueError: If the backend name is not registered.
    """
    if name == "remote" or (messager and node_id):
        from openjiuwen.agent_teams.worktree.remote import RemoteWorktreeBackend

        return RemoteWorktreeBackend(config or WorktreeConfig(), messager, node_id or "")

    factory = _BACKEND_REGISTRY.get(name)
    if not factory:
        raise ValueError(
            f"Unknown worktree backend '{name}'. "
            f"Available: {list(_BACKEND_REGISTRY)}"
        )
    return factory(config)
