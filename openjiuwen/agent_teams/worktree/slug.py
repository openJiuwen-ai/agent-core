# coding: utf-8

"""Slug validation and branch naming for worktrees.

Provides safe slug validation (path traversal prevention, length limits)
and deterministic branch/path derivation from slugs.
"""

import os
import re

VALID_SLUG_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
MAX_SLUG_LENGTH = 64


def validate_slug(slug: str) -> None:
    """Validate worktree slug for safety.

    Rejects path traversal, absolute paths, shell metacharacters,
    and overly long names.

    Args:
        slug: The worktree name to validate.

    Raises:
        ValueError: If slug is invalid, with specific reason.
    """
    if len(slug) > MAX_SLUG_LENGTH:
        raise ValueError(
            f"Invalid worktree name: must be {MAX_SLUG_LENGTH} "
            f"characters or fewer (got {len(slug)})"
        )

    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ValueError(
                f'Invalid worktree name "{slug}": '
                f'must not contain "." or ".." path segments'
            )
        if not VALID_SLUG_SEGMENT.match(segment):
            raise ValueError(
                f'Invalid worktree name "{slug}": '
                f"each segment must be non-empty and contain "
                f"only letters, digits, dots, underscores, and dashes"
            )


def worktree_branch_name(slug: str) -> str:
    """Convert slug to git branch name.

    Flattens "/" to "+" to avoid directory/file conflicts
    in git refs namespace.

    Args:
        slug: Validated worktree slug.

    Returns:
        Branch name in the format "worktree-<flattened-slug>".

    Examples:
        "feature-auth"       -> "worktree-feature-auth"
        "user/feature-login" -> "worktree-user+feature-login"
    """
    return f"worktree-{slug.replace('/', '+')}"


def worktree_path_for(base_dir: str, slug: str) -> str:
    """Compute worktree directory path under a base directory.

    Worktrees live in ``{base_dir}/.worktrees/{slug}``.  ``base_dir``
    is normally the owning DeepAgent's workspace root, so each agent's
    worktrees are isolated under its own workspace rather than the
    source git repository.

    Args:
        base_dir: Absolute path to the directory that owns the
            worktrees subtree (typically the DeepAgent workspace root).
        slug: Validated worktree slug.

    Returns:
        Absolute path to the worktree directory.
    """
    return os.path.join(base_dir, ".worktrees", slug)


def worktrees_dir(base_dir: str) -> str:
    """Return the parent directory for all worktrees under ``base_dir``.

    Args:
        base_dir: Absolute path to the directory that owns the
            worktrees subtree (typically the DeepAgent workspace root).

    Returns:
        Absolute path to the worktrees parent directory.
    """
    return os.path.join(base_dir, ".worktrees")
