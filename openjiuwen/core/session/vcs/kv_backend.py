# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""KV-store backend for vcs, reusing an injected ``BaseKVStore``.

Isolated per session_id via the key prefix ``{session_id}:vcs:{kind}:{...}``.
Log keys zero-pad the event_id to a fixed width so lexical order matches
numeric order, making ``get_by_prefix`` return entries already sorted.
"""
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.session.vcs import constants as const
from openjiuwen.core.session.vcs.backend import (
    as_text,
    decode_log_entry,
    encode_log_entry,
)
from openjiuwen.core.session.vcs.models import Commit, Head, LogEntry, Snapshot


class KvBackend:
    """vcs storage over a BaseKVStore, isolated under a per-session key prefix."""

    def __init__(self, session_id: str, kv_store: BaseKVStore):
        self._base = f"{session_id}:{const.VCS_NAMESPACE}"
        self._kv = kv_store

    def _log_key(self, event_id: int) -> str:
        return f"{self._base}:{const.KV_LOG_PREFIX}:{event_id:0{const.EVENT_ID_WIDTH}d}"

    @property
    def _log_prefix(self) -> str:
        return f"{self._base}:{const.KV_LOG_PREFIX}:"

    @property
    def _snap_prefix(self) -> str:
        return f"{self._base}:{const.KV_SNAPSHOT_PREFIX}:"

    @property
    def _commit_prefix(self) -> str:
        return f"{self._base}:{const.KV_COMMIT_PREFIX}:"

    @property
    def _head_key(self) -> str:
        return f"{self._base}:{const.KV_HEAD_SUFFIX}"

    async def append_log(self, entry: LogEntry) -> None:
        """Write one WAL entry under its zero-padded event_id key."""
        await self._kv.set(self._log_key(entry.event_id), encode_log_entry(entry))

    async def read_log(self, *, since_event_id: int = 0) -> list[LogEntry]:
        """Read entries with event_id > since; stop at the first corrupt entry."""
        items = await self._kv.get_by_prefix(self._log_prefix)
        entries: list[LogEntry] = []
        for key in sorted(items.keys()):
            entry = decode_log_entry(as_text(items[key]))
            if entry is None:
                break
            if entry.event_id > since_event_id:
                entries.append(entry)
        return entries

    async def put_snapshot(self, snap: Snapshot) -> None:
        """Persist a snapshot under its id key."""
        await self._kv.set(f"{self._snap_prefix}{snap.snapshot_id}", snap.model_dump_json())

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Load a snapshot by id, or None if absent."""
        value = await self._kv.get(f"{self._snap_prefix}{snapshot_id}")
        if value is None:
            return None
        return Snapshot.model_validate_json(as_text(value))

    async def latest_snapshot(self, *, at_event_id: int | None = None) -> Snapshot | None:
        """Return the newest snapshot with event_id_high <= at (or overall)."""
        items = await self._kv.get_by_prefix(self._snap_prefix)
        candidates: list[Snapshot] = []
        for value in items.values():
            snap = Snapshot.model_validate_json(as_text(value))
            if at_event_id is None or snap.event_id_high <= at_event_id:
                candidates.append(snap)
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.event_id_high)

    async def put_commit(self, commit: Commit) -> None:
        """Persist a commit under its id key."""
        await self._kv.set(f"{self._commit_prefix}{commit.commit_id}", commit.model_dump_json())

    async def get_commit(self, commit_id: str) -> Commit | None:
        """Load a commit by id, or None if absent."""
        value = await self._kv.get(f"{self._commit_prefix}{commit_id}")
        if value is None:
            return None
        return Commit.model_validate_json(as_text(value))

    async def list_commits(self) -> list[Commit]:
        """List all commits of this session."""
        items = await self._kv.get_by_prefix(self._commit_prefix)
        return [Commit.model_validate_json(as_text(v)) for v in items.values()]

    async def put_head(self, head: Head) -> None:
        """Persist the head pointer."""
        await self._kv.set(self._head_key, head.model_dump_json())

    async def get_head(self) -> Head | None:
        """Load the head pointer, or None on first use."""
        value = await self._kv.get(self._head_key)
        if value is None:
            return None
        return Head.model_validate_json(as_text(value))

    async def truncate(self, *, after_event_id: int) -> None:
        """Delete log entries with event_id > after and snapshots/commits beyond it."""
        to_delete: list[str] = []
        log_items = await self._kv.get_by_prefix(self._log_prefix)
        for key, value in log_items.items():
            entry = decode_log_entry(as_text(value))
            if entry is not None and entry.event_id > after_event_id:
                to_delete.append(key)
        snap_items = await self._kv.get_by_prefix(self._snap_prefix)
        for key, value in snap_items.items():
            snap = Snapshot.model_validate_json(as_text(value))
            if snap.event_id_high > after_event_id:
                to_delete.append(key)
        commit_items = await self._kv.get_by_prefix(self._commit_prefix)
        for key, value in commit_items.items():
            commit = Commit.model_validate_json(as_text(value))
            if commit.event_id_high > after_event_id:
                to_delete.append(key)
        if to_delete:
            await self._kv.batch_delete(to_delete)
