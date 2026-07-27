# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team trajectory aggregator.

Reads individual member trajectories from a shared store and
aggregates them into a combined view for team-level analysis.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from openjiuwen.agent_evolving.trajectory.semconv import (
    CASE_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_NAME,
    OJ_AGENT_INVOKE_TYPE,
    OJ_SESSION_ID,
    OJ_TEAM_ID,
    OJ_WORKFLOW_COMPONENT_TYPE,
    TRAJECTORY_ID,
    TRAJECTORY_INVOKE_TYPE,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SCOPE_NAME,
    TRAJECTORY_SOURCE,
    TRAJECTORY_STEP_KIND,
)
from openjiuwen.agent_evolving.trajectory.span_codec import (
    attributes_to_otlp,
    otlp_value_to_python,
)
from openjiuwen.agent_evolving.trajectory.store import (
    FileTrajectoryStore,
    TrajectoryStore,
)
from openjiuwen.agent_evolving.trajectory.types import (
    Trajectory,
    TrajectoryStep,
    trajectory_case_id,
    trajectory_execution_id,
    trajectory_meta,
    trajectory_resource_attributes,
    trajectory_session_id,
    trajectory_source,
    trajectory_steps,
)

TrajectoryRecord = Trajectory


@dataclass
class TeamTrajectory:
    """Aggregated team trajectory for a single session."""

    team_id: str
    session_id: str
    combined: Trajectory
    """All steps merged and sorted by start_time_ms"""

    members: dict[str, Trajectory] = field(default_factory=dict)
    """member_id -> individual trajectory"""


def aggregate_member_trajectories(
    trajectories: list[TrajectoryRecord],
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
        combined = _build_trajectory_from_spans(
            [],
            _team_resource_attributes(
                team_id=self._team_id,
                session_id=session_id,
                member_count=0,
            ),
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


def filter_member_trajectory(trajectory: TrajectoryRecord) -> Trajectory:
    """Filter a member's trajectory to keep only collaboration-relevant steps.

    Retains steps that reflect inter-member behavior:
    - Steps with cross-member meta keys (invoke_id, parent_invoke_id, child_invokes)
    - Tool calls using collaborative tool names (view_task, claim_task, etc.)
    - Reads or writes of team skill files
    - Skips pure internal LLM reasoning and non-whitelisted tool calls

    Returns a new Trajectory with filtered spans, preserving resource attributes.
    """
    return _filter_trajectory_step_spans(trajectory, _is_collaborative_step)


def _is_leader_trajectory(trajectory: Trajectory, member_id: str) -> bool:
    """Return True when trajectory metadata identifies a leader member."""
    meta = trajectory_meta(trajectory)
    for key in _MEMBER_ROLE_META_KEYS:
        role = meta.get(key)
        if role is None:
            continue
        role_value = getattr(role, "value", role)
        return str(role_value).lower() == _LEADER_ROLE
    return member_id == _LEADER_ROLE


def _member_trajectories_by_id(
    trajectories: list[TrajectoryRecord],
    *,
    filter_collaborative: bool,
) -> dict[str, Trajectory]:
    members: dict[str, Trajectory] = {}
    for trajectory in trajectories:
        meta = trajectory_meta(trajectory)
        execution_id = trajectory_execution_id(trajectory)
        member_id = str(meta.get("member_id", execution_id[:8]))
        processed = trajectory
        if filter_collaborative and not _is_leader_trajectory(processed, member_id):
            processed = filter_member_trajectory(trajectory)
        if trajectory_steps(processed):
            members[member_id] = _merge_member_trajectory(members.get(member_id), processed)
    return members


def _build_combined_trajectory(
    members: dict[str, Trajectory],
    *,
    team_id: str,
    session_id: str,
) -> Trajectory:
    all_spans: list[dict[str, Any]] = []
    for trajectory in members.values():
        all_spans.extend(_trajectory_spans(trajectory))
    all_spans.sort(key=_span_start_time)
    return _build_trajectory_from_spans(
        all_spans,
        _team_resource_attributes(
            team_id=team_id,
            session_id=session_id,
            member_count=len(members),
        ),
    )


def _merge_member_trajectory(
    existing: Optional[Trajectory],
    new: Trajectory,
) -> Trajectory:
    """Merge multiple persisted trajectory snapshots for the same member."""
    if existing is None:
        return new

    existing_steps = trajectory_steps(existing)
    new_steps = trajectory_steps(new)
    if len(new_steps) > len(existing_steps) and _steps_are_prefix(existing_steps, new_steps):
        return new
    if len(existing_steps) > len(new_steps) and _steps_are_prefix(new_steps, existing_steps):
        return existing

    attrs = {
        **trajectory_resource_attributes(existing),
        **trajectory_resource_attributes(new),
        TRAJECTORY_ID: trajectory_execution_id(existing) or trajectory_execution_id(new),
        OJ_SESSION_ID: trajectory_session_id(existing) or trajectory_session_id(new),
        CASE_ID: trajectory_case_id(existing) or trajectory_case_id(new),
        TRAJECTORY_SOURCE: trajectory_source(existing) or trajectory_source(new),
    }
    merged_spans = [*_trajectory_spans(existing), *_trajectory_spans(new)]
    return _build_trajectory_from_spans(merged_spans, attrs)


def _steps_are_prefix(prefix: list[TrajectoryStep], steps: list[TrajectoryStep]) -> bool:
    """Return True when ``prefix`` is the leading slice of ``steps``."""
    return steps[: len(prefix)] == prefix


def _is_collaborative_step(step: TrajectoryStep) -> bool:
    """Return True if the step reflects inter-member collaboration."""
    if step.meta and any(step.meta.get(key) for key in CROSS_MEMBER_META_KEYS):
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


def _team_resource_attributes(
    *,
    team_id: str,
    session_id: str,
    member_count: int,
) -> dict[str, Any]:
    return {
        TRAJECTORY_ID: f"team-{team_id}",
        TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
        OJ_SESSION_ID: session_id,
        OJ_TEAM_ID: team_id,
        TRAJECTORY_SOURCE: "online",
        "member_count": member_count,
    }


def _build_trajectory_from_spans(
    spans: list[dict[str, Any]],
    resource_attributes: dict[str, Any],
) -> Trajectory:
    attrs = dict(resource_attributes)
    attrs.setdefault(TRAJECTORY_SCHEMA_VERSION_ATTR, TRAJECTORY_SCHEMA_VERSION)
    return Trajectory(
        otlp_trace={
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_to_otlp(attrs)},
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": TRAJECTORY_SCOPE_NAME,
                                "version": TRAJECTORY_SCHEMA_VERSION,
                            },
                            "spans": deepcopy(spans),
                        }
                    ],
                }
            ]
        },
    )


