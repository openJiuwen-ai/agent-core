# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for runtime trajectory source and sink."""

from __future__ import annotations

from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryRegistry,
    LLMCallDetail,
    MemberTrajectorySnapshot,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)


def _trajectory(
    member_id: str,
    session_id: str,
    tool_name: str,
    start_time_ms: int = 1,
) -> Trajectory:
    return Trajectory(
        execution_id=f"exec-{member_id}-{tool_name}",
        session_id=session_id,
        source="online",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name=tool_name),
                start_time_ms=start_time_ms,
            )
        ],
        meta={"member_id": member_id},
    )


def _snapshot(
    *,
    team_id: str = "team-a",
    session_id: str = "session-a",
    member_id: str = "leader",
    member_role: str | None = "leader",
    tool_name: str = "view_task",
    start_time_ms: int = 1,
    recorded_at_ms: int = 1000,
) -> MemberTrajectorySnapshot:
    return MemberTrajectorySnapshot.make(
        team_id=team_id,
        member_id=member_id,
        member_role=member_role,
        trajectory=_trajectory(member_id, session_id, tool_name, start_time_ms=start_time_ms),
        recorded_at_ms=recorded_at_ms,
    )


def _tool_names(trajectory: Trajectory | None) -> list[str]:
    assert trajectory is not None
    return [step.detail.tool_name for step in trajectory.steps if step.detail is not None]


def test_registry_returns_none_for_empty_session() -> None:
    registry = InMemoryTrajectoryRegistry()

    result = registry.get_trajectory(team_id="team-a", session_id="missing")

    assert result is None


def test_registry_uses_later_publish_order_for_same_timestamp() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(_snapshot(member_id="researcher", tool_name="old_tool"))
    registry.publish_member_trajectory(_snapshot(member_id="researcher", tool_name="new_tool"))

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert result is not None
    assert _tool_names(result) == ["new_tool"]
    assert result.meta["member_count"] == 1


def test_registry_keeps_newer_recorded_at_when_older_arrives_later() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(_snapshot(member_id="writer", tool_name="latest_tool", recorded_at_ms=2000))
    registry.publish_member_trajectory(_snapshot(member_id="writer", tool_name="stale_tool", recorded_at_ms=1000))

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert _tool_names(result) == ["latest_tool"]


def test_registry_accepts_newer_recorded_at_snapshot() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(
        _snapshot(
            member_id="writer",
            tool_name="before_restart",
            recorded_at_ms=1000,
        )
    )
    registry.publish_member_trajectory(
        _snapshot(
            member_id="writer",
            tool_name="after_restart",
            recorded_at_ms=2000,
        )
    )

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert _tool_names(result) == ["after_restart"]


def test_registry_merges_members_in_time_order() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(
        _snapshot(
            member_id="writer",
            member_role="teammate",
            tool_name="send_message",
            recorded_at_ms=1000,
            start_time_ms=300,
        )
    )
    registry.publish_member_trajectory(
        _snapshot(
            member_id="leader",
            member_role="leader",
            tool_name="view_task",
            recorded_at_ms=1001,
            start_time_ms=100,
        )
    )

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert result is not None
    assert _tool_names(result) == ["view_task", "send_message"]
    assert result.meta["member_count"] == 2


def test_registry_clear_session_removes_only_target_session() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(_snapshot(team_id="team-a", session_id="session-a"))
    registry.publish_member_trajectory(_snapshot(team_id="team-a", session_id="session-b"))

    registry.clear_session(team_id="team-a", session_id="session-a")

    assert registry.get_trajectory(team_id="team-a", session_id="session-a") is None
    assert registry.get_trajectory(team_id="team-a", session_id="session-b") is not None


def test_registry_uses_snapshot_member_metadata_for_aggregation() -> None:
    registry = InMemoryTrajectoryRegistry()
    registry.publish_member_trajectory(
        MemberTrajectorySnapshot(
            team_id="team-a",
            session_id="session-a",
            member_id="leader",
            member_role="leader",
            trajectory=Trajectory(
                execution_id="random-execution-id",
                session_id="session-a",
                source="online",
                steps=[
                    TrajectoryStep(
                        kind="llm",
                        detail=LLMCallDetail(model="mock", messages=[]),
                        meta={"operator_id": "leader/llm_main"},
                    )
                ],
            ),
            recorded_at_ms=1000,
        )
    )

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert result is not None
    assert len(result.steps) == 1
    assert result.steps[0].kind == "llm"
    assert result.meta["member_count"] == 1


def test_registry_keeps_leader_llm_for_runtime_leader_member_id() -> None:
    registry = InMemoryTrajectoryRegistry()
    member_id = "jiuwen_team_a_team_leader"
    registry.publish_member_trajectory(
        MemberTrajectorySnapshot.make(
            team_id="team-a",
            member_id=member_id,
            member_role="leader",
            trajectory=Trajectory(
                execution_id="exec-leader",
                session_id="session-a",
                source="online",
                steps=[
                    TrajectoryStep(
                        kind="llm",
                        detail=LLMCallDetail(model="mock", messages=[]),
                        meta={"operator_id": f"{member_id}/llm_main"},
                        start_time_ms=1,
                    ),
                    TrajectoryStep(
                        kind="tool",
                        detail=ToolCallDetail(tool_name="view_task"),
                        start_time_ms=2,
                    ),
                ],
                meta={"member_id": member_id},
            ),
            recorded_at_ms=1000,
        )
    )

    result = registry.get_trajectory(team_id="team-a", session_id="session-a")

    assert result is not None
    assert [step.kind for step in result.steps] == ["llm", "tool"]


def test_member_trajectory_snapshot_make_fills_runtime_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.trajectory.registry.now_ms",
        lambda: 12345,
    )
    trajectory = _trajectory("writer", "session-a", "view_task")

    snapshot = MemberTrajectorySnapshot.make(
        team_id="team-a",
        member_id="writer",
        member_role="teammate",
        trajectory=trajectory,
    )

    assert snapshot.team_id == "team-a"
    assert snapshot.session_id == "session-a"
    assert snapshot.member_id == "writer"
    assert snapshot.member_role == "teammate"
    assert snapshot.trajectory is trajectory
    assert snapshot.recorded_at_ms == 12345
