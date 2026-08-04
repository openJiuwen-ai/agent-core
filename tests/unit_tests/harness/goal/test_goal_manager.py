# coding: utf-8
"""Tests for the session-scoped Goal capability."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, tzinfo
from typing import Any

import pytest

import openjiuwen.harness.goal.schema as goal_schema
from openjiuwen.core.session.state.base import InMemoryStateLike
from openjiuwen.harness.goal.manager import GoalManager
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalOperationError,
    GoalStatus,
)
from openjiuwen.harness.goal.store import SessionGoalStore
from openjiuwen.harness.task_loop.event_manager import EventManager
from openjiuwen.harness.schema.interaction import InteractionEvent, InteractionEventType


def _utc_timestamp(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _utc_iso(seconds: int) -> str:
    return _utc_timestamp(seconds).isoformat()


def _freeze_goal_clock(monkeypatch: pytest.MonkeyPatch, clock: dict[str, int]) -> None:
    class FrozenDateTime:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            current = _utc_timestamp(clock["now"])
            return current if tz is None else current.astimezone(tz)

        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return datetime.fromisoformat(value)

    monkeypatch.setattr(goal_schema, "datetime", FrozenDateTime)


class FakeSession:
    """Production-like session: dotted keys + None-as-delete merge semantics."""

    def __init__(self, session_id: str = "session-1") -> None:
        self._session_id = session_id
        self._state = InMemoryStateLike()
        self.commit_count = 0

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, value: dict[str, Any]) -> None:
        self._state.update(value)

    async def commit(self) -> None:
        self.commit_count += 1


class ManagerHarness:
    def __init__(self, *, output_attached: bool = True) -> None:
        self.session = FakeSession()
        self.store = SessionGoalStore(self.session)
        self.events = EventManager()
        self.output_attached = output_attached
        self.emitted: list[InteractionEvent] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.notify_calls = 0
        self.manager = GoalManager(
            store=self.store,
            event_manager=self.events,
            control_lock=asyncio.Lock(),
            has_output_stream=lambda: self.output_attached,
            cancel_active_round=self.cancel_active_round,
            emit_event=self.emitted.append,
            notify_work=self.notify_work,
        )

    async def cancel_active_round(self, **kwargs: Any) -> None:
        self.cancel_calls.append(kwargs)

    def notify_work(self) -> None:
        self.notify_calls += 1


@pytest.mark.asyncio
async def test_set_requires_a_non_empty_objective() -> None:
    harness = ManagerHarness()

    with pytest.raises(GoalOperationError, match="must not be empty") as error:
        await harness.manager.set("  ")

    assert error.value.code == "invalid_objective"
    assert await harness.manager.get() is None


@pytest.mark.asyncio
async def test_set_persists_goal_and_queues_work_only_with_an_output_consumer() -> None:
    detached = ManagerHarness(output_attached=False)
    record = await detached.manager.set("write a report")

    assert record.objective == "write a report"
    assert detached.events.next_work() is None
    assert detached.emitted == []

    attached = ManagerHarness()
    record = await attached.manager.set("write a report")
    queued = attached.events.next_work()

    assert queued is not None
    assert queued.kind == "goal"
    assert queued.context["goal_id"] == record.goal_id
    assert attached.notify_calls == 1
    assert attached.emitted[0].type is InteractionEventType.GOAL_UPDATED


@pytest.mark.asyncio
async def test_goal_writes_are_committed_immediately() -> None:
    harness = ManagerHarness(output_attached=False)

    goal = await harness.manager.set("write a report")
    assert harness.session.commit_count == 1

    paused = await harness.manager.pause()
    assert paused is not None
    assert harness.session.commit_count == 2

    resumed = await harness.manager.resume()
    assert resumed is not None
    assert harness.session.commit_count == 3

    await harness.manager.begin_attempt(goal_id=goal.goal_id, revision=resumed.revision)
    assert harness.session.commit_count == 4


@pytest.mark.asyncio
async def test_set_requires_confirmation_before_replacing_existing_goal() -> None:
    harness = ManagerHarness()
    old = await harness.manager.set("first goal")

    with pytest.raises(GoalOperationError) as error:
        await harness.manager.set("second goal")
    assert error.value.code == "already_exists"
    assert error.value.goal is not None
    assert error.value.goal.goal_id == old.goal_id

    replacement = await harness.manager.set("second goal", overwrite_confirmed=True)
    assert replacement.goal_id != old.goal_id
    assert replacement.objective == "second goal"
    assert harness.cancel_calls[-1] == {
        "expected_run_kind": "goal",
        "expected_goal_id": old.goal_id,
        "reason": "goal_overwrite",
    }


@pytest.mark.asyncio
async def test_pause_and_resume_are_noops_without_a_goal() -> None:
    harness = ManagerHarness()

    assert await harness.manager.pause() is None
    assert await harness.manager.resume() is None
    assert await harness.manager.clear() is None


@pytest.mark.asyncio
async def test_pause_then_resume_updates_state_and_requeues_goal_work() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")
    assert harness.events.has_pending_work()

    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    # Pending continuation discarded; nothing dequeued/active yet.
    assert harness.events.next_work() is None

    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.status is GoalStatus.ACTIVE
    assert resumed.revision == goal.revision + 1
    queued = harness.events.next_work()
    assert queued is not None
    assert queued.context["goal_id"] == goal.goal_id
    assert queued.context["revision"] == resumed.revision


@pytest.mark.asyncio
async def test_second_pause_after_settle_still_loads_goal() -> None:
    """Interrupt already paused the goal; a follow-up pause must not wipe store."""
    harness = ManagerHarness()
    await harness.manager.set("write a report")

    first = await harness.manager.pause()
    assert first is not None
    assert first.status is GoalStatus.PAUSED
    assert first.active_started_at is None

    second = await harness.manager.pause()
    assert second is not None
    assert second.status is GoalStatus.PAUSED
    assert await harness.manager.get() is not None


@pytest.mark.asyncio
async def test_pause_resume_and_completion_preserve_accumulated_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    goal = await harness.manager.set("write a report")
    assert goal.time_used_seconds == 0
    assert goal.active_started_at == _utc_iso(1000)

    clock["now"] = 1030
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    assert paused.time_used_seconds == 30
    assert paused.active_started_at is None

    clock["now"] = 2000
    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.status is GoalStatus.ACTIVE
    assert resumed.time_used_seconds == 30
    assert resumed.active_started_at == _utc_iso(2000)

    started = await harness.manager.begin_attempt(
        goal_id=goal.goal_id,
        revision=resumed.revision,
    )
    assert started is not None

    clock["now"] = 2045
    completed = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=resumed.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.COMPLETE,
            evidence="all checks passed",
        ),
    )

    assert completed is not None
    assert completed.status is GoalStatus.COMPLETED
    assert completed.time_used_seconds == 75
    assert completed.active_started_at is None


@pytest.mark.asyncio
async def test_pause_keeps_timing_while_in_flight_attempt_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pause must not drop wall time of the still-running attempt."""
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    goal = await harness.manager.set("write a report")
    work = harness.events.next_work()
    assert work is not None
    harness.events.mark_started(work)

    clock["now"] = 1007
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    # Settled through pause so display hosts can show time_used while paused.
    assert paused.time_used_seconds == 7
    # In-flight attempt reopens the clock for post-pause accounting.
    assert paused.active_started_at == _utc_iso(1007)

    clock["now"] = 1020
    await harness.manager.accumulate_usage(
        goal_id=goal.goal_id,
        revision=goal.revision,
        input_tokens=3,
    )
    mid = await harness.manager.get()
    assert mid is not None
    assert mid.time_used_seconds == 20
    assert mid.active_started_at == _utc_iso(1020)

    clock["now"] = 1025
    continued = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=goal.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.CONTINUE,
            evidence="still working",
        ),
    )
    assert continued is not None
    assert continued.status is GoalStatus.PAUSED
    assert continued.time_used_seconds == 25
    assert continued.active_started_at is None

    clock["now"] = 1100
    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.status is GoalStatus.ACTIVE
    assert resumed.time_used_seconds == 25
    # Idle pause (25→100) must not be billed; fresh segment starts at resume.
    assert resumed.active_started_at == _utc_iso(1100)


