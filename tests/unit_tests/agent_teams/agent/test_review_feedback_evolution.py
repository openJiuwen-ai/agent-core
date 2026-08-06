# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the product-agnostic reviewer-feedback evolution coordinator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.agent_teams.agent.scheduling.review_feedback_evolution import (
    GLOBAL_EVOLUTION_EVENTS,
    SKILL_CREATION_EVENTS,
    ReviewFeedbackEvolutionCoordinator,
)
from openjiuwen.extensions.observability import semconv


class _AttributionLLM:
    def __init__(self, payloads: dict | list[dict]) -> None:
        self._payloads = payloads if isinstance(payloads, list) else [payloads]
        self.calls = 0

    async def invoke(self, **_kwargs):
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return {"content": json.dumps(payload)}


class _TrajectoryRegistry:
    def __init__(self, trajectory) -> None:
        self.trajectory = trajectory
        self.calls: list[dict] = []

    def get_trajectory(self, **kwargs):
        self.calls.append(kwargs)
        return self.trajectory


def _trajectory(skill_md: str | None):
    spans = []
    if skill_md is not None:
        spans.append(
            {
                "traceId": "trace-1",
                "spanId": "tool-1",
                "name": "tool.read_file",
                "attributes": attributes_from_map(
                    {
                        semconv.GEN_AI_TOOL_NAME: "read_file",
                        semconv.GEN_AI_TOOL_INPUT: {"path": skill_md},
                        semconv.GEN_AI_TOOL_OUTPUT: "# xlsx",
                    }
                ),
            }
        )
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {TRAJECTORY_ID: "trace-1", SESSION_ID: "sess-1"}
                        )
                    },
                    "scopeSpans": [{"scope": {}, "spans": spans}],
                }
            ]
        }
    )


def _rail(tmp_path, llm):
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: xlsx\ndescription: Build spreadsheets\n---\n\nValidate output.\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        evolution_store=EvolutionStore(str(tmp_path)),
        evolver=SimpleNamespace(llm=llm, model="test-model"),
        auto_save=False,
        evolve_from_external_signals=AsyncMock(
            return_value=SimpleNamespace(
                skill_name="xlsx",
                status="staged",
                request=SimpleNamespace(request_id="skill_evolve_1"),
            )
        ),
        drain_pending_approval_events=AsyncMock(return_value=[]),
    )


def _coordinator(
    *,
    tmp_path,
    llm,
    trajectory,
    member_rail=None,
    creation_rail=None,
    event_sink=None,
    enabled=True,
):
    global_rail = _rail(tmp_path, llm)
    member = member_rail or _rail(tmp_path, llm)
    registry = _TrajectoryRegistry(trajectory)
    coordinator = ReviewFeedbackEvolutionCoordinator(
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=registry,
        global_rail_provider=lambda: global_rail,
        member_rail_provider=lambda _assignee, _global: member,
        skill_create_rail_provider=lambda: creation_rail,
        event_sink=event_sink,
        enabled=enabled,
        min_confidence=0.7,
    )
    return coordinator, global_rail, member, registry


@pytest.mark.asyncio
async def test_failed_review_evolves_member_then_promotes_global(tmp_path):
    llm = _AttributionLLM(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "validation guidance is incomplete",
            "reusable_guidance": "Reopen and validate before delivery.",
            "is_reusable": True,
            "confidence": 0.93,
        }
    )
    skill_md = str(tmp_path / "xlsx" / "SKILL.md")
    coordinator, global_rail, member_rail, registry = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=_trajectory(skill_md),
    )

    await coordinator(
        {
            "task_id": "task-1",
            "review_round": 1,
            "task_title": "Create workbook",
            "assignee": "worker-1",
            "feedback": "The workbook was not validated.",
        }
    )

    member_rail.evolve_from_external_signals.assert_awaited_once()
    assert member_rail.evolve_from_external_signals.await_args.kwargs["requires_approval"] is False
    assert await coordinator.on_team_completed() is True
    global_rail.evolve_from_external_signals.assert_awaited_once()
    global_call = global_rail.evolve_from_external_signals.await_args.kwargs
    assert global_call["requires_approval"] is True
    assert "task=task-1" in global_call["user_query"]
    assert await coordinator.on_team_completed() is False
    assert registry.calls == [
        {"team_id": "team-1", "session_id": "sess-1", "filter_collaborative": False},
        {"team_id": "team-1", "session_id": "sess-1", "filter_collaborative": False},
    ]


