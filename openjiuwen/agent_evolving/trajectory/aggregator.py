# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team trajectory aggregator.

Reads individual member trajectories from a shared store and
aggregates them into a combined view for team-level analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openjiuwen.agent_evolving.trajectory.store import (
    FileTrajectoryStore,
    TrajectoryStore,
)
from openjiuwen.agent_evolving.trajectory.types import Trajectory, TrajectoryStep


@dataclass
class TeamTrajectory:
    """Aggregated team trajectory for a single session."""

    team_id: str
    session_id: str
    combined: Trajectory
    """All steps merged and sorted by start_time_ms"""

    members: dict[str, Trajectory] = field(default_factory=dict)
    """member_id -> individual Trajectory"""


def aggregate_member_trajectories(
    trajectories: list[Trajectory],
    *,
    team_id: str,
    session_id: str,
    filter_collaborative: bool = True,
) -> Trajectory:
    """Aggregate member trajectories already loaded in memory."""
    return _build_combined_trajectory(
        _member_trajectories_by_id(
            trajectories,
            filter_collaborative=filter_collaborative,
        ),
        team_id=team_id,
        session_id=session_id,
    )


class TeamTrajectoryAggregator:
    """Aggregates member trajectories from a TrajectoryStore.

    Usage (preferred):
        agg = TeamTrajectoryAggregator(
            store=trajectory_store,
            team_id="my-team",
        )
        team_traj = agg.aggregate(session_id="session-123")

    Usage (backward-compatible):
        agg = TeamTrajectoryAggregator(
            trajectories_dir=Path("/tmp/traj"),
            team_id="my-team",
        )
    """

    def __init__(
        self,
        *,
        store: Optional[TrajectoryStore] = None,
        trajectories_dir: Optional[Path] = None,
        team_id: str,
    ) -> None:
        if store is not None:
            self._store: TrajectoryStore = store
        elif trajectories_dir is not None:
            self._store = FileTrajectoryStore(trajectories_dir)
        else:
            raise ValueError("Either 'store' or 'trajectories_dir' must be provided")
        self._team_id = team_id

    def aggregate(
        self,
        session_id: str,
        *,
        filter_collaborative: bool = True,
    ) -> TeamTrajectory:
        """Aggregate all member trajectories for the given session.

        Args:
            session_id: Session to aggregate.
            filter_collaborative: If True, apply filter_member_trajectory to
                each member trajectory before merging.

        Returns TeamTrajectory with:
        - members: dict of member_id -> filtered Trajectory
        - combined: merged view sorted by start_time_ms
        """
        trajectories = self._store.query(session_id=session_id)
        if not trajectories:
            return self._empty_combined(session_id)

        members = _member_trajectories_by_id(
            trajectories,
            filter_collaborative=filter_collaborative,
        )
        if not members:
            return self._empty_combined(session_id)

        combined = _build_combined_trajectory(
            members,
            team_id=self._team_id,
            session_id=session_id,
        )
        return TeamTrajectory(
            team_id=self._team_id,
            session_id=session_id,
            members=members,
            combined=combined,
        )

    def _empty_combined(self, session_id: str) -> TeamTrajectory:
        """Return an empty combined trajectory."""
        combined = Trajectory(
            execution_id=f"team-{self._team_id}",
            session_id=session_id,
            source="online",
            steps=[],
            meta={"member_count": 0},
        )
        return TeamTrajectory(
            team_id=self._team_id,
            session_id=session_id,
            combined=combined,
        )


# Collaborative tool names -- reflect inter-member interaction behavior
# Note: spawn_member is Leader-only, not included here for Teammate context
_COLLABORATIVE_TOOLS: frozenset[str] = frozenset(
    {
        "view_task",
        "claim_task",  # Teammate-only: claim or complete a task
        "send_message",  # Shared: point-to-point or broadcast messaging
        "workspace_meta",  # Shared: workspace lock and version management
    }
)

# Cross-member interaction meta markers
_CROSS_MEMBER_META_KEYS: frozenset[str] = frozenset(
    {
        "invoke_id",
        "parent_invoke_id",
        "child_invokes",
    }
)
_LEADER_ROLE = "leader"
_MEMBER_ROLE_META_KEYS: tuple[str, ...] = ("member_role", "role")


