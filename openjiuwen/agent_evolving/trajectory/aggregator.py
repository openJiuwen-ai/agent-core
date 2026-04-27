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

        members: dict[str, Trajectory] = {}
        for traj in trajectories:
            mid = traj.meta.get("member_id", traj.execution_id[:8])
            processed = traj
            if filter_collaborative and mid != "leader":
                processed = filter_member_trajectory(traj)
            if processed.steps:
                members[mid] = processed

        if not members:
            return self._empty_combined(session_id)

        combined = self._merge(members, session_id)
        return TeamTrajectory(
            team_id=self._team_id,
            session_id=session_id,
            members=members,
            combined=combined,
        )

    def _merge(self, members: dict[str, Trajectory], session_id: str) -> Trajectory:
        """Merge all member trajectories into a combined view."""
        all_steps: list[TrajectoryStep] = []
        for traj in members.values():
            all_steps.extend(traj.steps)

        # Sort by start_time_ms for temporal ordering
        all_steps.sort(key=lambda s: s.start_time_ms or 0)

        # Aggregate costs
        total_input = sum(t.cost.get("input_tokens", 0) for t in members.values() if t.cost)
        total_output = sum(t.cost.get("output_tokens", 0) for t in members.values() if t.cost)

        return Trajectory(
            execution_id=f"team-{self._team_id}",
            session_id=session_id,
            source="online",
            steps=all_steps,
            cost={
                "input_tokens": total_input,
                "output_tokens": total_output,
            }
            if total_input > 0 or total_output > 0
            else None,
            meta={"member_count": len(members)},
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
_COLLABORATIVE_TOOLS: frozenset[str] = frozenset({
    "view_task",
    "claim_task",     # Teammate-only: claim or complete a task
    "send_message",   # Shared: point-to-point or broadcast messaging
    "workspace_meta", # Shared: workspace lock and version management
    "read_file",
    "write_file",     # when writing shared resources like SKILL.md
})

# Pure internal tools -- member's own work, does not reflect team skill
_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "bash",
    "python",
    "node",
    "edit",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
})

# Cross-member interaction meta markers
_CROSS_MEMBER_META_KEYS: frozenset[str] = frozenset({
    "invoke_id",
    "parent_invoke_id",
    "child_invokes",
})


def filter_member_trajectory(trajectory: Trajectory) -> Trajectory:
    """Filter a member's trajectory to keep only collaboration-relevant steps.

    Retains steps that reflect inter-member behavior:
    - Steps with cross-member meta keys (invoke_id, parent_invoke_id, child_invokes)
    - Tool calls using collaborative tool names (spawn_member, view_task, etc.)
    - Skips pure internal LLM reasoning and internal tool calls

    Returns a new Trajectory with filtered steps, preserving all other fields.
    """
    filtered_steps = [
        step for step in trajectory.steps if _is_collaborative_step(step)
    ]

    return Trajectory(
        execution_id=trajectory.execution_id,
        session_id=trajectory.session_id,
        source=trajectory.source,
        steps=filtered_steps,
        cost=trajectory.cost,
        meta=trajectory.meta,
    )


def _is_collaborative_step(step: TrajectoryStep) -> bool:
    """Return True if the step reflects inter-member collaboration."""
    # 1. Cross-member invoke markers
    if step.meta:
        if any(key in step.meta for key in CROSS_MEMBER_META_KEYS):
            return True

    # 2. Tool steps: check tool name
    if step.kind == "tool" and step.detail:
        tool_name = getattr(step.detail, "tool_name", "").lower()
        if tool_name in COLLABORATIVE_TOOLS:
            return True
        # Also keep any tool whose name suggests reading team skill files
        if "read" in tool_name:
            args_str = str(getattr(step.detail, "call_args", ""))
            if "skill" in args_str.lower():
                return True
        # Internal tools: explicitly filter out
        if tool_name in _INTERNAL_TOOLS:
            return False
        # Unknown tools: keep them (conservative)
        return True

    # 3. LLM steps without cross-member markers: filter out
    return False


# Public exports for reuse by other modules
COLLABORATIVE_TOOLS = _COLLABORATIVE_TOOLS
CROSS_MEMBER_META_KEYS = _CROSS_MEMBER_META_KEYS
