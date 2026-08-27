# coding: utf-8
"""EvolutionRail execution-path regression tests independent of legacy builders."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.agent_evolving.trajectory.store import InMemoryTrajectoryStore
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, PreparedEvolutionInput
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail
from openjiuwen.extensions.observability import semconv


def _trajectory() -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {TRAJECTORY_ID: "trajectory-1", SESSION_ID: "session-1"}
                        )
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": []}],
                }
            ]
        }
    )


def _prepared() -> PreparedEvolutionInput:
    return PreparedEvolutionInput(trajectory=_trajectory(), messages=())


def _message_trajectory() -> Trajectory:
    payload = _trajectory().to_otlp()
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"] = [
        {
            "traceId": "trace-1",
            "spanId": "span-1",
            "name": "llm.call",
            "startTimeUnixNano": "1",
            "endTimeUnixNano": "2",
            "attributes": attributes_from_map(
                {
                    f"{semconv.GEN_AI_PROMPT}.0.role": "user",
                    f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
                    f"{semconv.GEN_AI_PROMPT}.0.name": "caller",
                }
            ),
        }
    ]
    return Trajectory.from_otlp(payload)


class _FailingRail(EvolutionRail):
    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        del prepared
        raise RuntimeError("evolution failed")


class _TimeoutRail(EvolutionRail):
    def _get_evolution_total_timeout_secs(self) -> float:
        return 0.001

    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        del prepared
        await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_safe_run_evolution_isolates_failure_and_emits_host_event() -> None:
    rail = _FailingRail(trajectory_span_processor=TrajectorySpanProcessor())

    await rail._safe_run_evolution(_prepared())

    events = await rail.drain_pending_host_events()
    assert len(events) == 1
    assert events[0].payload["evolution_meta"]["status"] == "failed"
    assert "evolution failed" in events[0].payload["content"]


@pytest.mark.asyncio
async def test_safe_run_evolution_applies_total_timeout() -> None:
    rail = _TimeoutRail(trajectory_span_processor=TrajectorySpanProcessor())

    await rail._safe_run_evolution(_prepared())

    events = await rail.drain_pending_host_events()
    assert len(events) == 1
    assert events[0].payload["evolution_meta"]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_approval_event_compatibility_wrappers_share_host_buffer() -> None:
    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    rail._emit_background_outcome_event(
        {
            "status": "failed",
            "message": "failed",
            "rail_kind": "skill",
            "stage": "evaluate",
            "skill_name": "demo",
        }
    )

    assert len(rail._collect_pending_approval_events()) == 1
    assert await rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_safe_run_evolution_accepts_only_prepared_contract() -> None:
    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())

    with pytest.raises(TypeError, match="prepared"):
        await rail._safe_run_evolution({"trajectory": _trajectory()})  # type: ignore[arg-type]


def test_trajectory_recorder_contract_is_explicit() -> None:
    processor = TrajectorySpanProcessor()
    store = InMemoryTrajectoryStore()
    rail = TrajectoryRail(trajectory_span_processor=processor, trajectory_store=store)

    assert isinstance(rail, EvolutionRail)
    assert rail.priority == 10
    assert rail.trajectory_span_processor is processor
    assert rail.trajectory_store is store


def test_evolution_rail_requires_concrete_processor() -> None:
    with pytest.raises(TypeError, match="TrajectorySpanProcessor"):
        EvolutionRail(trajectory_span_processor=object())  # type: ignore[arg-type]


def test_subclass_can_explicitly_select_trajectory_message_fields() -> None:
    messages = EvolutionRail._trajectory_to_messages(
        _message_trajectory(),
        fields={"name"},
    )

    assert messages == [{"role": "user", "name": "caller"}]


@pytest.mark.asyncio
async def test_cleanup_background_tasks() -> None:
    """cleanup_background_tasks drops completed tasks from the registry."""
    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    rail._bg_tasks = set()
    await rail.cleanup_background_tasks()
    assert len(rail._bg_tasks) == 0


@pytest.mark.asyncio
async def test_cleanup_background_tasks_waits_without_cancelling() -> None:
    """Terminal cleanup must let post-terminal work finish instead of killing it."""
    cancelled = False

    async def slow_work():
        nonlocal cancelled
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            raise

    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    bg_task = BackgroundTask.from_asyncio_task(
        asyncio.create_task(slow_work()),
        group="evolution",
    )
    rail._bg_tasks = {bg_task}

    await rail.cleanup_background_tasks()

    assert cancelled is False
    assert bg_task.done()
    assert len(rail._bg_tasks) == 0


@pytest.mark.asyncio
async def test_cleanup_background_tasks_timeout_does_not_cancel() -> None:
    """A wait timeout must not abort the background evolution task."""
    cancelled = False

    async def hang():
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    bg_task = BackgroundTask.from_asyncio_task(
        asyncio.create_task(hang()),
        group="evolution",
    )
    rail._bg_tasks = {bg_task}

    try:
        await rail.cleanup_background_tasks(timeout=0.05)
        assert cancelled is False
        assert bg_task.done() is False
        assert bg_task in rail._bg_tasks
    finally:
        await bg_task.cancel(reason="test_teardown")