def filter_member_trajectory(trajectory: Trajectory) -> Trajectory:
    """Filter a member's trajectory to keep only collaboration-relevant steps.

    Retains steps that reflect inter-member behavior:
    - Steps with cross-member meta keys (invoke_id, parent_invoke_id, child_invokes)
    - Tool calls using collaborative tool names (view_task, claim_task, etc.)
    - Reads or writes of team skill files
    - Skips pure internal LLM reasoning and non-whitelisted tool calls

    Returns a new Trajectory with filtered steps, preserving all other fields.
    """
    filtered_steps = [step for step in trajectory.steps if _is_collaborative_step(step)]

    return Trajectory(
        execution_id=trajectory.execution_id,
        session_id=trajectory.session_id,
        source=trajectory.source,
        steps=filtered_steps,
        cost=trajectory.cost,
        meta=trajectory.meta,
    )


def _is_leader_trajectory(trajectory: Trajectory, member_id: str) -> bool:
    """Return True when trajectory metadata identifies a leader member."""
    for key in _MEMBER_ROLE_META_KEYS:
        role = trajectory.meta.get(key)
        if role is None:
            continue
        role_value = getattr(role, "value", role)
        return str(role_value).lower() == _LEADER_ROLE
    return member_id == _LEADER_ROLE


def _member_trajectories_by_id(
    trajectories: list[Trajectory],
    *,
    filter_collaborative: bool,
) -> dict[str, Trajectory]:
    members: dict[str, Trajectory] = {}
    for trajectory in trajectories:
        member_id = str(trajectory.meta.get("member_id", trajectory.execution_id[:8]))
        processed = trajectory
        if filter_collaborative and not _is_leader_trajectory(trajectory, member_id):
            processed = filter_member_trajectory(trajectory)
        if processed.steps:
            members[member_id] = _merge_member_trajectory(members.get(member_id), processed)
    return members


def _build_combined_trajectory(
    members: dict[str, Trajectory],
    *,
    team_id: str,
    session_id: str,
) -> Trajectory:
    all_steps: list[TrajectoryStep] = []
    for trajectory in members.values():
        all_steps.extend(trajectory.steps)
    all_steps.sort(key=lambda step: step.start_time_ms or 0)

    total_input = sum(trajectory.cost.get("input_tokens", 0) for trajectory in members.values() if trajectory.cost)
    total_output = sum(trajectory.cost.get("output_tokens", 0) for trajectory in members.values() if trajectory.cost)

    return Trajectory(
        execution_id=f"team-{team_id}",
        session_id=session_id,
        source="online",
        steps=all_steps,
        cost={"input_tokens": total_input, "output_tokens": total_output}
        if total_input > 0 or total_output > 0
        else None,
        meta={"member_count": len(members)},
    )


def _merge_member_trajectory(existing: Optional[Trajectory], new: Trajectory) -> Trajectory:
    """Merge multiple persisted trajectory snapshots for the same member."""
    if existing is None:
        return new

    if len(new.steps) > len(existing.steps) and _steps_are_prefix(existing.steps, new.steps):
        return new
    if len(existing.steps) > len(new.steps) and _steps_are_prefix(new.steps, existing.steps):
        return existing

    return Trajectory(
        execution_id=existing.execution_id,
        session_id=existing.session_id or new.session_id,
        source=existing.source,
        case_id=existing.case_id or new.case_id,
        steps=[*existing.steps, *new.steps],
        cost=_merge_cost(existing.cost, new.cost),
        meta={**existing.meta, **new.meta},
    )


def _steps_are_prefix(prefix: list[TrajectoryStep], steps: list[TrajectoryStep]) -> bool:
    """Return True when ``prefix`` is the leading slice of ``steps``."""
    return steps[: len(prefix)] == prefix


def _merge_cost(first: Optional[dict], second: Optional[dict]) -> Optional[dict]:
    """Merge token cost dictionaries from independent member snapshots."""
    if not first and not second:
        return None
    merged: dict = {}
    for cost in (first, second):
        if not cost:
            continue
        for key, value in cost.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def _is_collaborative_step(step: TrajectoryStep) -> bool:
    """Return True if the step reflects inter-member collaboration."""
    if step.meta and any(key in step.meta for key in CROSS_MEMBER_META_KEYS):
        return True
    if step.kind != "tool" or not step.detail:
        return False

    tool_name = getattr(step.detail, "tool_name", "").lower()
    return tool_name in COLLABORATIVE_TOOLS or _is_team_skill_file_access(step, tool_name)


def _is_team_skill_file_access(step: TrajectoryStep, tool_name: str) -> bool:
    if "read" not in tool_name and "write" not in tool_name:
        return False
    args = str(getattr(step.detail, "call_args", "")).lower()
    return "skill" in args


# Public exports for reuse by other modules
COLLABORATIVE_TOOLS = _COLLABORATIVE_TOOLS
CROSS_MEMBER_META_KEYS = _CROSS_MEMBER_META_KEYS
