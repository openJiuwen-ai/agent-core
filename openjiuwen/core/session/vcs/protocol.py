# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Public ``VersionControl`` protocol for vcs."""
from typing import Protocol, runtime_checkable

from openjiuwen.core.session.vcs.models import Commit, ForkResult, Head


@runtime_checkable
class VersionControl(Protocol):
    """Per-session linear version control.

    Message-level WAL, commit, snapshot, replay and rewind operate on THIS
    session's linear history; ``fork`` clones a brand-new Session for parallel
    use by another agent.
    """

    async def append(self) -> str:
        """Diff current {context,state} vs last-known; append deltas to the WAL; return the event ref."""
        ...

    async def commit(self, message: str = "") -> str:
        """append() then mark a named point on this session's linear history; return the commit_id."""
        ...

    async def snapshot(self) -> str:
        """Persist a full {context,state} snapshot at the current head; return the snapshot_id."""
        ...

    async def restore(self, at: str) -> dict:
        """Rebuild full {context,state} at a commit_id/event ref of this session; read-only, no mutation."""
        ...

    async def rewind(self, at: str) -> dict:
        """Overwrite-rewind this session to `at` (same session_id): truncate after `at`, reload live state."""
        ...

    async def fork(self, *, at: str | None = None) -> ForkResult:
        """Clone a new Session (new session_id) seeded from this session at `at` for parallel use."""
        ...

    async def list_history(self, *, limit: int | None = None) -> list[Commit]:
        """List commits of this session newest-first along parent_id."""
        ...

    def current_head(self) -> Head:
        """Return this session's linear head; in-memory, no IO."""
        ...
