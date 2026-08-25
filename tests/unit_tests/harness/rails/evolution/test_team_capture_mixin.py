# coding: utf-8
"""Focused tests for team-scoped capture on leader-mounted skill rails."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import iter_spans
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.evolution.evolution_rail import _TeamTrajectoryCaptureMixin
import openjiuwen.harness.rails.evolution.evolution_rail as evolution_rail_module
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import TeamSkillEvolutionRail
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail


def _span(name: str, trace_id: int, span_id: int) -> ReadableSpan:
    return ReadableSpan(
        name=name,
        context=SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        ),
        resource=Resource.create({"producer": "test"}),
        kind=SpanKind.INTERNAL,
        attributes={},
        status=Status(StatusCode.OK),
        start_time=span_id,
        end_time=span_id + 1,
    )


def _root(*, trace_id: int = 0x123, team_name: str | None = "team-a", recording: bool = True):
    from openjiuwen.extensions.observability import semconv

    return SimpleNamespace(
        context=SimpleNamespace(trace_id=trace_id),
        attributes={semconv.AT_TEAM_NAME: team_name} if team_name is not None else {},
        is_recording=lambda: recording,
    )


@pytest.mark.asyncio
async def test_team_skill_create_captures_same_trace_member_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = TrajectorySpanProcessor()
    rail = TeamSkillCreateRail(skills_dir="", trajectory_span_processor=processor)
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: _root())
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=InvokeInputs(query="run", conversation_id="session-a"),
    )

    await rail.before_invoke(ctx)
    capture = rail._current_capture()
    assert capture is not None
    assert capture.scope_key == ("team", "team-a", "session-a")
    assert capture.team_id == "team-a"

    processor.on_end(_span("agent.member.task_iteration.1", 0x123, 1))
    processor.on_end(_span("agent.other.task_iteration.1", 0x456, 2))
    await rail.after_invoke(ctx)

    assert rail._trajectory is not None
    assert [span["name"] for span in iter_spans(rail._trajectory)] == ["agent.member.task_iteration.1"]
    assert processor.drain(capture.subscription) == (None, ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("root", "message"),
    [
        (None, "active root span"),
        (_root(recording=False), "recording root span"),
        (_root(trace_id=0), "non-zero trace_id"),
        (_root(team_name=None), "AT_TEAM_NAME"),
    ],
)
async def test_team_skill_capture_fails_without_complete_team_identity(
    monkeypatch: pytest.MonkeyPatch,
    root: object,
    message: str,
) -> None:
    rail = TeamSkillCreateRail(skills_dir="", trajectory_span_processor=TrajectorySpanProcessor())
    monkeypatch.setattr(evolution_rail_module, "get_root_span", lambda: root)
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=InvokeInputs(query="run", conversation_id="session-a"),
    )

    with pytest.raises(RuntimeError, match=message):
        await rail.before_invoke(ctx)


def test_team_skill_rails_share_the_private_team_capture_mixin() -> None:
    assert issubclass(TeamSkillCreateRail, _TeamTrajectoryCaptureMixin)
    assert issubclass(TeamSkillEvolutionRail, _TeamTrajectoryCaptureMixin)


def test_team_recorder_integration_and_public_team_trajectory_rail_are_removed() -> None:
    from openjiuwen.core.runner.team_runner import _TeamRunnerMixin
    from openjiuwen.harness.rails.evolution import trajectory_rail

    assert not hasattr(_TeamRunnerMixin, "_begin_team_trajectory_recorders")
    assert not hasattr(_TeamRunnerMixin, "_finish_team_trajectory_recorders")
    assert not hasattr(trajectory_rail, "TeamTrajectoryRail")
