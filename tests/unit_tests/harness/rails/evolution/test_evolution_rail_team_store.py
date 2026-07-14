# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for EvolutionRail trajectory sink publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openjiuwen.agent_evolving.trajectory import (
    FileTrajectoryStore,
    InMemoryTrajectoryRegistry,
    InMemoryTrajectoryStore,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.rail.base import InvokeInputs, ModelCallInputs, ToolCallInputs
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail


@dataclass
class _MockCard:
    id: str = "test-agent"


@dataclass
class _MockAgent:
    card: _MockCard = field(default_factory=_MockCard)
    role: TeamRole = TeamRole.LEADER


@dataclass
class _MockAgentWithoutRole:
    card: _MockCard = field(default_factory=lambda: _MockCard(id="jiuwen_team_a_team_leader"))


@dataclass
class _MockCtx:
    agent: Any = field(default_factory=_MockAgent)
    inputs: Any = field(
        default_factory=lambda: InvokeInputs(query="test query", conversation_id="test-session"),
    )


class _CaptureSink:
    def __init__(self) -> None:
        self.snapshots = []

    def publish_member_trajectory(self, snapshot) -> None:
        self.snapshots.append(snapshot)


async def _record_tool_invoke(
    rail: EvolutionRail,
    *,
    conversation_id: str = "test-session",
    tool_name: str = "view_task",
    tool_result: Any = None,
) -> None:
    invoke_ctx = _MockCtx(inputs=InvokeInputs(query="test query", conversation_id=conversation_id))
    await rail.before_invoke(invoke_ctx)
    await rail.after_tool_call(
        _MockCtx(
            inputs=ToolCallInputs(
                tool_name=tool_name,
                tool_args={},
                tool_result=tool_result,
            )
        )
    )
    await rail.after_invoke(invoke_ctx)


