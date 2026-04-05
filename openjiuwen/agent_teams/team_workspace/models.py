# coding: utf-8

"""Team workspace data models.

Pydantic models for team shared workspace configuration, file locks,
operating modes, and conflict strategies. These models define the
per-team lifecycle workspace that is independent of git worktree isolation.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field


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
