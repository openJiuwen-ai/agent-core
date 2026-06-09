# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""VersioningManager: per-session linear version control over a backend.

The manager is storage- and session-agnostic: it talks to a
``VersioningBackend`` plus a few injected callbacks, so it can be unit-tested
with in-memory stand-ins. ``for_session`` (in :mod:`adapter`) wires those
callbacks to a real Session + ContextEngine.

Injected callbacks:
- ``snapshot_provider() -> {"context": {...}, "state": {...}}`` — the live,
  json-native snapshot of the session.
- ``applier(snapshot) -> None`` — apply a ``{context, state}`` snapshot back to
  the live session (used by rewind).
- ``forker(new_id, seed, forked_from) -> ForkResult`` — derive a new Session
  seeded from ``seed`` (optional; fork raises without it).
"""
import time
import uuid
from copy import deepcopy
from typing import Awaitable, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.vcs import constants as const
from openjiuwen.core.session.vcs.backend import VersioningBackend
from openjiuwen.core.session.vcs.delta import (
    apply_context,
    apply_state,
    diff_context,
    diff_state,
)
from openjiuwen.core.session.vcs.models import Commit, ForkResult, Head, LogEntry, Snapshot

SnapshotProvider = Callable[[], Awaitable[dict]]
SnapshotApplier = Callable[[dict], Awaitable[None]]
Forker = Callable[[str, dict, tuple[str, str]], Awaitable[ForkResult]]


def _default_id() -> str:
    return uuid.uuid4().hex


class VersioningManager:
    """Implements the VersionControl protocol for one session's linear history."""

    def __init__(
        self,
        session_id: str,
        backend: VersioningBackend,
        *,
        snapshot_provider: SnapshotProvider,
        applier: SnapshotApplier,
        forker: Forker | None = None,
        snapshot_every: int = const.DEFAULT_SNAPSHOT_EVERY,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = _default_id,
    ):
        self._session_id = session_id
        self._backend = backend
        self._snapshot_provider = snapshot_provider
        self._applier = applier
        self._forker = forker
        self._snapshot_every = snapshot_every
        self._clock = clock
        self._id_factory = id_factory
        self._head = Head()
        self._last: dict = {"context": {}, "state": {}}
        self._appends_since_snapshot = 0
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        head = await self._backend.get_head()
        if head is not None:
            self._head = head
            self._last = await self._restore_at_event(head.event_id)
        self._loaded = True

    def current_head(self) -> Head:
        """Return this session's linear head; in-memory, no IO."""
        return self._head

    async def append(self) -> str:
        """Diff the live snapshot vs last-known and append the resulting deltas."""
        await self._ensure_loaded()
        current = await self._snapshot_provider()
        context_deltas = diff_context(self._last.get("context", {}), current.get("context", {}))
        state_delta = diff_state(self._last.get("state", {}), current.get("state", {}))
        if not context_deltas and not state_delta.set and not state_delta.removed:
            return f"{const.EVENT_REF_PREFIX}{self._head.event_id}"
        event_id = self._head.event_id + 1
        entry = LogEntry(event_id=event_id, context=context_deltas, state=state_delta, ts=self._clock())
        await self._backend.append_log(entry)
        self._head = self._head.model_copy(update={"event_id": event_id})
        await self._backend.put_head(self._head)
        self._last = deepcopy(current)
        self._appends_since_snapshot += 1
        if 0 < self._snapshot_every <= self._appends_since_snapshot:
            await self.snapshot()
        return f"{const.EVENT_REF_PREFIX}{event_id}"

    async def commit(self, message: str = "") -> str:
        """Append pending changes then record a named commit on the linear chain."""
        await self._ensure_loaded()
        await self.append()
        commit_id = self._id_factory()
        commit = Commit(
            commit_id=commit_id,
            parent_id=self._head.commit_id,
            event_id_high=self._head.event_id,
            message=message,
            ts=self._clock(),
        )
        await self._backend.put_commit(commit)
        self._head = self._head.model_copy(update={"commit_id": commit_id})
        await self._backend.put_head(self._head)
        return commit_id

    async def snapshot(self) -> str:
        """Persist a full snapshot of the head state to speed future replay."""
        await self._ensure_loaded()
        snap = Snapshot(
            snapshot_id=self._id_factory(),
            event_id_high=self._head.event_id,
            context=deepcopy(self._last.get("context", {})),
            state=deepcopy(self._last.get("state", {})),
            ts=self._clock(),
        )
        await self._backend.put_snapshot(snap)
        self._appends_since_snapshot = 0
        return snap.snapshot_id

    async def restore(self, at: str) -> dict:
        """Rebuild the full {context, state} at a commit_id / event ref; read-only."""
        await self._ensure_loaded()
        target = await self._resolve_event_id(at)
        return await self._restore_at_event(target)

    async def _restore_at_event(self, target: int) -> dict:
        snap = await self._backend.latest_snapshot(at_event_id=target)
        if snap is not None:
            context = deepcopy(snap.context)
            state = deepcopy(snap.state)
            since = snap.event_id_high
        else:
            context, state, since = {}, {}, 0
        for entry in await self._backend.read_log(since_event_id=since):
            if entry.event_id > target:
                break
            context = apply_context(context, entry.context)
            state = apply_state(state, entry.state)
        return {"context": context, "state": state}

    async def _resolve_event_id(self, at: str) -> int:
        suffix = at[len(const.EVENT_REF_PREFIX):]
        if at.startswith(const.EVENT_REF_PREFIX) and suffix.isdigit():
            return int(suffix)
        commit = await self._backend.get_commit(at)
        if commit is not None:
            return commit.event_id_high
        raise build_error(StatusCode.CONTEXT_EXECUTION_ERROR, error_msg=f"unknown vcs ref: {at}")

    async def rewind(self, at: str) -> dict:
        """Overwrite-rewind this session to `at`: truncate after it, reload live state."""
        await self._ensure_loaded()
        target = await self._resolve_event_id(at)
        restored = await self._restore_at_event(target)
        await self._backend.truncate(after_event_id=target)
        commit_id = await self._latest_commit_id(target)
        self._head = Head(event_id=target, commit_id=commit_id, forked_from=self._head.forked_from)
        await self._backend.put_head(self._head)
        await self._applier(restored)
        self._last = deepcopy(restored)
        self._appends_since_snapshot = 0
        return restored

    async def _latest_commit_id(self, target: int) -> str | None:
        candidates = [c for c in await self._backend.list_commits() if c.event_id_high <= target]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.event_id_high).commit_id

    async def fork(self, *, at: str | None = None) -> ForkResult:
        """Clone a new Session seeded from this session's state at `at` (default head)."""
        await self._ensure_loaded()
        if self._forker is None:
            raise build_error(StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="fork requires a session forker")
        if at is None:
            target = self._head.event_id
            at_ref = f"{const.EVENT_REF_PREFIX}{target}"
        else:
            target = await self._resolve_event_id(at)
            at_ref = at
        seed = await self._restore_at_event(target)
        new_id = self._id_factory()
        return await self._forker(new_id, seed, (self._session_id, at_ref))

    async def list_history(self, *, limit: int | None = None) -> list[Commit]:
        """List commits newest-first by walking parent_id from the current head."""
        await self._ensure_loaded()
        by_id = {c.commit_id: c for c in await self._backend.list_commits()}
        ordered: list[Commit] = []
        current = self._head.commit_id
        while current is not None and current in by_id:
            commit = by_id[current]
            ordered.append(commit)
            if limit is not None and len(ordered) >= limit:
                break
            current = commit.parent_id
        return ordered
