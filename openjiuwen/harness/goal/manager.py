# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal capability and the sole writer of persistent ``GoalRecord`` state."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Protocol

from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalOperationError,
    GoalRecord,
    GoalStatus,
)
from openjiuwen.harness.task_loop.event_manager import RoundWorkQueue
from openjiuwen.harness.schema.interaction import InteractionEvent, RoundWorkItem

logger = logging.getLogger(__name__)


CancelRound = Callable[..., Awaitable[None]]


class GoalStore(Protocol):
    """Persistence surface required by GoalManager."""

    @property
    def session_id(self) -> str: ...

    def load(self) -> Optional[GoalRecord]: ...

    def save(self, record: GoalRecord) -> None: ...

    def clear(self) -> None: ...


class GoalManager:
    """Session-scoped Goal capability.

    All writes share the interaction control lock with output attachment and
    work scheduling.  The manager never sees WebSocket/request objects.
    """

    def __init__(
        self,
        *,
        store: GoalStore,
        event_manager: RoundWorkQueue,
        control_lock: asyncio.Lock,
        has_output_stream: Callable[[], bool],
        cancel_active_round: CancelRound,
        emit_event: Callable[[InteractionEvent], None],
        notify_work: Callable[[], None],
        language: str = "cn",
    ) -> None:
        self._store = store
        self._event_manager = event_manager
        self._control_lock = control_lock
        self._has_output_stream = has_output_stream
        self._cancel_active_round = cancel_active_round
        self._emit_event = emit_event
        self._notify_work = notify_work
        self._language = language

    def get_store(self, session_id: str | None = None) -> GoalStore:
        """Expose the session store for read-only tools and rails only."""
        if session_id is not None and session_id != self._store.session_id:
            raise ValueError("GoalManager is bound to a different session")
        return self._store

    async def get(self) -> Optional[GoalRecord]:
        async with self._control_lock:
            record = self._store.load()
            return record.copy_for_response() if record is not None else None

    async def set(
        self,
        objective: str,
        *,
        overwrite_confirmed: bool = False,
        token_budget: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> GoalRecord:
        normalized = objective.strip()
        if not normalized:
            raise GoalOperationError(
                operation="set",
                code="invalid_objective",
                message="goal objective must not be empty",
            )
        if token_budget is not None and token_budget <= 0:
            raise GoalOperationError(
                operation="set",
                code="invalid_objective",
                message="token_budget must be positive",
            )
        if max_attempts is not None and max_attempts <= 0:
            raise GoalOperationError(
                operation="set",
                code="invalid_objective",
                message="max_attempts must be positive",
            )

        async with self._control_lock:
            existing = self._store.load()
            if existing is not None and not overwrite_confirmed:
                raise GoalOperationError(
                    operation="set",
                    code="already_exists",
                    message="a goal already exists for this session",
                    goal=existing,
                )

            if existing is not None:
                self._event_manager.discard_goal_work(
                    session_id=existing.session_id,
                    goal_id=existing.goal_id,
                )

            record = GoalRecord.create(
                session_id=self._store.session_id,
                objective=normalized,
                token_budget=token_budget,
                max_attempts=max_attempts,
            )
            self._store.save(record)

            # An existing stream remains the one and only consumer.  Queue the
            # replacement work before aborting the old goal round so the stream
            # naturally continues into the replacement.
            if self._has_output_stream():
                self._ensure_goal_work_locked(record)
                self._emit_goal_updated_locked(record)

            if existing is not None:
                await self._cancel_active_round(
                    expected_run_kind="goal",
                    expected_goal_id=existing.goal_id,
                    reason="goal_overwrite",
                )

            return record.copy_for_response()

    async def pause(self) -> Optional[GoalRecord]:
        async with self._control_lock:
            record = self._store.load()
            if record is None:
                return None
            if record.status is GoalStatus.ACTIVE:
                record.status = GoalStatus.PAUSED
                record.touch(bump_revision=True)
                self._store.save(record)
                self._event_manager.discard_goal_work(
                    session_id=record.session_id,
                    goal_id=record.goal_id,
                )
                if self._has_output_stream():
                    self._emit_goal_updated_locked(record)
            return record.copy_for_response()

    async def resume(self) -> Optional[GoalRecord]:
        async with self._control_lock:
            record = self._store.load()
            if record is None:
                return None
            if record.status in (GoalStatus.PAUSED, GoalStatus.BLOCKED):
                record.status = GoalStatus.ACTIVE
                record.touch(bump_revision=True)
                self._store.save(record)
                if self._has_output_stream():
                    self._ensure_goal_work_locked(record)
                    self._emit_goal_updated_locked(record)
            return record.copy_for_response()

    async def clear(self) -> Optional[GoalRecord]:
        async with self._control_lock:
            record = self._store.load()
            if record is None:
                return None
            self._store.clear()
            self._event_manager.discard_goal_work(
                session_id=record.session_id,
                goal_id=record.goal_id,
            )
            await self._cancel_active_round(
                expected_run_kind="goal",
                expected_goal_id=record.goal_id,
                reason="goal_clear",
            )
            if self._has_output_stream():
                self._emit_event(InteractionEvent.goal_updated(None))
            return record.copy_for_response()

    def ensure_active_goal_work_locked(self) -> bool:
        """Ensure the current ACTIVE record has one queued/dequeued/active work.

        The caller must hold the shared interaction control lock and must already
        have acquired an output stream.
        """
        record = self._store.load()
        if record is None or record.status is not GoalStatus.ACTIVE:
            return False
        return self._ensure_goal_work_locked(record)

    async def begin_attempt(
        self,
        *,
        goal_id: str,
        revision: int,
    ) -> Optional[GoalRecord]:
        """Validate and record the start of one goal attempt."""
        async with self._control_lock:
            record = self._store.load()
            if record is None or not self._matches_active(record, goal_id, revision):
                return None
            record.attempt_count += 1
            record.touch()
            self._store.save(record)
            return record.copy_for_response()

    async def accumulate_usage(
        self,
        *,
        goal_id: str,
        revision: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
    ) -> None:
        async with self._control_lock:
            record = self._store.load()
            if record is None or not self._matches_active(record, goal_id, revision):
                return
            record.token_usage.accumulate(input_tokens, output_tokens, cached_input_tokens)
            record.touch()
            self._store.save(record)

    async def apply_assessment(
        self,
        *,
        goal_id: str,
        revision: int,
        assessment: GoalAssessment,
    ) -> Optional[GoalRecord]:
        """Commit an assessment only when its goal generation is still current."""
        async with self._control_lock:
            record = self._store.load()
            if record is None or not self._matches_active(record, goal_id, revision):
                return None
            record.last_assessment = assessment
            if assessment.status is GoalAssessmentStatus.COMPLETE:
                record.status = GoalStatus.COMPLETED
                record.last_stop_reason = "completed"
            elif assessment.status is GoalAssessmentStatus.BLOCKED:
                record.status = GoalStatus.BLOCKED
                record.last_stop_reason = "blocked"
            record.touch()
            self._store.save(record)

            if record.status is GoalStatus.ACTIVE and self._has_output_stream():
                self._ensure_goal_work_locked(record)
            if self._has_output_stream():
                self._emit_goal_updated_locked(record)
            return record.copy_for_response()

    def _ensure_goal_work_locked(self, record: GoalRecord) -> bool:
        if record.status is not GoalStatus.ACTIVE or not self._has_output_stream():
            return False
        from openjiuwen.harness.prompts.sections.goal import build_goal_task_query

        work = RoundWorkItem.goal(
            inputs={"query": build_goal_task_query(record, self._language)},
            goal_id=record.goal_id,
            revision=record.revision,
            session_id=record.session_id,
        )
        queued = self._event_manager.push_goal(work)
        if queued:
            self._notify_work()
        return queued

    def _emit_goal_updated_locked(self, record: GoalRecord) -> None:
        self._emit_event(InteractionEvent.goal_updated(record.to_dict()))

    @staticmethod
    def _matches_active(
        record: Optional[GoalRecord],
        goal_id: str,
        revision: int,
    ) -> bool:
        return (
            record is not None
            and record.status is GoalStatus.ACTIVE
            and record.goal_id == goal_id
            and record.revision == revision
        )


__all__ = ["GoalManager", "GoalStore"]