@pytest.mark.asyncio
async def test_unread_skill_never_evolves(tmp_path):
    llm = _AttributionLLM(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "guidance is incomplete",
            "reusable_guidance": "Add validation.",
            "is_reusable": True,
            "confidence": 0.99,
        }
    )
    coordinator, global_rail, member_rail, _ = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=_trajectory(None),
    )

    await coordinator(
        {
            "task_id": "task-1",
            "review_round": 1,
            "assignee": "worker-1",
            "feedback": "The workbook was not validated.",
        }
    )

    member_rail.evolve_from_external_signals.assert_not_awaited()
    global_rail.evolve_from_external_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_repeated_pattern_routes_creation_and_events(tmp_path):
    llm = _AttributionLLM(
        {
            "classification": "new_skill_pattern",
            "skill_name": "",
            "target": None,
            "reason": "release recovery is missing",
            "reusable_guidance": "Create a reusable release recovery checklist.",
            "is_reusable": True,
            "confidence": 0.91,
        }
    )
    creation_rail = SimpleNamespace(
        propose_from_external_evidence=AsyncMock(return_value=True),
        drain_pending_approval_events=AsyncMock(return_value=[{"approval": True}]),
    )
    delivered: list[tuple[str, list]] = []

    async def event_sink(group, events):
        delivered.append((group, list(events)))

    coordinator, global_rail, member_rail, _ = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=_trajectory(None),
        creation_rail=creation_rail,
        event_sink=event_sink,
    )
    for task_id in ("task-a", "task-b"):
        await coordinator(
            {
                "task_id": task_id,
                "review_round": 1,
                "assignee": f"worker-{task_id[-1]}",
                "feedback": "The release plan omitted recovery policy.",
            }
        )

    assert await coordinator.on_team_completed() is True
    creation_rail.propose_from_external_evidence.assert_awaited_once()
    evidence = creation_rail.propose_from_external_evidence.await_args.kwargs["evidence"]
    assert len(evidence) == 2
    assert delivered == [(SKILL_CREATION_EVENTS, [{"approval": True}])]
    member_rail.evolve_from_external_signals.assert_not_awaited()
    global_rail.evolve_from_external_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_pending_events_use_injected_sink(tmp_path):
    llm = _AttributionLLM({})
    delivered: list[tuple[str, list]] = []

    async def event_sink(group, events):
        delivered.append((group, list(events)))

    coordinator, global_rail, _, _ = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=_trajectory(None),
        event_sink=event_sink,
    )
    global_rail.drain_pending_approval_events = AsyncMock(
        return_value=[{"event_type": "chat.delta"}]
    )

    await coordinator._push_pending_events(global_rail)

    assert delivered == [(GLOBAL_EVOLUTION_EVENTS, [{"event_type": "chat.delta"}])]


@pytest.mark.asyncio
async def test_disabled_coordinator_does_not_resolve_rails(tmp_path):
    global_provider = AsyncMock()
    coordinator = ReviewFeedbackEvolutionCoordinator(
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=None,
        global_rail_provider=global_provider,
        member_rail_provider=lambda _assignee, _global: None,
        enabled=False,
    )

    await coordinator(
        {
            "task_id": "task-1",
            "review_round": 1,
            "assignee": "worker-1",
            "feedback": "failed",
        }
    )

    global_provider.assert_not_called()
