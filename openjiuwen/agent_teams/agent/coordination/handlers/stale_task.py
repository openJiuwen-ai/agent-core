# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Stale-task sweep on POLL_TASK ticks.

Periodic check of tasks stuck in CLAIMED past the stale threshold
(every member sweeps tasks assigned to itself; leader additionally
sweeps all members') and PENDING past the stale threshold (leader
only). Per-task throttle prevents re-nudging within one stale window
across both the poll path and the member-status-change path
(``MemberHandler._nudge_idle_member_with_stale_claims``) — they
share the same throttle dict by reference.
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
        stale_claim_throttle: dict[str, float],
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        # task_id -> wall-clock seconds when we last fired a stale-claim
        # nudge. Used only to throttle follow-up nudges; the "is this
        # task stale?" decision itself reads ``task.updated_at`` from
        # the database so we never lose state across process restarts.
        # Shared by reference with MemberHandler so a poll tick and a
        # status flip within the same window cannot double-nudge.
        self._last_stale_nudge = stale_claim_throttle
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
        """Find claimed tasks that have been running past the stale threshold.

        Measures how long a task has been in CLAIMED state by reading
        the database ``updated_at`` column (bumped on every status
        transition, so for a claimed task it is the claim timestamp).
        When the elapsed time exceeds ``_STALE_CLAIM_SECONDS`` the
        assignee is nudged via the local agent loop (self) or a direct
        message (leader → other member). A per-task throttle prevents
        follow-up polls from re-nudging inside the same stale window.
        """
        task_manager = self._infra.task_manager
        if task_manager is None:
            return

        claimed = await task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        own_name = self._blueprint.member_name
        is_leader = self._blueprint.role == TeamRole.LEADER
        relevant = [tk for tk in claimed if tk.assignee and (tk.assignee == own_name or is_leader)]

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
            await self._nudge_stale_claim(task, now_ms)

    async def _nudge_stale_claim(self, task: Any, now_ms: int) -> None:
        """Dispatch a stale-claim nudge to self or to the assigned member."""
        assignee = task.assignee
        if assignee and assignee == self._blueprint.member_name:
            await self._self_nudge_stale_claim(task, now_ms)
        elif self._blueprint.role == TeamRole.LEADER and assignee:
            await self._leader_nudge_stale_claim(task, now_ms)

    @staticmethod
    def _format_stale_claim_nudge(task: Any, now_ms: int) -> str:
        return t(
            "dispatcher.stale_claim_self",
            task_id=task.task_id,
            title=task.title,
            content=task.content,
            time_info=format_time_context(task.updated_at, now_ms),
        )

    async def _self_nudge_stale_claim(self, task: Any, now_ms: int) -> None:
        """Feed a nudge input into the local agent loop."""
        content = self._format_stale_claim_nudge(task, now_ms)
        await self._round.deliver_input(content)
        team_logger.info(
            "[{}] self-nudged stale claimed task {}",
            self._blueprint.member_name,
            task.task_id,
        )

    async def _leader_nudge_stale_claim(self, task: Any, now_ms: int) -> None:
        """Send a direct reminder to the member holding a stale claim."""
        if self._infra.message_manager is None:
            return
        content = self._format_stale_claim_nudge(task, now_ms)
        await self._infra.message_manager.send_message(content, task.assignee)
        team_logger.info(
            "[leader] nudged {} about stale claimed task {}",
            task.assignee,
            task.task_id,
        )

    async def _check_stale_pending_tasks(self) -> None:
        """Leader-only: self-prompt about pending tasks that nobody claimed.

        Scans pending tasks via ``task.updated_at`` (bumped on every
        status transition). When a task has been pending past
        ``_STALE_PENDING_SECONDS``, the leader feeds itself an input
        listing those tasks plus a hint to pick the right teammate and
        ping them via ``send_message``. The model decides who to
        notify based on each task's content and the team roster — the
        dispatcher does not try to do the matching itself. A per-task
        throttle prevents follow-up polls from re-prompting inside the
        same stale window.
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
            lines.append(f"- [{task.task_id}] {task.title}: {task.content} ({time_info})")
        content = "\n".join(lines)

        await self._round.deliver_input(content)
        team_logger.info(
            "[leader] self-prompted about {} stale pending task(s)",
            len(fresh),
        )
