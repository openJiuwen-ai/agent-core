# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Stale-task sweep on POLL_TASK ticks.

Periodic check of tasks stuck in CLAIMED past the stale threshold and
PENDING past the stale threshold (leader only). Stale-claim nudging is
**self-only**: every member sweeps the tasks assigned to *itself* and
feeds its own agent loop — the leader does not reach across processes
to nudge another member about that member's stale claims. A per-task
throttle prevents re-nudging within one stale window.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import DispatcherHost, PollController


class StaleTaskHandler(BaseCoordinationHandler):
    """Periodic stale-task sweep on POLL_TASK ticks."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        InnerEventType.POLL_TASK.value: "on_poll_task",
    }

    _STALE_CLAIM_SECONDS: ClassVar[float] = 10 * 60.0
    _STALE_PENDING_SECONDS: ClassVar[float] = 10 * 60.0

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        # task_id -> wall-clock seconds when we last fired a stale-claim
        # nudge. Used only to throttle follow-up nudges; the "is this
        # task stale?" decision itself reads ``task.updated_at`` from
        # the database so we never lose state across process restarts.
        self._last_stale_nudge: dict[str, float] = {}
        # Same idea, but for stale PENDING tasks the leader observes —
        # throttles the leader's self-prompt about long-unclaimed work.
        self._last_pending_nudge: dict[str, float] = {}

    async def on_poll_task(self, event: InnerEventMessage) -> None:
        """Periodic task-board sweep: flag stale CLAIMED + leader stale PENDING."""
        member_name = self._blueprint.member_name
        team_logger.debug("poll task: member_name={}, agent_running={}", member_name, self._round.is_agent_running())
        if member_name and self._infra.task_manager:
            await self._check_stale_claimed_tasks()
            await self._check_stale_pending_tasks()
            # if not host.is_agent_running():
            #     await self._nudge_idle_agent(member_name, from_poll=True)

    async def _check_stale_claimed_tasks(self) -> None:
        """Find own active tasks that have been running past the stale threshold.

        "Active" spans the three owned non-terminal conditions — PLANNING,
        IN_PROGRESS, and IN_REVIEW. Measures how long a task
        has been active by reading the database ``updated_at`` column
        (bumped on every status transition). When the elapsed time
        exceeds ``_STALE_CLAIM_SECONDS`` the member feeds a nudge into
        its own agent loop. Only tasks assigned to *this* member are
        swept — the leader does not nudge another member about that
        member's stale work. A per-task throttle prevents follow-up
        polls from re-nudging inside the same stale window.
        """
        task_manager = self._infra.task_manager
        if task_manager is None:
            return

        own_name = self._blueprint.member_name
        active: list[Any] = []
        for status in (
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.IN_REVIEW.value,
        ):
            active.extend(await task_manager.list_tasks(status=status))
        relevant = [tk for tk in active if tk.assignee and tk.assignee == own_name]

        current_ids = {tk.task_id for tk in relevant}
        for tid in [k for k in self._last_stale_nudge if k not in current_ids]:
            self._last_stale_nudge.pop(tid, None)

        if not relevant:
            return

        # Throttle stays in seconds (time.time); the millisecond now is only
        # for rendering the relative-time string and is kept separate so the
        # two units never get mixed.
        now = time.time()
        now_ms = get_current_time()
        threshold_ms = self._STALE_CLAIM_SECONDS * 1000
        for task in relevant:
            if task.updated_at is None:
                continue
            elapsed_ms = now * 1000 - task.updated_at
            if elapsed_ms < threshold_ms:
                continue
            last_nudge = self._last_stale_nudge.get(task.task_id, 0.0)
            if now - last_nudge < self._STALE_CLAIM_SECONDS:
                continue
            self._last_stale_nudge[task.task_id] = now
            await self._self_nudge_stale_claim(task, now_ms)

    async def _self_nudge_stale_claim(self, task: Any, now_ms: int) -> None:
        """Feed a stale-claim nudge into this member's own agent loop.

        Rendered as a ``<team-event kind="stale-claim">`` and appended
        (``use_steer=False``) rather than steered: the nudge only tells
        the member to keep pushing a task it already owns, so it must not
        interrupt the very round doing that work. The body carries just
        the task id + title; the member reads full details via
        ``view_task`` if needed.
        """
        content = t(
            "dispatcher.stale_claim_self",
            task_id=task.task_id,
            title=task.title,
            time_info=format_time_context(task.updated_at, now_ms),
        )
        await self._round.deliver_input(
            render_event(kind="stale-claim", body=content, task_id=task.task_id),
            use_steer=False,
        )
        team_logger.info(
            "[{}] self-nudged stale claimed task {}",
            self._blueprint.member_name,
            task.task_id,
        )

    async def _check_stale_pending_tasks(self) -> None:
        """Leader-only: self-prompt about pending tasks that nobody claimed.

        Scans pending tasks via ``task.updated_at`` (bumped on every
        status transition). When a task has been pending past
        ``_STALE_PENDING_SECONDS``, the leader appends
        (``use_steer=False``) a self-prompt listing those tasks by id +
        title plus a hint to pick the right teammate and ping them via
        ``send_message``. The list stays minimal; the model reads each
        task's details via ``view_task`` and decides who to notify based
        on the team roster — the dispatcher does not do the matching
        itself. A per-task throttle prevents follow-up polls from
        re-prompting inside the same stale window.
        """
        if self._blueprint.role != TeamRole.LEADER:
            return
        task_manager = self._infra.task_manager
        if task_manager is None:
            return

        pending = await task_manager.list_tasks(status=TaskStatus.PENDING.value)
        now = time.time()
        threshold_ms = self._STALE_PENDING_SECONDS * 1000
        stale_ids = {
            tk.task_id for tk in pending if tk.updated_at is not None and (now * 1000 - tk.updated_at) >= threshold_ms
        }

        # GC throttle entries for tasks no longer pending/stale.
        for tid in [k for k in self._last_pending_nudge if k not in stale_ids]:
            self._last_pending_nudge.pop(tid, None)

        fresh: list = []
        for task in pending:
            if task.task_id not in stale_ids:
                continue
            last = self._last_pending_nudge.get(task.task_id, 0.0)
            if now - last < self._STALE_PENDING_SECONDS:
                continue
            fresh.append(task)

        if not fresh:
            return

        for task in fresh:
            self._last_pending_nudge[task.task_id] = now

        now_ms = get_current_time()
        lines = [t("dispatcher.stale_pending_header")]
        for task in fresh:
            time_info = format_time_context(task.updated_at, now_ms)
            lines.append(f"- [{task.task_id}] {task.title} ({time_info})")
        content = "\n".join(lines)

        await self._round.deliver_input(render_event(kind="stale-pending", body=content), use_steer=False)
        team_logger.info(
            "[leader] self-prompted about {} stale pending task(s)",
            len(fresh),
        )


class ScheduledStaleTaskHandler(StaleTaskHandler):
    """Stale-task sweep of a scheduled-dispatch team instance (F_62).

    The poll-tick composition is what differs by mode: the self-owned
    stale-claim sweep stays (it is the delivery safety net for a member that
    missed a scheduler handoff), but the leader's stale-PENDING self-prompt
    belongs to the autonomous claim pool only — under scheduled dispatch a
    long-PENDING task is normal (pre-assigned, queued behind its owner's
    one-active limit) and the scheduler starts it the moment the owner frees
    up. Selection happens in ``EventDispatcher``, never inside a handler.
    """

    async def on_poll_task(self, event: InnerEventMessage) -> None:
        """Periodic sweep: flag own stale active tasks only."""
        member_name = self._blueprint.member_name
        team_logger.debug(
            "poll task (scheduled): member_name={}, agent_running={}",
            member_name,
            self._round.is_agent_running(),
        )
        if member_name and self._infra.task_manager:
            await self._check_stale_claimed_tasks()
