# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TeamTrajectoryAggregator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.trajectory import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
    TeamTrajectoryAggregator,
    Trajectory,
    TrajectoryBuilder,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    TrajectoryStep,
)
from openjiuwen.agent_evolving.trajectory.aggregator import filter_member_trajectory


def _build_member_trajectory(
    member_id: str,
    session_id: str,
    step_count: int = 2,
) -> Trajectory:
    builder = TrajectoryBuilder(
        session_id=session_id,
        source="online",
        member_id=member_id,
    )
    for i in range(step_count):
        builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name=f"tool_{i}"),
                start_time_ms=1000 * i,
            )
        )
    return builder.build()


class TestTeamTrajectoryAggregatorSingleMember:
    """Test with single member trajectory."""

    def test_single_member_aggregation(self):
        """Aggregator with one member returns members with 1 entry."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            traj = _build_member_trajectory("member-1", "session-1")
            store.save(traj)

            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("session-1")

            assert len(result.members) == 1
            assert "member-1" in result.members

    def test_single_member_combined_equals_member(self):
        """Combined steps should match the single member's steps."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            traj = _build_member_trajectory("member-1", "session-1", step_count=3)
            store.save(traj)

            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("session-1")

            assert len(result.combined.steps) == 3
            assert result.combined.meta.get("member_count") == 1


class TestTeamTrajectoryAggregatorMultiMember:
    """Test with multiple member trajectories."""

    def test_multi_member_aggregation(self):
        """Aggregator with multiple members groups by member_id."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            t1 = _build_member_trajectory("member-1", "session-1", step_count=2)
            t2 = _build_member_trajectory("member-2", "session-1", step_count=3)
            store.save(t1)
            store.save(t2)

            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("session-1")

            assert len(result.members) == 2
            assert "member-1" in result.members
            assert "member-2" in result.members

    def test_combined_steps_sorted_by_time(self):
        """Combined steps should be sorted by start_time_ms."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            t1 = _build_member_trajectory("member-1", "session-1", step_count=2)
            t2 = _build_member_trajectory("member-2", "session-1", step_count=2)
            store.save(t1)
            store.save(t2)

            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("session-1")

            assert len(result.combined.steps) == 4
            times = [s.start_time_ms for s in result.combined.steps]
            assert times == sorted(times)
            assert result.combined.meta.get("member_count") == 2


