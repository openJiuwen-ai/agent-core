# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the product-agnostic reviewer-feedback evolution coordinator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.agent_teams.agent.scheduling.review_feedback_evolution import (
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


def _trajectory(skill_md: str | None, *, member_id: str = "worker-1"):
    spans = [
        {
            "traceId": "trace-1",
            "spanId": "team-1",
            "name": "team.run",
            "attributes": attributes_from_map({semconv.AT_TEAM_ID: "team-1"}),
        },
        {
            "traceId": "trace-1",
            "spanId": "member-1",
            "parentSpanId": "team-1",
            "name": "member.run",
            "attributes": attributes_from_map({semconv.AT_MEMBER_ID: member_id}),
        },
    ]
    if skill_md is not None:
        spans.append(
            {
                "traceId": "trace-1",
                "spanId": "tool-1",
                "parentSpanId": "member-1",
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
                    "resource": {"attributes": attributes_from_map({TRAJECTORY_ID: "trace-1", SESSION_ID: "sess-1"})},
                    "scopeSpans": [{"scope": {}, "spans": spans}],
                }
            ]
        }
    )


def _rail(tmp_path, llm, trajectory):
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
        get_trajectory=Mock(return_value=trajectory),
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
    creation_rail=None,
    event_sink=None,
    enabled=True,
):
    team_rail = _rail(tmp_path, llm, trajectory)
    coordinator = ReviewFeedbackEvolutionCoordinator(
        session_id="sess-1",
        team_id="team-1",
        team_rail_provider=lambda: team_rail,
        skill_create_rail_provider=lambda: creation_rail,
        event_sink=event_sink,
        enabled=enabled,
        min_confidence=0.7,
    )
    return coordinator, team_rail


@pytest.mark.asyncio
async def test_failed_review_is_retained_until_team_evolution(tmp_path):
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
    coordinator, team_rail = _coordinator(
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

    team_rail.evolve_from_external_signals.assert_not_awaited()
    assert await coordinator.on_team_completed() is True
    team_rail.evolve_from_external_signals.assert_awaited_once()
    team_call = team_rail.evolve_from_external_signals.await_args.kwargs
    assert team_call["requires_approval"] is True
    assert "task=task-1" in team_call["user_query"]
    assert await coordinator.on_team_completed() is False
    assert team_rail.get_trajectory.call_count == 2
    assert team_rail.get_trajectory.call_args.kwargs == {
        "session_id": "sess-1",
        "team_id": "team-1",
    }


@pytest.mark.asyncio
async def test_multiple_task_observations_share_one_terminal_team_evolution(tmp_path):
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
    trajectory = _trajectory(skill_md)
    coordinator, team_rail = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=trajectory,
    )

    for task_id in ("task-1", "task-2"):
        await coordinator(
            {
                "task_id": task_id,
                "review_round": 1,
                "task_title": "Create workbook",
                "assignee": "worker-1",
                "feedback": f"{task_id} workbook was not validated.",
            }
        )

    team_rail.evolve_from_external_signals.assert_not_awaited()
    assert await coordinator.on_team_completed() is True
    team_rail.evolve_from_external_signals.assert_awaited_once()
    team_call = team_rail.evolve_from_external_signals.await_args.kwargs
    assert len(team_call["signals"]) == 2
    assert "task=task-1" in team_call["user_query"]
    assert "task=task-2" in team_call["user_query"]


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
    coordinator, team_rail = _coordinator(
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

    team_rail.evolve_from_external_signals.assert_not_awaited()


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

    coordinator, team_rail = _coordinator(
        tmp_path=tmp_path,
        llm=llm,
        trajectory=_trajectory(None, member_id="worker-a"),
        creation_rail=creation_rail,
        event_sink=event_sink,
    )
    for task_id in ("task-a", "task-b"):
        await coordinator(
            {
                "task_id": task_id,
                "review_round": 1,
                "assignee": "worker-a",
                "feedback": "The release plan omitted recovery policy.",
            }
        )

    assert await coordinator.on_team_completed() is True
    creation_rail.propose_from_external_evidence.assert_awaited_once()
    evidence = creation_rail.propose_from_external_evidence.await_args.kwargs["evidence"]
    assert len(evidence) == 2
    assert delivered == [(SKILL_CREATION_EVENTS, [{"approval": True}])]
    team_rail.evolve_from_external_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_coordinator_does_not_resolve_rails(tmp_path):
    team_provider = AsyncMock()
    coordinator = ReviewFeedbackEvolutionCoordinator(
        session_id="sess-1",
        team_id="team-1",
        team_rail_provider=team_provider,
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

    team_provider.assert_not_called()
