# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Data models for session version control (vcs).

All persisted records are pydantic models serialized as JSON (never pickle).
Message payloads are kept as json-native dicts (encoded via :mod:`codec`)
rather than ``BaseMessage`` instances, to avoid pydantic nested-polymorphism
pitfalls and keep the on-disk form portable.
"""
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageDelta(BaseModel):
    """One context's message change within a single append.

    Attributes:
        context_id: The context this delta applies to.
        kind: ``"append"`` carries only newly appended messages; ``"reset"``
            carries the full message list (used on truncation / compaction /
            offload change).
        messages: Encoded message dicts (json-native).
        offload_messages: Full offload map, carried on ``"reset"`` only.
    """

    context_id: str
    kind: Literal["append", "reset"]
    messages: list[dict] = Field(default_factory=list)
    offload_messages: dict | None = None


class StateDelta(BaseModel):
    """KV state change expressed without None ambiguity.

    Attributes:
        set: Nested dict merged via ``update_dict(ignore_delete=True)``; None
            values are written literally, not treated as deletions.
        removed: Nested key paths to delete (e.g. ``"global_state.foo.bar"``).
    """

    set: dict = Field(default_factory=dict)
    removed: list[str] = Field(default_factory=list)


class LogEntry(BaseModel):
    """One WAL entry: the full set of changes produced by a single append().

    Attributes:
        event_id: Monotonic id within the session's linear history.
        context: Per-context message changes (0..N).
        state: KV state change (possibly empty).
        ts: Wall-clock timestamp (informational only).
        crc: crc32 over the canonical JSON of this entry excluding ``crc``,
            used to detect partial / torn writes during recovery.
    """

    event_id: int
    context: list[MessageDelta] = Field(default_factory=list)
    state: StateDelta = Field(default_factory=StateDelta)
    ts: float = 0.0
    crc: int = 0


class Commit(BaseModel):
    """A named point on the session's linear history.

    Attributes:
        commit_id: Unique id (uuid4 hex).
        parent_id: Previous commit on this session's chain, or None for the first.
        event_id_high: WAL position this commit covers up to.
        snapshot_id: Associated full snapshot id, if one was taken.
        message: Human-readable label.
        ts: Wall-clock timestamp.
    """

    commit_id: str
    parent_id: str | None = None
    event_id_high: int = 0
    snapshot_id: str | None = None
    message: str = ""
    ts: float = 0.0


class Snapshot(BaseModel):
    """A full ``{context, state}`` snapshot (replay start / fork seed).

    Attributes:
        snapshot_id: Unique id (uuid4 hex).
        event_id_high: WAL position this snapshot reflects = replay start point.
        context: ``{context_id: {"messages": [...], "offload_messages": {...}}}``.
        state: KV state dict (with the context key stripped out).
        ts: Wall-clock timestamp.
    """

    snapshot_id: str
    event_id_high: int = 0
    context: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)
    ts: float = 0.0


class Head(BaseModel):
    """The session's linear-history head pointer.

    Attributes:
        event_id: Current last event id of the session's WAL.
        commit_id: Most recent commit id, or None.
        forked_from: Set only on forked sessions: ``(source_session_id, source_at)``.
    """

    event_id: int = 0
    commit_id: str | None = None
    forked_from: tuple[str, str] | None = None


@dataclass
class ForkResult:
    """Return value of ``fork()``: a brand-new Session and its version control.

    Not persisted, hence a plain dataclass holding live objects.

    Attributes:
        session_id: The new session id.
        session: The newly created Session object.
        version_control: VersionControl bound to the new session.
    """

    session_id: str
    session: Any
    version_control: Any
