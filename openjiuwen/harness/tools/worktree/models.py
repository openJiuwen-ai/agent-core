# coding: utf-8

"""Worktree data models.

Pydantic models for worktree configuration, session state, creation results,
and change summaries.

Owner identification fields (``member_name`` / ``team_name``) are kept for
backwards compatibility with existing persistence and tests; semantically
they are generic ``owner_id`` / ``tag`` markers and may be left ``None`` by
single-agent callers.
"""

from enum import Enum

from pydantic import BaseModel


class WorktreeLifecyclePolicy(str, Enum):
    """How worktree lifecycle binds to its owner's lifecycle."""

    AUTO = "auto"
    """Infer from owner lifecycle (temporary -> ephemeral, persistent -> durable)."""

    EPHEMERAL = "ephemeral"
    """Always auto-cleanup on owner shutdown."""

    DURABLE = "durable"
    """Always preserve across sessions."""


class WorktreeConfig(BaseModel):
    """Worktree configuration for a managed worktree owner.

    Controls how worktrees are created and managed. Used by both team
    members (via ``TeamAgentSpec.worktree``) and single agents (passed
    directly to ``WorktreeManager``).
    """

    enabled: bool = False
    """Enable worktree isolation."""

    base_dir: str | None = None
    """Override worktree root directory.
    Default: <workspace-root>/.worktrees/
    """

    sparse_paths: list[str] | None = None
    """Sparse checkout paths for large repos.
    Only these directories are checked out, reducing disk usage.
    Example: ["src/", "pyproject.toml", "tests/"]
    """

    symlink_directories: list[str] | None = None
    """Directories to symlink from main repo instead of copying.
    Saves disk space for heavy directories.
    Example: [".venv", "node_modules", ".tox"]
    """

    include_patterns: list[str] | None = None
    """Gitignored file patterns to copy into worktree.
    Uses .gitignore syntax. Replaces .worktreeinclude file.
    Example: [".env.local", "config/secrets/"]
    """

    cleanup_after_days: int = 30
    """Auto-cleanup ephemeral worktrees older than this many days."""

    auto_cleanup_on_shutdown: bool = True
    """Automatically remove worktree when owner shuts down cleanly."""

    lifecycle_policy: WorktreeLifecyclePolicy = WorktreeLifecyclePolicy.AUTO
    """How worktree lifecycle binds to its owner's lifecycle.
    AUTO: infer from owner context (temporary -> ephemeral, persistent -> durable).
    EPHEMERAL: always auto-cleanup on owner shutdown.
    DURABLE: always preserve across sessions.
    """


class WorktreeSession(BaseModel):
    """Runtime state of an active worktree.

    Persisted fields survive process restarts (via event log or DB).
    Transient fields are recomputed on recovery.
    """

    # === Persisted fields ===
    original_cwd: str
    """Original working directory before entering worktree."""

    worktree_path: str
    """Absolute path to the worktree directory."""

    worktree_name: str
    """Slug identifier for this worktree."""

    worktree_branch: str | None = None
    """Git branch name. Format: worktree-<flattened-slug>."""

    original_branch: str | None = None
    """Branch that was checked out before worktree creation."""

    original_head_commit: str | None = None
    """Base commit SHA at creation time. Used for change detection."""

    member_name: str | None = None
    """Owner identifier (team member name, or any caller-defined owner id)."""

    team_name: str | None = None
    """Owner tag (team name, or any caller-defined grouping label)."""

    hook_based: bool = False
    """True if created via WorktreeBackend hook, not native git."""

    lifecycle_policy: WorktreeLifecyclePolicy = WorktreeLifecyclePolicy.AUTO
    """Resolved lifecycle policy for this worktree."""

    team_lifecycle: str | None = None
    """Owner lifecycle marker at creation time (temporary/persistent)."""

    # === Transient fields (not persisted) ===
    creation_duration_ms: float | None = None
    """Time spent creating this worktree, for performance tracking."""

    used_sparse_paths: bool = False
    """Whether sparse checkout was used."""


class WorktreeCreateResult(BaseModel):
    """Result of worktree creation."""

    worktree_path: str
    """Absolute path to the created worktree."""

    worktree_branch: str | None = None
    """Git branch name for the worktree."""

    head_commit: str | None = None
    """HEAD commit SHA at creation time."""

    base_branch: str | None = None
    """Base branch the worktree was created from."""

    existed: bool = False
    """True if worktree already existed (fast recovery path)."""

    hook_based: bool = False
    """True if created via external backend hook."""


class WorktreeChangeSummary(BaseModel):
    """Summary of uncommitted/unpushed changes in a worktree."""

    changed_files: int = 0
    """Number of uncommitted file changes."""

    commits: int = 0
    """Number of commits since original_head_commit."""
