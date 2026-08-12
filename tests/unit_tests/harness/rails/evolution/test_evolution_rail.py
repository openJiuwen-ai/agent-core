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
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, PreparedEvolutionInput
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail


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