@pytest.mark.asyncio
async def test_pause_without_in_flight_attempt_still_settles_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    await harness.manager.set("write a report")
    # Queued-only work is discarded on pause → settle immediately.

    clock["now"] = 1030
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.time_used_seconds == 30
    assert paused.active_started_at is None


@pytest.mark.asyncio
async def test_resume_jumps_time_used_after_in_flight_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused display freezes; resume must fold running-through-pause time in."""
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    await harness.manager.set("write a report")
    work = harness.events.next_work()
    assert work is not None
    harness.events.mark_started(work)

    clock["now"] = 1010
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.time_used_seconds == 10
    assert paused.active_started_at == _utc_iso(1010)

    clock["now"] = 1040
    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.status is GoalStatus.ACTIVE
    # 10s before pause + 30s still running while paused → jump to 40.
    assert resumed.time_used_seconds == 40
    assert resumed.active_started_at == _utc_iso(1040)
    # Same attempt still running: keep generation, do not queue another round.
    assert resumed.revision == paused.revision
    assert harness.events.next_work() is None


@pytest.mark.asyncio
async def test_resume_while_in_flight_keeps_revision_so_assessment_can_commit() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")
    work = harness.events.next_work()
    assert work is not None
    harness.events.mark_started(work)

    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.revision == goal.revision

    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.revision == goal.revision
    assert harness.events.next_work() is None

    completed = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=goal.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.COMPLETE,
            evidence="all checks passed",
        ),
    )
    assert completed is not None
    assert completed.status is GoalStatus.COMPLETED


@pytest.mark.asyncio
async def test_second_pause_resume_while_in_flight_still_jumps_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    goal = await harness.manager.set("write a report")
    work = harness.events.next_work()
    assert work is not None
    harness.events.mark_started(work)

    clock["now"] = 1010
    first_pause = await harness.manager.pause()
    assert first_pause is not None
    assert first_pause.time_used_seconds == 10

    clock["now"] = 1020
    first_resume = await harness.manager.resume()
    assert first_resume is not None
    assert first_resume.time_used_seconds == 20
    assert first_resume.revision == goal.revision

    clock["now"] = 1030
    second_pause = await harness.manager.pause()
    assert second_pause is not None
    assert second_pause.time_used_seconds == 30
    assert second_pause.active_started_at == _utc_iso(1030)

    clock["now"] = 1050
    second_resume = await harness.manager.resume()
    assert second_resume is not None
    assert second_resume.time_used_seconds == 50
    assert second_resume.revision == goal.revision
    assert harness.events.next_work() is None


@pytest.mark.asyncio
async def test_idle_resume_bumps_revision_and_ensures_work() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")
    # Discard queued work via pause with nothing started → true idle resume.
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.revision == goal.revision

    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.revision == goal.revision + 1
    queued = harness.events.next_work()
    assert queued is not None
    assert queued.context["revision"] == resumed.revision


@pytest.mark.asyncio
async def test_resume_does_not_bill_idle_pause_without_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness()

    await harness.manager.set("write a report")
    # Queued-only → pause settles and does not reopen the clock.
    clock["now"] = 1030
    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.time_used_seconds == 30
    assert paused.active_started_at is None

    clock["now"] = 1100
    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.time_used_seconds == 30
    assert resumed.active_started_at == _utc_iso(1100)
    assert resumed.revision == paused.revision + 1


@pytest.mark.asyncio
async def test_active_usage_accounting_flushes_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    harness = ManagerHarness(output_attached=False)

    goal = await harness.manager.set("write a report")
    await harness.manager.begin_attempt(goal_id=goal.goal_id, revision=goal.revision)

    clock["now"] = 1012
    await harness.manager.accumulate_usage(
        goal_id=goal.goal_id,
        revision=goal.revision,
        input_tokens=5,
    )
    record = await harness.manager.get()
    assert record is not None
    assert record.time_used_seconds == 12
    assert record.active_started_at == _utc_iso(1012)

    clock["now"] = 1020
    await harness.manager.accumulate_usage(
        goal_id=goal.goal_id,
        revision=goal.revision,
        output_tokens=7,
    )
    record = await harness.manager.get()
    assert record is not None
    assert record.time_used_seconds == 20
    assert record.active_started_at == _utc_iso(1020)
    assert record.token_usage.total_tokens == 12


@pytest.mark.asyncio
async def test_clear_removes_goal_work_cancels_active_round_and_emits_snapshot() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")

    cleared = await harness.manager.clear()

    assert cleared is not None
    assert cleared.goal_id == goal.goal_id
    assert await harness.manager.get() is None
    assert harness.events.next_work() is None
    assert harness.cancel_calls[-1] == {
        "expected_run_kind": "goal",
        "expected_goal_id": goal.goal_id,
        "reason": "goal_clear",
    }
    assert harness.emitted[-1].payload == {"goal": None}


@pytest.mark.asyncio
async def test_attempt_usage_and_completion_are_written_for_current_generation() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")

    started = await harness.manager.begin_attempt(goal_id=goal.goal_id, revision=goal.revision)
    assert started is not None
    assert started.attempt_count == 1

    await harness.manager.accumulate_usage(
        goal_id=goal.goal_id,
        revision=goal.revision,
        input_tokens=5,
        output_tokens=7,
    )
    completed = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=goal.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.COMPLETE,
            evidence="all checks passed",
        ),
    )

    assert completed is not None
    assert completed.status is GoalStatus.COMPLETED
    assert completed.token_usage.total_tokens == 12
    assert completed.last_stop_reason == "completed"


@pytest.mark.asyncio
async def test_pause_keeps_revision_so_in_flight_assessment_can_commit() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")
    started = await harness.manager.begin_attempt(goal_id=goal.goal_id, revision=goal.revision)
    assert started is not None

    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    assert paused.revision == goal.revision

    continued = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=goal.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.CONTINUE,
            evidence="partial progress",
            next_instruction="keep going",
        ),
    )
    assert continued is not None
    assert continued.status is GoalStatus.PAUSED
    assert continued.last_assessment is not None
    assert continued.last_assessment.evidence == "partial progress"
    # Pause discarded pending work; CONTINUE must not re-queue while paused.
    assert harness.events.next_work() is None


@pytest.mark.asyncio
async def test_pause_then_complete_assessment_overrides_paused() -> None:
    harness = ManagerHarness()
    goal = await harness.manager.set("write a report")
    await harness.manager.begin_attempt(goal_id=goal.goal_id, revision=goal.revision)
    await harness.manager.pause()

    completed = await harness.manager.apply_assessment(
        goal_id=goal.goal_id,
        revision=goal.revision,
        assessment=GoalAssessment(
            status=GoalAssessmentStatus.COMPLETE,
            evidence="all checks passed",
        ),
    )

    assert completed is not None
    assert completed.status is GoalStatus.COMPLETED
    assert completed.last_assessment is not None
    assert completed.last_stop_reason == "completed"
    assert harness.events.next_work() is None
