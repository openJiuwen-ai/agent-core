# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal-mode rail for a team member's task tools.

Two things happen around the member's own tool calls:

- claiming a task makes that task the member's goal, in the same turn;
- handing a goal task in is assessed first, and a hand-in the assessment does
  not accept never reaches the task board.

The task board stays the only record of whether a task is done: the goal is
closed after the hand-in succeeded, never before.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.goal import GOAL_TASK_KEY, bind_task_goal, is_active_task_goal
from openjiuwen.agent_teams.i18n import t
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.goal.schema import GoalAssessment, GoalAssessmentStatus
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

_CLAIM_TOOL = "claim_task"
_COMPLETE_TOOL = "member_complete_task"


def _tool_args(ctx: AgentCallbackContext) -> dict[str, Any]:
    """Return the tool call's arguments as a dict (empty when unreadable)."""
    raw = getattr(ctx.inputs, "tool_args", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_succeeded(ctx: AgentCallbackContext) -> bool:
    """Whether the tool call that just finished reported success."""
    result = getattr(ctx.inputs, "tool_result", None)
    if isinstance(result, dict):
        return bool(result.get("success", "error" not in result))
    return bool(getattr(result, "success", False))


class TeamGoalRail(DeepAgentRail):
    """Bind a member's goal to the task it claims and gate its hand-in."""

    priority = 80

    def __init__(self, *, task_manager: "TeamTaskManager") -> None:
        super().__init__()
        self._task_manager = task_manager
        self._harness: Any = None
        # Task ids whose hand-in passed the gate and is now executing.
        self._accepted: set[str] = set()

    def init(self, agent: Any) -> None:
        """Keep the owning harness; tool hooks run on the inner agent."""
        super().init(agent)
        self._harness = agent

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Assess a goal task's hand-in before it reaches the task board."""
        task_id = self._handed_in_task(ctx)
        if task_id is None:
            return
        manager, session = self._goal_handles()
        if manager is None:
            return
        # A task can be started for the member without a claim (scheduler,
        # plan approval) and finished within the very same round, before any
        # round-end check ran. Give it its goal here so no hand-in of an
        # owned in-progress task ever skips the assessment.
        await bind_task_goal(manager, session, self._task_manager, task_id)
        if not is_active_task_goal(manager, session, task_id):
            return
        completion_rail = self._completion_rail()
        if completion_rail is None:
            return
        note = str(_tool_args(ctx).get("note") or "")
        assessment = await completion_rail.assess_completion_claim(
            self._assessment_context(ctx),
            note or f"The member is handing in task {task_id} as complete.",
        )
        # No reliable verdict is not a rejection: a broken assessor must not
        # pin the task in progress forever.
        if assessment is None or assessment.status is GoalAssessmentStatus.COMPLETE:
            self._accepted.add(task_id)
            return
        record = manager.peek()
        await manager.apply_assessment(
            goal_id=record.goal_id,
            revision=record.revision,
            assessment=assessment,
        )
        self._reject(ctx, task_id, assessment)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Open the goal on a successful claim; close it on a successful hand-in."""
        tool_name = getattr(ctx.inputs, "tool_name", "")
        args = _tool_args(ctx)
        task_id = str(args.get("task_id") or "")
        if not task_id:
            return
        manager, session = self._goal_handles()
        if manager is None:
            return
        if tool_name == _CLAIM_TOOL and args.get("status") == "claimed":
            if _tool_succeeded(ctx):
                await bind_task_goal(manager, session, self._task_manager, task_id)
            return
        if task_id not in self._accepted:
            return
        self._accepted.discard(task_id)
        if not _tool_succeeded(ctx) or not is_active_task_goal(manager, session, task_id):
            return
        record = manager.peek()
        await manager.apply_assessment(
            goal_id=record.goal_id,
            revision=record.revision,
            assessment=GoalAssessment(
                status=GoalAssessmentStatus.COMPLETE,
                evidence=f"Task {task_id} was handed in and accepted by the task board.",
            ),
        )
        # The task left the member's hands; if a reviewer sends it back it
        # starts over as a fresh goal.
        session.update_state({GOAL_TASK_KEY: None})

    @staticmethod
    def _handed_in_task(ctx: AgentCallbackContext) -> str | None:
        """Return the task id a completion call hands in, or None for other calls."""
        tool_name = getattr(ctx.inputs, "tool_name", "")
        args = _tool_args(ctx)
        is_hand_in = tool_name == _COMPLETE_TOOL or (
            tool_name == _CLAIM_TOOL and args.get("status") == "completed"
        )
        task_id = str(args.get("task_id") or "").strip()
        return task_id if is_hand_in and task_id else None

    def _goal_handles(self) -> tuple[Any, Any]:
        """Return the harness's goal manager and session, or (None, None)."""
        harness = self._harness
        manager = getattr(harness, "goal_manager", None)
        session = getattr(harness, "session", None)
        if manager is None or session is None:
            return None, None
        return manager, session

    def _completion_rail(self) -> TaskCompletionRail | None:
        """Return the harness's completion rail, which owns the assessor."""
        rail = getattr(self._harness, "_task_completion_rail", None)
        return rail if isinstance(rail, TaskCompletionRail) else None

    def _assessment_context(self, ctx: AgentCallbackContext) -> Any:
        """Present this tool call to the assessor as a turn of the harness.

        Tool hooks fire on the inner agent, while the assessor resolves its
        model and prompt language from the outer harness.
        """
        return SimpleNamespace(
            agent=self._harness,
            context=getattr(ctx, "context", None),
            inputs=ctx.inputs,
            extra=ctx.extra,
            session=getattr(ctx, "session", None),
        )

    @staticmethod
    def _reject(ctx: AgentCallbackContext, task_id: str, assessment: GoalAssessment) -> None:
        """Skip the hand-in and tell the member what is still missing."""
        message = t(
            "goal.submission_rejected",
            task_id=task_id,
            evidence=assessment.evidence or "-",
            remaining=assessment.remaining_work or assessment.next_instruction or "-",
        )
        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = str(getattr(tool_call, "id", "") or "unknown-tool-call")
        # Keyed by call id: parallel tool calls share ``ctx.extra``.
        ctx.extra.setdefault("_skip_tool_calls", {})[tool_call_id] = True
        ctx.inputs.tool_result = {"error": message}
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)
        team_logger.info("[goal] hand-in of task {} rejected by assessment", task_id)


__all__ = ["TeamGoalRail"]