class TestTeamTrajectoryAggregatorEmpty:
    """Test with no matching trajectories."""

    def test_empty_session_returns_empty_combined(self):
        """Aggregator with no matching data returns empty combined."""
        with tempfile.TemporaryDirectory() as tmp:
            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("nonexistent-session")

            assert len(result.members) == 0
            assert len(result.combined.steps) == 0
            assert result.combined.meta.get("member_count") == 0

    def test_empty_dir_does_not_raise(self):
        """Aggregator on empty directory should not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("any-session")
            assert result is not None


class TestFilterMemberTrajectory:
    """Test member trajectory filtering rules."""

    def test_filters_internal_llm_steps(self):
        """Internal LLM reasoning steps should be filtered out."""
        steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "researcher/llm_main"},
            ),
        ]
        traj = Trajectory(
            execution_id="exec-1",
            session_id="sess-1",
            steps=steps,
            meta={"member_id": "researcher"},
        )

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 0

    def test_keeps_collaborative_tool_calls(self):
        """Collaborative tool calls should be kept.

        Note: spawn_member is Leader-only, not included in Teammate context.
        Using claim_task (Teammate-only) and view_task as representative collaborative tools.
        """
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="claim_task", call_args={"task_id": "t1"}),
                meta={"operator_id": "claim_task"},
            ),
        ]
        traj = Trajectory(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 1
        assert result.steps[0].detail.tool_name == "claim_task"

    def test_keeps_cross_member_invoke(self):
        """Steps with cross-member interaction markers should be kept."""
        steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"invoke_id": "inv-1", "parent_invoke_id": "parent-1"},
            ),
        ]
        traj = Trajectory(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 1

    def test_filters_internal_file_edit(self):
        """Pure internal code execution tools should be filtered out."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="bash", call_args="python x.py"),
                meta={"operator_id": "bash"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="python", call_args="import os"),
                meta={"operator_id": "python"},
            ),
        ]
        traj = Trajectory(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 0

    def test_keeps_skill_read(self):
        """read_file for SKILL.md should be kept."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="read_file", call_args="team_skills/research/SKILL.md"
                ),
                meta={"operator_id": "read_file"},
            ),
        ]
        traj = Trajectory(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 1

    def test_mixed_steps_keep_only_collaborative(self):
        """Mixed steps should only keep collaborative ones.

        Note: spawn_member is Leader-only, using claim_task and view_task
        as representative Teammate collaborative tools.
        """
        steps = [
            # Collaborative: claim_task (Teammate-only)
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="claim_task", call_args={"task_id": "t1"}),
                meta={"operator_id": "claim_task"},
                start_time_ms=100,
            ),
            # Internal: LLM reasoning
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "teammate/llm_main"},
                start_time_ms=200,
            ),
            # Collaborative: view_task results
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="view_task", call_args={}),
                meta={"operator_id": "view_task"},
                start_time_ms=300,
            ),
        ]
        traj = Trajectory(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 2
        assert result.steps[0].detail.tool_name == "claim_task"
        assert result.steps[1].detail.tool_name == "view_task"

    def test_empty_trajectory_returns_empty(self):
        """Empty trajectory returns empty, preserves execution_id."""
        traj = Trajectory(execution_id="e1", session_id="s1", steps=[])

        result = filter_member_trajectory(traj)

        assert len(result.steps) == 0
        assert result.execution_id == "e1"

    def test_preserves_original_execution_id(self):
        """Filtering should preserve the original execution_id."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="claim_task"),
                meta={"operator_id": "claim_task"},
            ),
        ]
        traj = Trajectory(execution_id="my-exec-123", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert result.execution_id == "my-exec-123"


class TestTeamTrajectoryAggregatorWithStore:
    """Test aggregator with TrajectoryStore protocol (not file-based)."""

    def test_aggregate_from_store_filters_collaborative(self):
        """Aggregator from store should filter out internal steps.

        Note: spawn_member is Leader-only, using claim_task and send_message
        as representative Teammate collaborative tools.
        """
        store = InMemoryTrajectoryStore()

        # Member 1: collaborative + internal mixed
        steps1 = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="claim_task"),
                start_time_ms=100,
                meta={"invoke_id": "i1"},
            ),
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                start_time_ms=200,
                meta={"operator_id": "m1/llm_main"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="view_task"),
                start_time_ms=300,
            ),
        ]
        store.save(Trajectory(
            execution_id="e1", session_id="s1", steps=steps1,
            meta={"member_id": "m1"},
        ))

        # Member 2: only collaborative
        steps2 = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="team_skills/x/SKILL.md"),
                start_time_ms=150,
            ),
        ]
        store.save(Trajectory(
            execution_id="e2", session_id="s1", steps=steps2,
            meta={"member_id": "m2"},
        ))

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        assert len(result.members) == 2
        # Only collaborative steps: claim_task + view_task + read_file = 3
        assert len(result.combined.steps) == 3

    def test_aggregate_from_store_no_filter(self):
        """Aggregator with filter_collaborative=False keeps all steps."""
        store = InMemoryTrajectoryStore()

        steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                start_time_ms=100,
                meta={"operator_id": "m1/llm_main"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="send_message"),
                start_time_ms=200,
            ),
        ]
        store.save(Trajectory(
            execution_id="e1", session_id="s1", steps=steps,
            meta={"member_id": "m1"},
        ))

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1", filter_collaborative=False)

        assert len(result.combined.steps) == 2

    def test_aggregate_keeps_full_leader_and_filters_members(self):
        """Leader keeps full trajectory while teammates keep only collaborative steps."""
        store = InMemoryTrajectoryStore()

        leader_steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                start_time_ms=100,
                meta={"operator_id": "leader/llm_main"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="view_task"),
                start_time_ms=200,
                meta={"operator_id": "view_task"},
            ),
        ]
        member_steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                start_time_ms=150,
                meta={"operator_id": "researcher/llm_main"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="team_skills/x/SKILL.md"),
                start_time_ms=250,
                meta={"operator_id": "read_file"},
            ),
        ]

        store.save(Trajectory(
            execution_id="leader-exec", session_id="s1", steps=leader_steps,
            meta={"member_id": "leader"},
        ))
        store.save(Trajectory(
            execution_id="member-exec", session_id="s1", steps=member_steps,
            meta={"member_id": "researcher"},
        ))

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        assert len(result.members["leader"].steps) == 2
        assert result.members["leader"].steps[0].kind == "llm"
        assert len(result.members["researcher"].steps) == 1
        assert result.members["researcher"].steps[0].detail.tool_name == "read_file"
        assert len(result.combined.steps) == 3
        assert [step.kind for step in result.combined.steps] == ["llm", "tool", "tool"]

    def test_aggregate_from_empty_store(self):
        """Aggregator from empty store returns empty combined."""
        store = InMemoryTrajectoryStore()
        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("nonexistent")
        assert len(result.members) == 0
        assert len(result.combined.steps) == 0

    def test_backward_compat_trajectories_dir(self):
        """Old API: trajectories_dir=Path should still work."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            traj = Trajectory(
                execution_id="e1", session_id="s1",
                steps=[TrajectoryStep(kind="tool", detail=ToolCallDetail(tool_name="claim_task"), start_time_ms=100)],
                meta={"member_id": "m1"},
            )
            store.save(traj)

            agg = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="t1")
            result = agg.aggregate("s1")

            assert len(result.members) == 1

    def test_requires_store_or_trajectories_dir(self):
        """Aggregator requires either store or trajectories_dir."""
        with pytest.raises(ValueError, match="Either"):
            TeamTrajectoryAggregator(team_id="t1")
