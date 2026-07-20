# coding: utf-8
"""Tests for the session-scoped Goal capability."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

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


class FakeSession:
    def __init__(self, session_id: str = "session-1") -> None:
        self._session_id = session_id
        self._state: dict[str, Any] = {}
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
    assert harness.events.next_work() is not None

    paused = await harness.manager.pause()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    assert harness.events.next_work() is None

    resumed = await harness.manager.resume()
    assert resumed is not None
    assert resumed.status is GoalStatus.ACTIVE
    queued = harness.events.next_work()
    assert queued is not None
    assert queued.context["goal_id"] == goal.goal_id


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
