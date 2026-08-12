"""Focused canonical trajectory cutover tests for Skill rails."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.core.single_agent.rail.base import InvokeInputs
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
    _SkillPreparedEvolutionInput,
    SkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import TeamSkillEvolutionRail
from openjiuwen.harness.rails.skills.skill_create_rail import SkillCreateRail
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail


def _trajectory(session_id: str = "session") -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": SESSION_ID, "value": {"stringValue": session_id}},
                            {"key": TRAJECTORY_ID, "value": {"stringValue": f"trajectory-{session_id}"}},
                        ]
                    },
                    "scopeSpans": [{"scope": {}, "spans": []}],
                }
            ]
        }
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        inputs=SimpleNamespace(conversation_id="session", is_follow_up=False),
        session=None,
        agent=SimpleNamespace(_loop_controller=None),
    )


def _skill_kwargs() -> dict[str, object]:
    return {
        "llm": Mock(),
        "model": "model",
        "review_runtime": EvolutionReviewRuntime(),
        "signal_trigger": False,
    }


@pytest.mark.parametrize("rail_type", [SkillCreateRail, TeamSkillCreateRail])
def test_create_rails_require_shared_span_processor(tmp_path, rail_type) -> None:
    with pytest.raises(TypeError, match="trajectory_span_processor"):
        rail_type(skills_dir=str(tmp_path / "skills"))


@pytest.mark.parametrize("rail_type", [SkillEvolutionRail, TeamSkillEvolutionRail])
def test_evolution_rails_require_shared_span_processor(tmp_path, rail_type) -> None:
    with pytest.raises(TypeError, match="trajectory_span_processor"):
        rail_type(skills_dir=str(tmp_path / "skills"), **_skill_kwargs())


@pytest.mark.asyncio
async def test_create_hooks_use_canonical_trajectory_argument(tmp_path) -> None:
    processor = TrajectorySpanProcessor()
    trajectory = _trajectory()
    ctx = _ctx()
    skill = SkillCreateRail(skills_dir=str(tmp_path / "skill"), trajectory_span_processor=processor)
    team = TeamSkillCreateRail(skills_dir=str(tmp_path / "team"), trajectory_span_processor=processor)

    await skill._on_before_invoke(ctx)
    await skill._on_after_task_iteration(ctx, trajectory)
    assert skill._trajectory is trajectory

    await team._on_before_invoke(ctx)
    await team._on_after_invoke(ctx, trajectory)
    assert team._trajectory is trajectory


@pytest.mark.asyncio
@pytest.mark.parametrize("rail_type", [SkillCreateRail, TeamSkillCreateRail])
async def test_create_rail_uninit_releases_processor_subscription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    rail_type,
) -> None:
    if rail_type is TeamSkillCreateRail:
        from openjiuwen.extensions.observability import semconv
        import openjiuwen.harness.rails.evolution.evolution_rail as evolution_rail_module

        root = SimpleNamespace(
            context=SimpleNamespace(trace_id=1),
            attributes={semconv.AT_TEAM_NAME: "team"},
            is_recording=lambda: True,
        )
        monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: root)
    processor = TrajectorySpanProcessor()
    rail = rail_type(skills_dir=str(tmp_path / "skills"), trajectory_span_processor=processor)
    ctx = SimpleNamespace(
        inputs=InvokeInputs(query="q", conversation_id="session"),
        session=None,
        agent=SimpleNamespace(card=SimpleNamespace(id="agent"), _loop_controller=None),
    )

    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    subscription = capture.subscription

    rail.uninit(None)

    assert processor.drain(subscription) == (None, ())


@pytest.mark.asyncio
async def test_skill_prepared_input_is_frozen_and_contains_detached_state(tmp_path) -> None:
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        trajectory_span_processor=TrajectorySpanProcessor(),
        **{**_skill_kwargs(), "signal_trigger": True},
    )
    rail._collect_messages_from_trajectory = lambda _trajectory: [{"role": "user", "content": "hello"}]

    prepared = await rail._prepare_evolution_input(_trajectory(), _ctx())

    assert isinstance(prepared, _SkillPreparedEvolutionInput)
    assert prepared is not None
    assert prepared.messages == ({"role": "user", "content": "hello"},)
    with pytest.raises(FrozenInstanceError):
        prepared.messages = ()


class _ProbeEvolutionRail(EvolutionRail):
    def __init__(self, processor: TrajectorySpanProcessor, *, async_evolution: bool) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.NONE,
            async_evolution=async_evolution,
            trajectory_span_processor=processor,
        )
        self.prepared_inputs = []

    async def run_evolution(self, prepared) -> None:
        self.prepared_inputs.append(prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("async_evolution", [False, True])
async def test_sync_and_background_paths_share_one_frozen_prepared_input(async_evolution: bool) -> None:
    rail = _ProbeEvolutionRail(TrajectorySpanProcessor(), async_evolution=async_evolution)

    await rail._trigger_evolution(_trajectory(), _ctx())
    if async_evolution:
        await rail.drain_pending_host_events(wait=True)

    assert len(rail.prepared_inputs) == 1
    prepared = rail.prepared_inputs[0]
    assert prepared.trajectory.session_id == "session"
    with pytest.raises(FrozenInstanceError):
        prepared.trajectory = _trajectory("other")
