# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Append-only storage for per-iteration agent step checkpoints.

One KV key per completed ReAct iteration, laid out as
``{session_id}:agent-step:{agent_id}:{seq:020d}``. Keys are only ever appended;
recovery replays them in order from an empty state, so a half-written round
simply never becomes a key.

The process keeps an in-memory baseline per ``(session_id, agent_id)``: the
snapshot that was last written successfully. Diffs are always computed against
that baseline, never against whatever happens to be on disk, so a writer
process can keep appending without re-reading its own records, and a failed
write leaves the baseline untouched for the next round to cover.
"""
from dataclasses import dataclass
from typing import Any, Literal

from openjiuwen.core.common.logging import LogEventType, session_logger
from openjiuwen.core.graph.store import create_serializer
from openjiuwen.core.session.checkpointer.base import build_key_with_namespace
from openjiuwen.core.session.checkpointer.step_delta import (
    STEP_WINDOW_KEY,  # noqa: F401  (re-exported for callers building snapshots)
    StepKind,
    StepRecord,
    apply_step_context,
    apply_step_state,
    build_step_snapshot,
    diff_step_context,
    diff_step_state,
    restore_full_state,
)
from openjiuwen.core.session.session import BaseSession

SESSION_NAMESPACE_AGENT_STEP = "agent-step"
SEQ_WIDTH = 20

StepStatus = Literal["active", "resume_pending", "interrupted", "idle"]


@dataclass(frozen=True)
class StepResume:
    """Where a resumed run should start, taken from the last completed step.

    Attributes:
        iteration: Iteration of the last completed step; resume at ``+1``.
        query: User query of the run that last step belongs to.
    """

    iteration: int
    query: str


@dataclass
class _StepTrack:
    """In-process bookkeeping for one ``(session_id, agent_id)`` pair."""

    baseline: dict | None = None
    next_seq: int = 0
    last_iteration: int = -1
    last_query: str = ""
    last_kind: StepKind | None = None
    status: StepStatus | None = None
    resume: StepResume | None = None


class AgentStepStorage:
    """Append, replay and clear agent step records on a ``BaseKVStore``."""

    def __init__(self, kv_store):
        self._kv_store = kv_store
        self._serde = create_serializer("pickle")
        self._tracks: dict[tuple[str, str], _StepTrack] = {}

    # --- public API ---

    async def append(
        self,
        session: BaseSession,
        *,
        kind: StepKind,
        iteration: int | None = None,
        query: str | None = None,
    ) -> None:
        """Append one record for the session's current state.

        The baseline only advances after the KV write returns, so a failed
        write leaves the next record to cover both rounds.

        Raises:
            Exception: Whatever the KV store raised; the step is not recorded.
        """
        sid, aid = self._ids(session)
        track = self._tracks.setdefault((sid, aid), _StepTrack())
        snapshot = build_step_snapshot(session.state().get_state())
        base = track.baseline if track.baseline is not None else {"context": {}, "state": {}}

        context_delta = diff_step_context(base.get("context") or {}, snapshot["context"])
        state_delta, state_full = diff_step_state(base.get("state") or {}, snapshot["state"])
        record_iteration = track.last_iteration if iteration is None else iteration
        record_query = track.last_query if query is None else query
        seq = track.next_seq
        record = StepRecord(
            seq=seq,
            iteration=record_iteration,
            kind=kind,
            query=record_query,
            context=context_delta,
            state=state_delta,
            state_full=state_full,
        )

        await self._kv_store.set(self._key(sid, aid, seq), self._serialize(record))

        track.baseline = snapshot
        track.next_seq = seq + 1
        track.last_iteration = record_iteration
        track.last_query = record_query
        track.last_kind = kind
        session_logger.debug(
            "Agent step record appended",
            event_type=LogEventType.CHECKPOINT_SAVE,
            session_id=sid,
            agent_id=aid,
            metadata={"storage_type": "persistence", "kind": kind, "seq": seq},
        )

    async def recover(self, session: BaseSession) -> None:
        """Rebuild the session state from the step records on disk.

        A writer process restores from its in-memory baseline without touching
        the store at all. Any other process replays keys in order and truncates
        from the first record that fails to deserialize.
        """
        sid, aid = self._ids(session)
        track = self._tracks.get((sid, aid))
        if track is not None and track.baseline is not None:
            session.state().set_state(restore_full_state(track.baseline))
            return

        prefix = self._prefix(sid, aid)
        raw = await self._kv_store.get_by_prefix(prefix)
        exact = sorted(
            key for key in raw if key.startswith(prefix) and self._seq_of(key, prefix) is not None
        )

        records: list[tuple[int, StepRecord]] = []
        for index, key in enumerate(exact):
            try:
                records.append((self._seq_of(key, prefix), self._deserialize(raw[key])))
            except Exception as exc:
                doomed = exact[index:]
                await self._kv_store.batch_delete(doomed)
                session_logger.warning(
                    "Agent step record unreadable, truncating from %s",
                    key,
                    event_type=LogEventType.CHECKPOINT_ERROR,
                    session_id=sid,
                    agent_id=aid,
                    error_message=str(exc),
                    metadata={"storage_type": "persistence", "deleted_keys": len(doomed)},
                )
                break

        context: dict = {}
        state: dict = {}
        for _, record in records:
            context = apply_step_context(context, record.context)
            state = apply_step_state(state, record.state, record.state_full)
        session.state().set_state(restore_full_state({"context": context, "state": state}))

        if not records:
            self._tracks.pop((sid, aid), None)
            return

        last_seq, last = records[-1]
        self._tracks[(sid, aid)] = _StepTrack(
            baseline={"context": context, "state": state},
            next_seq=last_seq + 1,
            last_iteration=last.iteration,
            last_query=last.query,
            last_kind=last.kind,
            status=self._status_for(last.kind),
            resume=StepResume(last.iteration, last.query) if last.kind == "step" else None,
        )
        session_logger.debug(
            "Agent step records replayed",
            event_type=LogEventType.CHECKPOINT_RESTORE,
            session_id=sid,
            agent_id=aid,
            metadata={"storage_type": "persistence", "records": len(records)},
        )

    async def clear(self, session_id: str, agent_id: str) -> None:
        """Delete every step record of one agent in one session.

        Matching is done in Python on the exact prefix because
        ``delete_by_prefix`` compiles the prefix into a LIKE pattern, where
        ``_`` and ``%`` inside a session or agent id would match too much.
        """
        prefix = self._prefix(session_id, agent_id)
        raw = await self._kv_store.get_by_prefix(prefix)
        exact = [
            key for key in sorted(raw) if key.startswith(prefix) and self._seq_of(key, prefix) is not None
        ]
        if exact:
            await self._kv_store.batch_delete(exact)
        self._tracks.pop((session_id, agent_id), None)
        session_logger.debug(
            "Agent step records cleared",
            event_type=LogEventType.CHECKPOINT_CLEAR,
            session_id=session_id,
            agent_id=agent_id,
            metadata={"storage_type": "persistence", "deleted_keys": len(exact)},
        )

    def forget_session(self, session_id: str) -> None:
        """Drop in-process tracks of a whole session after a bulk key delete."""
        for key in [k for k in self._tracks if k[0] == session_id]:
            self._tracks.pop(key, None)

    def begin(self, session: BaseSession) -> StepResume | None:
        """Take the pending resume point (once) and mark the agent active."""
        track = self._tracks.setdefault(self._ids(session), _StepTrack())
        resume = track.resume
        track.resume = None
        track.status = "active"
        return resume

    def peek_resume(self, session: BaseSession) -> StepResume | None:
        """Read the pending resume point without consuming it."""
        track = self._tracks.get(self._ids(session))
        return track.resume if track is not None else None

    async def end(self, session: BaseSession, *, interrupted: bool) -> None:
        """Close the current run, recording why it stopped.

        An ``interrupt`` record is written even when nothing was persisted yet
        (the first round may stop for confirmation before any step completes).
        A ``final`` record is only written when steps exist, so an idle session
        does not accumulate junk. The status is set in ``finally`` so it stays
        correct even when the append fails.
        """
        sid, aid = self._ids(session)
        track = self._tracks.setdefault((sid, aid), _StepTrack())
        try:
            if interrupted:
                if track.last_kind != "interrupt":
                    await self.append(session, kind="interrupt")
            elif track.baseline is not None and track.last_kind != "final":
                await self.append(session, kind="final")
        finally:
            track.status = "interrupted" if interrupted else "idle"
            track.resume = None
            session_logger.debug(
                "Agent steps ended",
                event_type=LogEventType.CHECKPOINT_SAVE,
                session_id=sid,
                agent_id=aid,
                metadata={"storage_type": "persistence", "interrupted": interrupted},
            )

    def status(self, session: BaseSession) -> StepStatus | None:
        track = self._tracks.get(self._ids(session))
        return track.status if track is not None else None

    def has_records(self, session: BaseSession) -> bool:
        track = self._tracks.get(self._ids(session))
        return track is not None and track.baseline is not None

    # --- internals ---

    @staticmethod
    def _ids(session: BaseSession) -> tuple[str, str]:
        return session.session_id(), session.agent_id()

    @staticmethod
    def _prefix(session_id: str, agent_id: str) -> str:
        return build_key_with_namespace(session_id, SESSION_NAMESPACE_AGENT_STEP, agent_id) + ":"

    @staticmethod
    def _key(session_id: str, agent_id: str, seq: int) -> str:
        return build_key_with_namespace(
            session_id, SESSION_NAMESPACE_AGENT_STEP, agent_id, f"{seq:0{SEQ_WIDTH}d}"
        )

    @staticmethod
    def _seq_of(key: str, prefix: str) -> int | None:
        suffix = key[len(prefix):] if key.startswith(prefix) else ""
        if len(suffix) == SEQ_WIDTH and suffix.isdigit():
            return int(suffix)
        return None

    @staticmethod
    def _status_for(kind: StepKind) -> StepStatus:
        if kind == "step":
            return "resume_pending"
        if kind == "interrupt":
            return "interrupted"
        return "idle"

    def _serialize(self, record: StepRecord) -> bytes:
        _, blob = self._serde.dumps_typed(record)
        return blob

    def _deserialize(self, blob: Any) -> StepRecord:
        return self._serde.loads_typed(("pickle", blob))