class TestEvolutionRailTeamTrajectory:
    """Tests for trajectory sink publication."""

    @pytest.fixture
    def rail_with_team_sink(self):
        personal = InMemoryTrajectoryStore()
        sink = _CaptureSink()
        rail = EvolutionRail(
            trajectory_store=personal,
            async_evolution=False,
        )
        rail.set_trajectory_sink(sink, team_id="team-a")
        return rail, personal, sink

    @pytest.mark.asyncio
    async def test_after_invoke_publishes_member_snapshot(self, rail_with_team_sink):
        rail, personal, sink = rail_with_team_sink
        await _record_tool_invoke(rail)

        assert len(personal.query()) == 1
        assert len(sink.snapshots) == 1
        snapshot = sink.snapshots[0]
        assert snapshot.team_id == "team-a"
        assert snapshot.session_id == "test-session"
        assert snapshot.member_id == "test-agent"
        assert snapshot.member_role is None
        assert len(snapshot.trajectory.steps) == 1

    @pytest.mark.asyncio
    async def test_after_invoke_without_sink_only_saves_personal_store(self):
        personal = InMemoryTrajectoryStore()
        rail = EvolutionRail(
            trajectory_store=personal,
            async_evolution=False,
        )
        await _record_tool_invoke(rail)

        assert len(personal.query()) == 1

    @pytest.mark.asyncio
    async def test_after_invoke_publishes_snapshot_each_time(self, rail_with_team_sink):
        rail, personal, sink = rail_with_team_sink
        await _record_tool_invoke(rail)
        await _record_tool_invoke(rail)

        assert len(personal.query()) == 2
        assert len(sink.snapshots) == 2
        assert all(snapshot.team_id == "team-a" for snapshot in sink.snapshots)
        assert all(snapshot.member_id == "test-agent" for snapshot in sink.snapshots)
        assert [len(snapshot.trajectory.steps) for snapshot in sink.snapshots] == [1, 2]

    def test_team_trajectory_store_is_deprecated(self):
        with pytest.warns(DeprecationWarning, match="team_trajectory_store"):
            rail = EvolutionRail(team_trajectory_store=InMemoryTrajectoryStore())

        assert rail is not None

    def test_trajectory_sink_requires_team_id(self):
        rail = EvolutionRail(async_evolution=False)
        sink = _CaptureSink()

        with pytest.raises(ValueError, match="team_id is required"):
            rail.set_trajectory_sink(sink, team_id=None)

    @pytest.mark.asyncio
    async def test_base_sink_binding_without_member_role_does_not_invent_role(self):
        personal = InMemoryTrajectoryStore()
        sink = _CaptureSink()
        rail = EvolutionRail(
            trajectory_store=personal,
            async_evolution=False,
        )
        rail.set_trajectory_sink(sink, team_id="team-a")

        agent = _MockAgentWithoutRole()
        invoke_ctx = _MockCtx(
            agent=agent,
            inputs=InvokeInputs(query="test query", conversation_id="test-session"),
        )
        await rail.before_invoke(invoke_ctx)
        await rail.after_tool_call(
            _MockCtx(
                agent=agent,
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result={},
                ),
            )
        )
        await rail.after_invoke(invoke_ctx)

        snapshot = sink.snapshots[0]
        assert snapshot.member_role is None
        assert "member_role" not in snapshot.trajectory.meta

    @pytest.mark.asyncio
    async def test_bound_member_role_fills_snapshot_when_ctx_agent_has_no_role(self):
        personal = InMemoryTrajectoryStore()
        sink = _CaptureSink()
        rail = EvolutionRail(
            trajectory_store=personal,
            async_evolution=False,
        )
        rail.set_trajectory_sink(sink, team_id="team-a", member_role=TeamRole.LEADER)

        agent = _MockAgentWithoutRole()
        invoke_ctx = _MockCtx(
            agent=agent,
            inputs=InvokeInputs(query="test query", conversation_id="test-session"),
        )
        await rail.before_invoke(invoke_ctx)
        await rail.after_tool_call(
            _MockCtx(
                agent=agent,
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result={},
                ),
            )
        )
        await rail.after_invoke(invoke_ctx)

        snapshot = sink.snapshots[0]
        assert snapshot.member_id == "jiuwen_team_a_team_leader"
        assert snapshot.member_role == "leader"
        assert snapshot.trajectory.meta["member_role"] == "leader"

    @pytest.mark.asyncio
    async def test_bound_leader_role_keeps_llm_steps_in_team_aggregate_without_ctx_agent_role(self):
        registry = InMemoryTrajectoryRegistry()
        rail = EvolutionRail(async_evolution=False)
        rail.set_trajectory_sink(
            registry,
            team_id="team-a",
            member_role=TeamRole.LEADER,
        )

        agent = _MockAgentWithoutRole()
        invoke_ctx = _MockCtx(
            agent=agent,
            inputs=InvokeInputs(query="test query", conversation_id="test-session"),
        )
        await rail.before_invoke(invoke_ctx)
        await rail.after_model_call(
            _MockCtx(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "test query"}],
                    response={"role": "assistant", "content": "thinking"},
                ),
            )
        )
        await rail.after_tool_call(
            _MockCtx(
                agent=agent,
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result={},
                ),
            )
        )
        await rail.after_invoke(invoke_ctx)

        aggregated = registry.get_trajectory(team_id="team-a", session_id="test-session")

        assert aggregated is not None
        assert [step.kind for step in aggregated.steps] == ["llm", "tool"]

    @pytest.mark.asyncio
    async def test_registry_aggregate_uses_latest_trajectory_for_repeated_member(self, monkeypatch):
        monkeypatch.setattr(
            "openjiuwen.agent_evolving.trajectory.registry.now_ms",
            lambda: 12345,
        )
        registry = InMemoryTrajectoryRegistry()
        rail = EvolutionRail(async_evolution=False)
        rail.set_trajectory_sink(registry, team_id="team-a")

        await _record_tool_invoke(rail, tool_name="view_task")
        await _record_tool_invoke(rail, tool_name="send_message")

        aggregated = registry.get_trajectory(team_id="team-a", session_id="test-session")

        assert aggregated is not None
        assert [step.detail.tool_name for step in aggregated.steps if step.detail is not None] == [
            "view_task",
            "send_message",
        ]

    @pytest.mark.asyncio
    async def test_deprecated_team_trajectory_store_does_not_append_snapshots(self, tmp_path):
        team_store = FileTrajectoryStore(tmp_path)
        with pytest.warns(DeprecationWarning, match="team_trajectory_store"):
            rail = EvolutionRail(
                trajectory_store=InMemoryTrajectoryStore(),
                team_trajectory_store=team_store,
                async_evolution=False,
            )
        await _record_tool_invoke(rail, tool_name="read_file", tool_result="x" * 1000)
        await _record_tool_invoke(rail, tool_name="read_file", tool_result="y" * 1000)

        assert not (tmp_path / "trajectories_default.jsonl").exists()
