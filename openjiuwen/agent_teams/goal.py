# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal mode for team members and the leader.

A goal follows a piece of work. For a member that work is the task it has
started; for the leader it is the user's request. This module decides when a
goal exists and what it says. Driving the goal — continuation rounds,
assessment, limits — belongs to the harness goal manager, which stays the only
writer of the goal record.

The task board remains the single source of truth for whether a task is done.
A goal is only what keeps the member working on it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.team import TeamGoalSpec, TeamRole
from openjiuwen.harness.goal.schema import GoalStatus, GoalStopConfig, GoalStopStrategy

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
    from openjiuwen.core.session.agent import Session
    from openjiuwen.harness.goal.manager import GoalManager

# Session-state key holding the id of the task the member's goal stands for.
# It lives beside the goal record so both recover together.
GOAL_TASK_KEY = "team.goal.task_id"


def resolve_goal_spec(spec: "TeamAgentSpec", ctx: "TeamRuntimeContext") -> TeamGoalSpec | None:
    """Return the goal settings that apply to one member, or None when off.

    A leader takes ``leader.goal``. A locally run teammate takes the team's
    ``member_goal`` unless it opted out. Every other role — and any member on a
    third-party harness — has no goal mode.

    Args:
        spec: The team's assembly spec.
        ctx: Runtime context of the member being assembled.
    """
    if ctx.cli_agent:
        return None
    if ctx.role == TeamRole.LEADER:
        return spec.leader.goal
    if ctx.role != TeamRole.TEAMMATE:
        return None
    if ctx.goal_enabled is False:
        return None
    return spec.member_goal


def to_stop_config(goal: TeamGoalSpec) -> GoalStopConfig:
    """Translate team goal settings into the harness stop policy."""
    return GoalStopConfig(
        strategy=GoalStopStrategy(goal.strategy),
        max_attempts=goal.max_attempts,
        token_budget=goal.token_budget,
    )


def bound_task_id(session: "Session") -> str | None:
    """Return the id of the task the member's goal stands for, if any."""
    value = session.get_state(GOAL_TASK_KEY)
    return value if isinstance(value, str) and value else None


def is_active_task_goal(manager: "GoalManager", session: "Session", task_id: str) -> bool:
    """Whether the member's goal is active and stands for ``task_id``."""
    if bound_task_id(session) != task_id:
        return False
    record = manager.peek()
    return record is not None and record.status is GoalStatus.ACTIVE


async def bind_task_goal(
    manager: "GoalManager",
    session: "Session",
    task_manager: "TeamTaskManager",
    task_id: str,
    *,
    feedback: str = "",
) -> bool:
    """Make the task this member has started its goal.

    Does nothing unless the task is in progress and owned by this member, so a
    late or duplicate signal can never plant a goal for work the member does
    not hold. A task that already has a goal keeps it: re-opening would wipe
    its attempt count.

    Args:
        manager: The member's goal manager.
        session: The member's own session.
        task_manager: The member's task manager (carries its member name).
        task_id: Task that entered execution.
        feedback: Reviewer feedback when the task came back for rework.

    Returns:
        True when the task is this member's goal on return.
    """
    task = await task_manager.get(task_id)
    if task is None:
        return False
    if task.status != TaskStatus.IN_PROGRESS.value or task.assignee != task_manager.member_name:
        return False
    # Still bound to this task means its goal is open, or was assessed as met
    # and only awaits the hand-in; either way there is nothing to re-open.
    # A hand-in releases the binding, so a task sent back for rework starts over.
    if manager.peek() is not None and bound_task_id(session) == task_id:
        return True
    objective = t("goal.task_objective", task_id=task_id, title=task.title, content=task.content)
    if feedback:
        objective += t("goal.task_revision", feedback=feedback)
    await manager.set(objective, overwrite_confirmed=True)
    session.update_state({GOAL_TASK_KEY: task_id})
    return True


async def release_task_goal(manager: "GoalManager", session: "Session", task_id: str) -> bool:
    """Drop the member's goal if it stands for ``task_id``.

    Used when the task leaves the member's hands without being completed by it
    (reassigned or cancelled).

    Returns:
        True when a goal was dropped.
    """
    if bound_task_id(session) != task_id:
        return False
    await manager.clear()
    session.update_state({GOAL_TASK_KEY: None})
    return True


async def bind_request_goal(manager: "GoalManager", request: Any) -> bool:
    """Make the user's request the leader's goal, unless one is still open.

    A request arriving while a goal is active or blocked is input to that goal,
    not a replacement for it; a blocked goal is reopened so the new input is
    followed through.

    Args:
        manager: The leader's goal manager.
        request: The user's input; only non-empty text opens a goal.

    Returns:
        True when the leader has an active goal on return.
    """
    record = manager.peek()
    if record is not None and record.status is GoalStatus.ACTIVE:
        return True
    if record is not None and record.status is GoalStatus.BLOCKED:
        await manager.resume()
        return True
    if not isinstance(request, str) or not request.strip():
        return False
    await manager.set(t("goal.leader_objective", request=request.strip()), overwrite_confirmed=True)
    return True


__all__ = [
    "GOAL_TASK_KEY",
    "bind_request_goal",
    "bind_task_goal",
    "bound_task_id",
    "is_active_task_goal",
    "release_task_goal",
    "resolve_goal_spec",
    "to_stop_config",
]
