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
    TrajectoryBuilder,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
    set_trajectory_resource_attributes,
    trajectory_execution_id,
    trajectory_from_steps,
    trajectory_meta,
    trajectory_steps,
)
from openjiuwen.agent_evolving.trajectory.aggregator import filter_member_trajectory
from openjiuwen.agent_evolving.trajectory.aggregator import aggregate_member_trajectories


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
                detail=ToolCallDetail(tool_name="view_task"),
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

            assert len(trajectory_steps(result.combined)) == 3
            assert trajectory_meta(result.combined).get("member_count") == 1


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

            assert len(trajectory_steps(result.combined)) == 4
            times = [s.start_time_ms for s in trajectory_steps(result.combined)]
            assert times == sorted(times)
            assert trajectory_meta(result.combined).get("member_count") == 2


class TestTeamTrajectoryAggregatorEmpty:
    """Test with no matching trajectories."""

    def test_empty_session_returns_empty_combined(self):
        """Aggregator with no matching data returns empty combined."""
        with tempfile.TemporaryDirectory() as tmp:
            aggregator = TeamTrajectoryAggregator(trajectories_dir=Path(tmp), team_id="team-1")
            result = aggregator.aggregate("nonexistent-session")

            assert len(result.members) == 0
            assert len(trajectory_steps(result.combined)) == 0
            assert trajectory_meta(result.combined).get("member_count") == 0

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
        traj = trajectory_from_steps(
            execution_id="exec-1",
            session_id="sess-1",
            steps=steps,
            meta={"member_id": "researcher"},
        )

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 0

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
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 1
        assert trajectory_steps(result)[0].detail.tool_name == "claim_task"

    def test_keeps_cross_member_invoke(self):
        """Steps with cross-member interaction markers should be kept."""
        steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"invoke_id": "inv-1", "parent_invoke_id": "parent-1"},
            ),
        ]
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 1

    def test_filters_internal_file_edit(self):
        """Tools outside the collaboration whitelist should be filtered out."""
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
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 0

    def test_filters_unknown_tool_calls(self):
        """Unknown tools are not collaboration evidence by default."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="custom_debug_tool"),
                meta={"operator_id": "custom_debug_tool"},
            ),
        ]
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 0

    def test_filters_regular_file_read(self):
        """File access is only kept when it targets skill-related files."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="notes.txt"),
                meta={"operator_id": "read_file"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="write_file", call_args="src/app.py"),
                meta={"operator_id": "write_file"},
            ),
        ]
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 0

    def test_keeps_skill_file_access(self):
        """read_file/write_file for SKILL.md should be kept."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="team_skills/research/SKILL.md"),
                meta={"operator_id": "read_file"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="write_file", call_args="team_skills/research/SKILL.md"),
                meta={"operator_id": "write_file"},
            ),
        ]
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 2

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
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 2
        assert trajectory_steps(result)[0].detail.tool_name == "claim_task"
        assert trajectory_steps(result)[1].detail.tool_name == "view_task"

    def test_empty_trajectory_returns_empty(self):
        """Empty trajectory returns empty, preserves execution_id."""
        traj = trajectory_from_steps(execution_id="e1", session_id="s1", steps=[])

        result = filter_member_trajectory(traj)

        assert len(trajectory_steps(result)) == 0
        assert trajectory_execution_id(result) == "e1"

    def test_preserves_original_execution_id(self):
        """Filtering should preserve the original execution_id."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="claim_task"),
                meta={"operator_id": "claim_task"},
            ),
        ]
        traj = trajectory_from_steps(execution_id="my-exec-123", session_id="s1", steps=steps)

        result = filter_member_trajectory(traj)

        assert trajectory_execution_id(result) == "my-exec-123"


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
        store.save(
            trajectory_from_steps(
                execution_id="e1",
                session_id="s1",
                steps=steps1,
                meta={"member_id": "m1"},
            )
        )

        # Member 2: only collaborative
        steps2 = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="team_skills/x/SKILL.md"),
                start_time_ms=150,
            ),
        ]
        store.save(
            trajectory_from_steps(
                execution_id="e2",
                session_id="s1",
                steps=steps2,
                meta={"member_id": "m2"},
            )
        )

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        assert len(result.members) == 2
        # Only collaborative steps: claim_task + view_task + read_file = 3
        assert len(trajectory_steps(result.combined)) == 3

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
        store.save(
            trajectory_from_steps(
                execution_id="e1",
                session_id="s1",
                steps=steps,
                meta={"member_id": "m1"},
            )
        )

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1", filter_collaborative=False)

        assert len(trajectory_steps(result.combined)) == 2

    def test_aggregate_keeps_full_leader_by_role_and_filters_members(self):
        """Leader keeps full trajectory while teammates keep only collaborative steps."""
        store = InMemoryTrajectoryStore()
        leader_id = "jiuwen_team_sess_123_team_leader"

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

        store.save(
            trajectory_from_steps(
                execution_id="leader-exec",
                session_id="s1",
                steps=leader_steps,
                meta={"member_id": leader_id, "member_role": "leader"},
            )
        )
        store.save(
            trajectory_from_steps(
                execution_id="member-exec",
                session_id="s1",
                steps=member_steps,
                meta={"member_id": "researcher", "member_role": "teammate"},
            )
        )

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        assert len(trajectory_steps(result.members[leader_id])) == 2
        assert trajectory_steps(result.members[leader_id])[0].kind == "llm"
        assert len(trajectory_steps(result.members["researcher"])) == 1
        assert trajectory_steps(result.members["researcher"])[0].detail.tool_name == "read_file"
        assert len(trajectory_steps(result.combined)) == 3
        assert [step.kind for step in trajectory_steps(result.combined)] == ["llm", "tool", "tool"]

    def test_aggregate_accumulates_multiple_trajectories_for_same_member(self):
        """Multiple trajectories from one member should be merged, not overwritten."""
        store = InMemoryTrajectoryStore()
        leader_id = "jiuwen_team_sess_123_team_leader"

        store.save(
            trajectory_from_steps(
                execution_id="leader-round-1",
                session_id="s1",
                steps=[
                    TrajectoryStep(
                        kind="tool",
                        detail=ToolCallDetail(
                            tool_name="skill_tool",
                            call_args={"skill_name": "short-video-production-swarm"},
                        ),
                        start_time_ms=100,
                    ),
                ],
                meta={"member_id": leader_id, "member_role": "leader"},
            )
        )
        store.save(
            trajectory_from_steps(
                execution_id="leader-round-2",
                session_id="s1",
                steps=[
                    TrajectoryStep(
                        kind="tool",
                        detail=ToolCallDetail(tool_name="view_task"),
                        start_time_ms=200,
                    ),
                ],
                meta={"member_id": leader_id, "member_role": "leader"},
            )
        )

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        tool_names = [step.detail.tool_name for step in trajectory_steps(result.members[leader_id])]
        assert tool_names == ["skill_tool", "view_task"]

    def test_aggregate_deduplicates_cumulative_snapshots_for_same_member(self):
        """Cumulative snapshots from one builder should not duplicate prefix steps."""
        store = InMemoryTrajectoryStore()
        leader_id = "jiuwen_team_sess_123_team_leader"
        skill_step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="skill_tool",
                call_args={"skill_name": "short-video-production-swarm"},
            ),
            start_time_ms=100,
        )
        view_task_step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(tool_name="view_task"),
            start_time_ms=200,
        )

        store.save(
            trajectory_from_steps(
                execution_id="leader-snapshot-1",
                session_id="s1",
                steps=[skill_step],
                meta={"member_id": leader_id, "member_role": "leader"},
            )
        )
        store.save(
            trajectory_from_steps(
                execution_id="leader-snapshot-2",
                session_id="s1",
                steps=[skill_step, view_task_step],
                meta={"member_id": leader_id, "member_role": "leader"},
            )
        )

        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("s1")

        tool_names = [step.detail.tool_name for step in trajectory_steps(result.members[leader_id])]
        assert tool_names == ["skill_tool", "view_task"]

    def test_aggregate_from_empty_store(self):
        """Aggregator from empty store returns empty combined."""
        store = InMemoryTrajectoryStore()
        agg = TeamTrajectoryAggregator(store=store, team_id="t1")
        result = agg.aggregate("nonexistent")
        assert len(result.members) == 0
        assert len(trajectory_steps(result.combined)) == 0

    def test_backward_compat_trajectories_dir(self):
        """Old API: trajectories_dir=Path should still work."""
        with tempfile.TemporaryDirectory() as tmp:
            store = FileTrajectoryStore(Path(tmp))
            traj = trajectory_from_steps(
                execution_id="e1",
                session_id="s1",
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


def test_aggregate_member_trajectories_uses_latest_prefix_snapshot():
    old = _build_member_trajectory("leader", "session-1", step_count=1)
    set_trajectory_resource_attributes(old, {"member_role": "leader"})
    new = _build_member_trajectory("leader", "session-1", step_count=2)
    set_trajectory_resource_attributes(new, {"member_role": "leader"})

    combined = aggregate_member_trajectories(
        [old, new],
        team_id="team-1",
        session_id="session-1",
        filter_collaborative=True,
    )

    assert len(trajectory_steps(combined)) == 2
    assert trajectory_meta(combined)["member_count"] == 1


def test_aggregate_member_trajectories_filters_teammate_internals():
    teammate = trajectory_from_steps(
        execution_id="teammate-1",
        session_id="session-1",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "researcher/llm_main"},
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="send_message"),
                meta={"operator_id": "send_message"},
                start_time_ms=2,
            ),
        ],
        source="online",
        meta={"member_id": "researcher", "member_role": "teammate"},
    )

    combined = aggregate_member_trajectories(
        [teammate],
        team_id="team-1",
        session_id="session-1",
        filter_collaborative=True,
    )

    assert len(trajectory_steps(combined)) == 1
    assert trajectory_steps(combined)[0].detail.tool_name == "send_message"
