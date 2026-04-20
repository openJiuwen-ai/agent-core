# coding: utf-8

"""Worktree context notice generation.

Builds informational text injected into the system prompt of spawned
members to inform them about their isolated worktree environment.
"""


def build_worktree_notice(
    parent_cwd: str,
    worktree_cwd: str,
) -> str:
    """Build context notice for agents running in a worktree.

    Injected into the system prompt of spawned members to inform
    them about the isolated environment.

    Args:
        parent_cwd: Working directory of the parent agent.
        worktree_cwd: Working directory of the worktree (isolated copy).

    Returns:
        Multi-line notice string suitable for system prompt injection.
    """
    return (
        f"You are operating in an isolated git worktree at {worktree_cwd}. "
        f"The parent agent works in {parent_cwd} — same repository, "
        f"same relative file structure, separate working copy.\n\n"
        f"Important:\n"
        f"- Paths from the parent context refer to {parent_cwd}\n"
        f"- Translate them to your worktree root before use\n"
        f"- Re-read files before editing if the parent may have modified them\n"
        f"- Your changes stay in this worktree and will not affect the parent"
    )
