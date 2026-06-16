# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Filesystem (jsonl) backend for vcs, isolated per session_id.

Layout under ``<root>/<session_id>/``::

    HEAD                      one JSON line (Head)
    logs/log.jsonl            append-only, one LogEntry per line
    snapshots/<id>.json       one Snapshot per file
    commits/<id>.json         one Commit per file

Mutable writes (HEAD / snapshot / commit / truncated log) go through a temp
file + ``os.replace`` for atomic, cross-platform overwrite. Blocking IO is
wrapped in ``asyncio.to_thread`` and serialized by a per-instance lock
(single-writer per session).
"""
import asyncio
import os
from pathlib import Path

from openjiuwen.core.session.vcs import constants as const
from openjiuwen.core.session.vcs.backend import decode_log_entry, encode_log_entry
from openjiuwen.core.session.vcs.models import Commit, Head, LogEntry, Snapshot


class JsonlBackend:
    """Append-only jsonl log + snapshot/commit/HEAD files under one session dir."""

    def __init__(
        self,
        session_id: str,
        root: str | Path,
        *,
        fsync_policy: str = const.DEFAULT_FSYNC_POLICY,
    ):
        self._dir = Path(root) / session_id
        self._fsync_policy = fsync_policy
        self._lock = asyncio.Lock()

    @property
    def _log_path(self) -> Path:
        return self._dir / const.LOG_DIRNAME / const.LOG_FILENAME

    @property
    def _snapshot_dir(self) -> Path:
        return self._dir / const.SNAPSHOT_DIRNAME

    @property
    def _commit_dir(self) -> Path:
        return self._dir / const.COMMIT_DIRNAME

    @property
    def _head_path(self) -> Path:
        return self._dir / const.HEAD_FILENAME

    async def append_log(self, entry: LogEntry) -> None:
        """Append one WAL entry to the session's log file."""
        line = encode_log_entry(entry)
        async with self._lock:
            await asyncio.to_thread(self._append_line, line)

    def _append_line(self, line: str) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            if self._fsync_policy == const.FSYNC_EACH:
                handle.flush()
                os.fsync(handle.fileno())

    async def read_log(self, *, since_event_id: int = 0) -> list[LogEntry]:
        """Read entries with event_id > since; stop at the first corrupt line."""
        return await asyncio.to_thread(self._read_log, since_event_id)

    def _read_log(self, since_event_id: int) -> list[LogEntry]:
        if not self._log_path.exists():
            return []
        entries: list[LogEntry] = []
        with open(self._log_path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if not line:
                    continue
                entry = decode_log_entry(line)
                if entry is None:
                    break
                if entry.event_id > since_event_id:
                    entries.append(entry)
        return entries

    async def put_snapshot(self, snap: Snapshot) -> None:
        """Persist a snapshot as one JSON file."""
        async with self._lock:
            await asyncio.to_thread(
                self._write_atomic,
                self._snapshot_dir / f"{snap.snapshot_id}.json",
                snap.model_dump_json(),
            )

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Load a snapshot by id, or None if absent."""
        return await asyncio.to_thread(self._load_snapshot, snapshot_id)

    def _load_snapshot(self, snapshot_id: str) -> Snapshot | None:
        path = self._snapshot_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        return Snapshot.model_validate_json(path.read_text(encoding="utf-8"))

    async def latest_snapshot(self, *, at_event_id: int | None = None) -> Snapshot | None:
        """Return the newest snapshot with event_id_high <= at (or overall)."""
        return await asyncio.to_thread(self._latest_snapshot, at_event_id)

    def _latest_snapshot(self, at_event_id: int | None) -> Snapshot | None:
        snaps = self._load_all(self._snapshot_dir, Snapshot)
        candidates = [s for s in snaps if at_event_id is None or s.event_id_high <= at_event_id]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.event_id_high)

    async def put_commit(self, commit: Commit) -> None:
        """Persist a commit as one JSON file."""
        async with self._lock:
            await asyncio.to_thread(
                self._write_atomic,
                self._commit_dir / f"{commit.commit_id}.json",
                commit.model_dump_json(),
            )

    async def get_commit(self, commit_id: str) -> Commit | None:
        """Load a commit by id, or None if absent."""
        return await asyncio.to_thread(self._load_commit, commit_id)

    def _load_commit(self, commit_id: str) -> Commit | None:
        path = self._commit_dir / f"{commit_id}.json"
        if not path.exists():
            return None
        return Commit.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_commits(self) -> list[Commit]:
        """List all commits of this session."""
        return await asyncio.to_thread(self._load_all, self._commit_dir, Commit)

    async def put_head(self, head: Head) -> None:
        """Atomically persist the head pointer."""
        async with self._lock:
            await asyncio.to_thread(self._write_atomic, self._head_path, head.model_dump_json())

    async def get_head(self) -> Head | None:
        """Load the head pointer, or None on first use."""
        return await asyncio.to_thread(self._load_head)

    def _load_head(self) -> Head | None:
        if not self._head_path.exists():
            return None
        return Head.model_validate_json(self._head_path.read_text(encoding="utf-8"))

    async def truncate(self, *, after_event_id: int) -> None:
        """Rewrite log keeping event_id <= after; drop snapshots/commits beyond it."""
        async with self._lock:
            await asyncio.to_thread(self._truncate, after_event_id)

    def _truncate(self, after_event_id: int) -> None:
        if self._log_path.exists():
            kept: list[str] = []
            with open(self._log_path, encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    entry = decode_log_entry(line)
                    if entry is None:
                        break
                    if entry.event_id <= after_event_id:
                        kept.append(line)
            content = ("\n".join(kept) + "\n") if kept else ""
            self._write_atomic(self._log_path, content)
        for snap in self._load_all(self._snapshot_dir, Snapshot):
            if snap.event_id_high > after_event_id:
                (self._snapshot_dir / f"{snap.snapshot_id}.json").unlink(missing_ok=True)
        for commit in self._load_all(self._commit_dir, Commit):
            if commit.event_id_high > after_event_id:
                (self._commit_dir / f"{commit.commit_id}.json").unlink(missing_ok=True)

    @staticmethod
    def _load_all(directory: Path, model: type) -> list:
        if not directory.exists():
            return []
        return [
            model.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
