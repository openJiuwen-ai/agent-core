# coding: utf-8

"""Stale worktree cleanup.

Identifies and removes expired ephemeral worktrees using fail-closed
safety checks: only removes worktrees matching ephemeral naming patterns,
with no uncommitted changes and no unpushed commits.
"""

import asyncio
import os
import re
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from openjiuwen.agent_teams.worktree.backend import WorktreeBackend
from openjiuwen.agent_teams.worktree.git import (
    find_canonical_git_root,
    has_unpushed_commits,
    status_porcelain,
    worktree_prune,
)
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from openjiuwen.agent_teams.worktree.slug import worktrees_dir

EPHEMERAL_PATTERNS: list[re.Pattern[str]] = [
    # teammate-<member_name first 8 hex chars>
    re.compile(r"^teammate-[0-9a-f]{8}$"),
    # agent-<7hex> (legacy compatibility)
    re.compile(r"^agent-[0-9a-f]{7}$"),
]


def is_ephemeral_slug(slug: str) -> bool:
    """Check if a slug matches ephemeral worktree naming patterns.

    Args:
        slug: Worktree slug to check.

    Returns:
        True if the slug matches any ephemeral pattern.
    """
    return any(p.match(slug) for p in EPHEMERAL_PATTERNS)


async def cleanup_stale_worktrees(
    config: WorktreeConfig,
    backend: WorktreeBackend,
    *,
    current_worktree_path: str | None = None,
) -> int:
    """Clean up expired ephemeral worktrees.

    Safety strategy (fail-closed):
    1. Only clean worktrees matching ephemeral patterns
    2. Skip the current session's worktree
    3. Check for uncommitted changes (git status)
    4. Check for unpushed commits (git rev-list)
    5. Skip on any check failure

    Args:
        config: Worktree configuration with cleanup_after_days.
        backend: Backend used to remove worktrees.
        current_worktree_path: Path of the active worktree to skip.

    Returns:
        Number of worktrees removed.
    """
    from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace
    repo_root = await find_canonical_git_root(get_cwd())
    if not repo_root:
        return 0

    workspace = get_workspace()
    if workspace is None:
        # Without an agent workspace we can't locate the worktrees
        # subtree -- skip cleanup rather than guessing.
        return 0
    wt_dir = worktrees_dir(workspace)
    try:
        entries = os.listdir(wt_dir)
    except FileNotFoundError:
        return 0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.cleanup_after_days)
    cutoff_ts = cutoff.timestamp()
    removed = 0

    for slug in entries:
        if not is_ephemeral_slug(slug):
            continue

        wt_path = os.path.join(wt_dir, slug)

        if current_worktree_path and wt_path == current_worktree_path:
            continue

        try:
            mtime = os.stat(wt_path).st_mtime
        except OSError:
            continue

        if mtime >= cutoff_ts:
            continue

        # Parallel safety checks
        changes, unpushed = await asyncio.gather(
            status_porcelain(wt_path),
            has_unpushed_commits(wt_path),
        )

        # Fail-closed: skip if any check fails or finds changes
        if changes:
            continue
        if unpushed is None or unpushed:
            continue

        if await backend.remove(wt_path, repo_root):
            removed += 1

    if removed > 0:
        await worktree_prune(repo_root)

    return removed
