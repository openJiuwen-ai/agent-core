# coding: utf-8

"""Worktree backend protocol and git implementation.

Pluggable backend for worktree creation and removal. The default
``GitBackend`` uses native git worktree commands with a four-phase
creation flow: fast recovery, base resolution, worktree add, and
optional sparse checkout.

Custom backends (e.g. distributed cross-machine isolation) are
registered via :func:`register_worktree_backend` and selected by
name through :func:`create_backend`.
"""

import os
from typing import Callable, Protocol, runtime_checkable

from openjiuwen.harness.tools.worktree.git import (
    GitError,
    branch_delete,
    create_empty_initial_commit,
    fetch_ref,
    get_current_branch,
    get_default_branch,
    read_worktree_head_sha,
    rev_parse,
    set_longpaths,
    sparse_checkout_set,
    worktree_add,
    worktree_remove,
)
from openjiuwen.harness.tools.worktree.models import WorktreeConfig, WorktreeCreateResult
from openjiuwen.harness.tools.worktree.slug import worktree_branch_name

# Conventional default branch names, probed in this order.
_DEFAULT_BRANCH_NAMES = ("main", "master", "develop")


@runtime_checkable
class WorktreeBackend(Protocol):
    """Pluggable backend for worktree creation/removal.

    Default implementation uses native git worktree commands.
    Custom implementations can wrap other VCS systems or
    cloud-based isolation (containers, VMs).
    """

    async def create(
        self,
        slug: str,
        repo_root: str,
        target_path: str,
    ) -> WorktreeCreateResult:
        """Create a worktree for the given slug.

        Args:
            slug: Validated worktree name.
            repo_root: Repository root directory (used for git commands).
            target_path: Absolute filesystem path where the worktree
                should be created.  The caller (typically
                ``WorktreeManager``) is responsible for resolving this
                from the agent workspace.

        Returns:
            WorktreeCreateResult with path and metadata.
        """
        ...

    async def remove(
        self,
        worktree_path: str,
        repo_root: str,
        *,
        force: bool = False,
    ) -> bool:
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
    2. Base resolution (local-first; remote-first on remote worktree nodes)
    3. git worktree add -B
    4. Optional sparse checkout
    """

    def __init__(self, config: WorktreeConfig | None = None, *, remote_base: bool = False):
        """Create a git worktree backend.

        Args:
            config: Worktree configuration.
            remote_base: Resolve the base from ``origin/<default>`` (fetching
                it when missing) instead of local branches. Only for a remote
                worktree node, whose framework-owned clone never advances its
                local branches.
        """
        self._config = config or WorktreeConfig()
        self.remote_base = remote_base

    async def create(
        self,
        slug: str,
        repo_root: str,
        target_path: str,
    ) -> WorktreeCreateResult:
        """Create or recover a worktree at ``target_path``.

        Args:
            slug: Validated worktree name.
            repo_root: Repository root directory (used for git commands).
            target_path: Absolute filesystem path where the worktree
                should live.  Resolved by the caller from the DeepAgent
                workspace, not derived from ``repo_root``.

        Returns:
            WorktreeCreateResult with path, branch, and metadata.
        """
        wt_path = target_path
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

        # Phase 1b: enable Windows long-path support before checkout (rc=128).
        if self._config.longpaths_support:
            await set_longpaths(repo_root, enabled=True)

        # Phase 2: resolve base branch
        os.makedirs(os.path.dirname(wt_path), exist_ok=True)
        base_branch, base_sha = await self._resolve_base(repo_root)

        # Phase 3: create worktree
        sparse = self._config.sparse_paths
        await worktree_add(
            repo_root,
            wt_path,
            wt_branch,
            base_sha or base_branch,
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

    async def remove(
        self,
        worktree_path: str,
        repo_root: str,
        *,
        force: bool = False,
    ) -> bool:
        """Remove a worktree and its branch.

        Args:
            worktree_path: Absolute path to the worktree.
            repo_root: Repository root directory.

        Returns:
            True if removal succeeded.
        """
        branch = await get_current_branch(worktree_path)
        ok = await worktree_remove(worktree_path, repo_root=repo_root, force=force)
        if ok and branch and branch.startswith("worktree-"):
            await branch_delete(branch, repo_root, force=force)
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
        """Resolve the base ref and SHA for a new worktree, local state first.

        Order: the checked-out local branch, then a local default branch
        (main/master/develop), then an existing ``origin/<default>`` ref, then
        a detached HEAD. No fetch: the worktree branches from what the user
        has locally, unpushed commits included. Remote worktree nodes use
        :meth:`_resolve_remote_base` instead.

        Args:
            repo_root: Repository root directory.

        Returns:
            Tuple of (base_ref, sha_or_none).
        """
        if self.remote_base:
            return await self._resolve_remote_base(repo_root)

        current = await get_current_branch(repo_root)
        local_names = dict.fromkeys(name for name in (current, *_DEFAULT_BRANCH_NAMES) if name)
        candidates = [(name, f"refs/heads/{name}") for name in local_names]
        candidates += [(f"origin/{name}", f"refs/remotes/origin/{name}") for name in _DEFAULT_BRANCH_NAMES]
        for display, ref in candidates:
            sha = await rev_parse(f"{ref}^{{commit}}", repo_root)
            if sha:
                return display, sha

        sha = await rev_parse("HEAD", repo_root)
        if sha:
            return "HEAD", sha

        # Unborn repository: create a real empty base commit so git worktree can
        # branch from HEAD without consuming staged or untracked user files.
        sha = await create_empty_initial_commit(repo_root)
        return "HEAD", sha

    async def _resolve_remote_base(self, repo_root: str) -> tuple[str, str | None]:
        """Resolve the base from the remote default branch (remote worktree nodes).

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
            if sha:
                return origin_ref, sha

        # Last resort: use current HEAD
        sha = await rev_parse("HEAD", repo_root)
        if sha:
            return "HEAD", sha

        # Unborn repository: create a real empty base commit so git worktree can
        # branch from HEAD without consuming staged or untracked user files.
        sha = await create_empty_initial_commit(repo_root)
        return "HEAD", sha


# -- Backend registry ---------------------------------------------------------

# Built-in backends are populated below. Backends that need extra
# constructor arguments (e.g. ``RemoteWorktreeBackend`` in
# ``agent_teams.worktree_remote``, which takes a messager and node id)
# are not exposed through ``create_backend``; callers instantiate them
# directly and pass them as ``WorktreeManager(backend=...)``.
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
) -> WorktreeBackend:
    """Create a worktree backend by name.

    Args:
        name: Backend identifier (default "git").
        config: Worktree configuration to pass to the backend.

    Returns:
        A WorktreeBackend instance.

    Raises:
        ValueError: If the backend name is not registered.
    """
    factory = _BACKEND_REGISTRY.get(name)
    if not factory:
        raise ValueError(f"Unknown worktree backend '{name}'. Available: {list(_BACKEND_REGISTRY)}")
    return factory(config)
