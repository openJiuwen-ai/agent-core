# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Storage backend protocol and shared codec for vcs.

A backend is bound to a single session's space; ``fork`` creates a fresh
backend for the new session_id. All records are JSON (never pickle). A
``LogEntry`` carries a crc32 over its canonical JSON (excluding ``crc``), so
torn / partial writes are detected and stopped at on read.
"""
import json
import zlib
from typing import Protocol, runtime_checkable

from openjiuwen.core.session.vcs.models import Commit, Head, LogEntry, Snapshot


def compute_crc(entry: LogEntry) -> int:
    """Compute crc32 over the entry's canonical JSON excluding the crc field."""
    payload = entry.model_dump(exclude={"crc"})
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return zlib.crc32(canonical.encode("utf-8"))


def encode_log_entry(entry: LogEntry) -> str:
    """Serialize a LogEntry to a single JSON line with a freshly computed crc."""
    stamped = entry.model_copy(update={"crc": compute_crc(entry)})
    return stamped.model_dump_json()


def decode_log_entry(line: str) -> LogEntry | None:
    """Parse one JSON line into a LogEntry, or None if corrupt / crc-mismatched."""
    try:
        entry = LogEntry.model_validate_json(line)
    except ValueError:
        return None
    if entry.crc != compute_crc(entry):
        return None
    return entry


def as_text(value: str | bytes) -> str:
    """Normalize a kv-store value (str or bytes) to text."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


@runtime_checkable
class VersioningBackend(Protocol):
    """Per-session storage primitives: WAL log, snapshots, commits, head."""

    async def append_log(self, entry: LogEntry) -> None:
        """Append one WAL entry to this session's log (durable per fsync policy)."""
        ...

    async def read_log(self, *, since_event_id: int = 0) -> list[LogEntry]:
        """Read entries with event_id > since; stop at the first corrupt entry."""
        ...

    async def put_snapshot(self, snap: Snapshot) -> None:
        """Persist a full-state snapshot."""
        ...

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Load a snapshot by id, or None if absent."""
        ...

    async def latest_snapshot(self, *, at_event_id: int | None = None) -> Snapshot | None:
        """Return the snapshot with the greatest event_id_high <= at (or overall)."""
        ...

    async def put_commit(self, commit: Commit) -> None:
        """Persist an immutable commit object."""
        ...

    async def get_commit(self, commit_id: str) -> Commit | None:
        """Load a commit by id, or None if absent."""
        ...

    async def list_commits(self) -> list[Commit]:
        """List all commit objects of this session."""
        ...

    async def put_head(self, head: Head) -> None:
        """Atomically persist this session's head pointer."""
        ...

    async def get_head(self) -> Head | None:
        """Load this session's head pointer, or None on first use."""
        ...

    async def truncate(self, *, after_event_id: int) -> None:
        """Drop log entries with event_id > after and snapshots/commits beyond it."""
        ...
