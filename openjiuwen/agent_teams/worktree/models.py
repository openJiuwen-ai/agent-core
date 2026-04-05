# coding: utf-8

"""Worktree data models.

Pydantic models for worktree configuration, session state, creation results,
change summaries, and team workspace configuration.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field


class WorktreeLifecyclePolicy(str, Enum):
    """How worktree lifecycle binds to team lifecycle."""

    AUTO = "auto"
    """Infer from TeamLifecycle (temporary -> ephemeral, persistent -> durable)."""

    EPHEMERAL = "ephemeral"
    """Always auto-cleanup on member shutdown."""

    DURABLE = "durable"
    """Always preserve across sessions."""


class WorktreeConfig(BaseModel):
    """Worktree configuration declared in TeamAgentSpec.

    Controls how worktrees are created and managed for team members.
    Mirrors the settings.json worktree section from Claude Code.
    """

    enabled: bool = False
    """Enable worktree isolation for team members."""

    base_dir: str | None = None
    """Override worktree root directory.
    Default: <repo-root>/.agent_teams/worktrees/
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
    """Automatically remove worktree when member shuts down cleanly."""

    lifecycle_policy: WorktreeLifecyclePolicy = WorktreeLifecyclePolicy.AUTO
    """How worktree lifecycle binds to team lifecycle.
    AUTO: infer from TeamLifecycle (temporary -> ephemeral, persistent -> durable).
    EPHEMERAL: always auto-cleanup on member shutdown.
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

    member_id: str | None = None
    """Team member this worktree belongs to."""

    team_id: str | None = None
    """Team this worktree belongs to."""

    hook_based: bool = False
    """True if created via WorktreeBackend hook, not native git."""

    lifecycle_policy: WorktreeLifecyclePolicy = WorktreeLifecyclePolicy.AUTO
    """Resolved lifecycle policy for this worktree."""

    team_lifecycle: str | None = None
    """Team lifecycle at creation time (temporary/persistent)."""

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


class WorkspaceMode(str, Enum):
    """Team workspace operating mode."""

    LOCAL = "local"
    """Single machine or shared filesystem. Symlink mount, in-memory locks."""

    DISTRIBUTED = "distributed"
    """Each node has independent clone. Git push/pull sync, leader-coordinated locks."""


class ConflictStrategy(str, Enum):
    """Strategy for handling concurrent modifications in shared workspace."""

    LOCK = "lock"
    """File-level locking: only one member can write at a time.
    LOCAL: in-memory mutex. DISTRIBUTED: leader-coordinated via Messager."""

    MERGE = "merge"
    """Git-based: concurrent writes auto-committed, conflicts reported.
    DISTRIBUTED: push/pull with rebase, conflict events on failure."""

    LAST_WRITE_WINS = "last_write_wins"
    """No conflict detection: last write overwrites.
    DISTRIBUTED: push --force (dangerous, use only for append-only logs)."""


class TeamWorkspaceConfig(BaseModel):
    """Configuration for team shared workspace."""

    enabled: bool = False
    """Enable shared workspace for the team."""

    artifact_dirs: list[str] = Field(
        default=["artifacts/code", "artifacts/docs", "artifacts/reports"],
    )
    """Pre-created artifact directories."""

    version_control: bool = True
    """Enable git version control for workspace contents."""

    conflict_strategy: ConflictStrategy = ConflictStrategy.LOCK
    """Strategy for handling concurrent modifications."""

    remote_url: str | None = None
    """Git remote URL for workspace repo in distributed mode.
    If set, workspace operates in DISTRIBUTED mode.
    If None, auto-detected: DISTRIBUTED when Messager is pyzmq, else LOCAL.
    """


class WorkspaceFileLock(BaseModel):
    """File-level lock entry for team shared workspace."""

    file_path: str = Field(..., description="Locked file path relative to workspace")
    holder_id: str = Field(..., description="Member ID holding the lock")
    holder_name: str = Field(..., description="Display name of lock holder")
    acquired_at: str = Field(..., description="ISO 8601 timestamp when lock was acquired")
    timeout_seconds: int = Field(default=300, description="Lock timeout in seconds")

    def is_expired(self) -> bool:
        """Check whether this lock has exceeded its timeout."""
        acquired = datetime.fromisoformat(self.acquired_at)
        return datetime.now(timezone.utc) > acquired + timedelta(
            seconds=self.timeout_seconds,
        )
