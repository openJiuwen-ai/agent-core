# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-side wiring of goal mode for one member (leader or teammate).

The harness drives a goal; this object tells it *when*. A teammate's goal is
kept in step with the task board every time one of its rounds settles, so
every way a task can start or leave the member's hands is covered by one
check instead of one hook per event. The leader's goal waits for the team to
go quiet, and holds the team's completion verdict back while it is open.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.goal import (
    bind_request_goal,
    bind_task_goal,
    bound_task_id,
    release_task_goal,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.goal.schema import GoalStatus

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.member_activity import MemberActivityRegistry
    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

# Session-state key remembering the task a member was already told to hand in,
# so the reminder is sent once per task rather than once per settled round.
_SUBMIT_REMINDED_KEY = "team.goal.submit_reminded"


class TeamGoalCoordinator:
    """Decide when a member's goal exists and when its next attempt may run."""

    def __init__(
        self,
        *,
        role: TeamRole,
        member_name: str,
        harness: "TeamHarness",
        task_manager_getter: Callable[[], "TeamTaskManager | None"],
        message_manager_getter: Callable[[], "TeamMessageManager | None"],
        registry_getter: Callable[[], "MemberActivityRegistry | None"],
        leader_name_getter: Callable[[], str | None],
        emit_blocked: Callable[[str], None],
    ) -> None:
        self._role = role
        self._member_name = member_name
        self._harness = harness
        self._task_manager_getter = task_manager_getter
        self._message_manager_getter = message_manager_getter
        self._registry_getter = registry_getter
        self._leader_name_getter = leader_name_getter
        self._emit_blocked = emit_blocked
        self._background: set[asyncio.Task] = set()
        is_leader = role == TeamRole.LEADER
        harness.set_goal_hooks(
            ready=self._leader_ready if is_leader else self._sync_task_goal,
            on_goal=self._on_goal_changed,
        )

    # ------------------------------------------------------------------
    # Leader
    # ------------------------------------------------------------------

    async def open_user_goal(self, request: Any) -> None:
        """Make the user's request the leader's goal (leader only)."""
        manager = self._harness.goal_manager
        if self._role != TeamRole.LEADER or manager is None:
            return
        await bind_request_goal(manager, request)

    def holds_completion(self) -> bool:
        """Whether the leader's open goal forbids declaring the team complete.

        When it does, the leader is asked to work on the goal: the board being
        drained is exactly the moment to check it against the request.
        """
        manager = self._harness.goal_manager
        if self._role != TeamRole.LEADER or manager is None:
            return False
        record = manager.peek()
        if record is None or record.status is not GoalStatus.ACTIVE:
            return False
        self._harness.nudge_goal()
        return True

    def on_team_quiet(self) -> None:
        """The whole team has held still: let the leader's goal take a turn."""
        if self._role == TeamRole.LEADER:
            self._harness.nudge_goal()

    async def _leader_ready(self) -> bool:
        """A leader goal attempt waits until no other member is moving."""
        registry = self._registry_getter()
        if registry is None:
            return True
        return registry.others_idle()

    # ------------------------------------------------------------------
    # Teammate
    # ------------------------------------------------------------------

    async def _sync_task_goal(self) -> bool:
        """Bring the member's goal in step with the task it holds.

        Runs whenever one of the member's rounds settles. The member's
        in-progress task becomes its goal however it got there (claimed,
        started by the scheduler, plan approved, sent back by a reviewer); a
        goal for a task the member no longer holds is dropped.

        Returns:
            Always True: once in step, a goal attempt may start at once.
        """
        manager = self._harness.goal_manager
        session = self._harness.goal_session
        task_manager = self._task_manager_getter()
        if manager is None or session is None or task_manager is None:
            return True
        tasks = await task_manager.get_tasks_by_assignee(
            self._member_name,
            status=TaskStatus.IN_PROGRESS.value,
        )
        current = tasks[0].task_id if tasks else None
        bound = bound_task_id(session)
        if bound is not None and bound != current:
            await release_task_goal(manager, session, bound)
        if current is None:
            return True
        record = manager.peek()
        assessed_done = bound == current and record is not None and record.status is GoalStatus.COMPLETED
        if assessed_done:
            # The goal was assessed as met but the task was never handed in.
            # The board, not the goal, says whether a task is done.
            self._remind_to_submit(session, current)
            return True
        await bind_task_goal(manager, session, task_manager, current)
        return True

    def _remind_to_submit(self, session: Any, task_id: str) -> None:
        """Tell the member, once per task, to hand in work assessed as done."""
        if session.get_state(_SUBMIT_REMINDED_KEY) == task_id:
            return
        session.update_state({_SUBMIT_REMINDED_KEY: task_id})
        # The caller runs inside the harness supervisor, which is also what
        # answers ``send``; awaiting it here would wait on ourselves.
        self._spawn(self._harness.send(t("goal.submit_now", task_id=task_id)))

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    async def _on_goal_changed(self, goal: dict | None) -> None:
        """Escalate a goal that stopped driving itself."""
        if not goal or goal.get("status") != GoalStatus.BLOCKED.value:
            return
        assessment = goal.get("last_assessment") or {}
        reason = str(assessment.get("evidence") or goal.get("last_stop_reason") or "blocked")
        if self._role == TeamRole.LEADER:
            self._emit_blocked(reason)
            return
        session = self._harness.goal_session
        task_id = bound_task_id(session) if session is not None else None
        leader = self._leader_name_getter()
        message_manager = self._message_manager_getter()
        if not task_id or not leader or message_manager is None:
            return
        try:
            await message_manager.send_message(
                t("goal.member_blocked", member=self._member_name, task_id=task_id, reason=reason),
                to_member_name=leader,
            )
        except Exception as exc:  # noqa: BLE001 - escalation is best-effort
            team_logger.error("[{}] failed to report blocked goal: {}", self._member_name, exc)

    def _spawn(self, coro: Any) -> None:
        """Run a coroutine in the background, keeping a reference until done."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


__all__ = ["TeamGoalCoordinator"]