def _filter_trajectory_step_spans(
    trajectory: Trajectory,
    keep_step: Callable[[TrajectoryStep], bool],
) -> Trajectory:
    trace = deepcopy(trajectory.otlp_trace or {})
    steps = iter(trajectory_steps(trajectory))
    for resource_span in trace.get("resourceSpans") or []:
        for scope_span in resource_span.get("scopeSpans") or []:
            kept_spans = []
            for span in scope_span.get("spans") or []:
                if not _span_has_step_projection(span):
                    continue
                step = next(steps, None)
                if step is not None and keep_step(step):
                    kept_spans.append(span)
            scope_span["spans"] = kept_spans
    return Trajectory(otlp_trace=trace)


def _trajectory_spans(trajectory: Trajectory) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    trace = trajectory.otlp_trace if isinstance(trajectory.otlp_trace, dict) else {}
    for resource_span in trace.get("resourceSpans") or []:
        for scope_span in resource_span.get("scopeSpans") or []:
            spans.extend(deepcopy(scope_span.get("spans") or []))
    return spans


def _span_start_time(span: dict[str, Any]) -> int:
    value = span.get("startTimeUnixNano")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _span_attribute_map(span: dict[str, Any]) -> dict[str, Any]:
    return {
        item.get("key"): otlp_value_to_python(item.get("value") or {})
        for item in span.get("attributes") or []
        if item.get("key")
    }


def _span_has_step_projection(span: dict[str, Any]) -> bool:
    attrs = _span_attribute_map(span)
    operation_name = str(attrs.get(GEN_AI_OPERATION_NAME) or "").lower()
    if operation_name in ("chat", "text_completion", "generate_content", "execute_tool"):
        return True
    if attrs.get(GEN_AI_INPUT_MESSAGES) is not None or attrs.get(GEN_AI_OUTPUT_MESSAGES) is not None:
        return True
    if (
        attrs.get(GEN_AI_TOOL_NAME) is not None
        or attrs.get(GEN_AI_TOOL_CALL_ARGUMENTS) is not None
        or attrs.get(GEN_AI_TOOL_CALL_RESULT) is not None
    ):
        return True
    invoke_type = str(
        attrs.get(TRAJECTORY_INVOKE_TYPE)
        or attrs.get(OJ_AGENT_INVOKE_TYPE)
        or ""
    ).lower()
    component_type = str(attrs.get(OJ_WORKFLOW_COMPONENT_TYPE) or "").lower()
    if invoke_type in ("llm", "plugin", "tool") or component_type in ("llm", "tool", "plugin"):
        return True
    if attrs.get(TRAJECTORY_STEP_KIND) in ("llm", "tool"):
        return True
    span_name = str(span.get("name") or "").lower()
    return (
        span_name.startswith("llm.")
        or span_name == "llm.call"
        or span_name.startswith("tool.")
        or span_name.startswith("execute_tool ")
    )


# Public exports for reuse by other modules
COLLABORATIVE_TOOLS = _COLLABORATIVE_TOOLS
CROSS_MEMBER_META_KEYS = _CROSS_MEMBER_META_KEYS
