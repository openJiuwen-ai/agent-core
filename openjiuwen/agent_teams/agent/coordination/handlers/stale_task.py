# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Stale-task sweep on POLL_TASK ticks.

Autonomous dispatch measures a stall from the member's **process-local
idle clock** (``TeamAgentState.idle_since``, read through
``AgentRoundController.idle_seconds()``), never from the database
``task.updated_at``: pausing a team freezes ``updated_at`` while the wall
clock keeps running, so an ``updated_at``-based measure reports a huge
fabricated stall right after a long pause -> resume. The idle clock also
makes "busy" unrepresentable as a stall — ``idle_seconds()`` is ``None``
mid-round, so a member actively working its task is never nudged. See F_65.

Two sweeps, both throttled per task:

- **stale claim (self-only)**: a member idle past the threshold while it
  still owns a PLANNING / IN_PROGRESS task feeds a nudge into its own agent
  loop to keep pushing. Consecutive fruitless windows escalate the stall to
  the leader, who can check in, reassign, or replace the assignee. The
  leader never reaches across processes to nudge a member directly (F_53) —
  the stalled member reports its own stall instead.
- **stale pending (leader-only)**: a leader idle past the threshold while
  unassigned PENDING tasks exist *and* at least one member is free
  self-prompts to assign them. The free-member precondition keeps the
  prompt away from a team whose members are simply all busy.

Scheduled dispatch keeps the legacy ``updated_at`` claim sweep — see
``ScheduledStaleTaskHandler``.
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
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
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
    # Consecutive fruitless stale-claim windows before the stalled member
    # reports the stall to the leader. Self-nudging first keeps the common
    # case in-process (only the assignee can push its own task); escalating
    # after repeated windows surfaces the ones self-nudging cannot fix.
    _STALE_CLAIM_ESCALATE_STREAK: ClassVar[int] = 3

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        spec = blueprint.spec
        self._idle_claim_seconds = float(spec.stale_claim_idle_timeout)
        self._idle_pending_seconds = float(spec.stale_pending_idle_timeout)
        # task_id -> wall-clock seconds when we last fired a stale-claim
        # nudge. Throttles follow-up nudges only; whether a task *is* stale
        # is decided by the member's idle clock (``idle_seconds()``), which
        # is process-local by design — see the module docstring.
        self._last_stale_nudge: dict[str, float] = {}
        # Same idea, but for stale PENDING tasks the leader observes —
        # throttles the leader's self-prompt about long-unclaimed work.
        self._last_pending_nudge: dict[str, float] = {}
        # task_id -> how many consecutive stale windows nudged this task
        # without it leaving the owned-active set, and the ids already
        # escalated so the leader hears about each stall once. Both are GC'd
        # when the task leaves the set.
        self._stale_claim_streak: dict[str, int] = {}
        self._escalated_claims: set[str] = set()

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
        """Nudge self when idle too long while still owning active work.

        Sweeps the tasks assigned to *this* member in the two conditions the
        member itself is expected to push — PLANNING and IN_PROGRESS.
        IN_REVIEW is deliberately excluded: an author sitting idle while its
        reviewers decide is waiting by design, not stalling.

        The stall is measured by the member's own idle clock rather than the
        task's ``updated_at``, which buys two things: a paused team cannot
        manufacture a stall (the clock is re-based on resume), and a member
        busy pushing the task is never nudged — ``idle_seconds()`` is
        ``None`` mid-round, so "busy" is simply unrepresentable as a stall.
        Only this member's own tasks are swept; the leader does not reach
        across processes to nudge a member (F_53). After
        ``_STALE_CLAIM_ESCALATE_STREAK`` fruitless windows the member reports
        the stall to the leader itself. A per-task throttle prevents
        re-nudging inside one stale window.
        """
        task_manager = self._infra.task_manager
        if task_manager is None:
            return

        own_name = self._blueprint.member_name
        active: list[Any] = []
        for status in (
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
        ):
            active.extend(await task_manager.list_tasks(status=status))
        relevant = [tk for tk in active if tk.assignee and tk.assignee == own_name]

        # GC bookkeeping for tasks that left the owned-active set — completed,
        # reassigned, sent to review or cancelled. A task that comes back
        # starts its streak over, which is what "consecutive" should mean.
        current_ids = {tk.task_id for tk in relevant}
        for tid in [k for k in self._last_stale_nudge if k not in current_ids]:
            self._last_stale_nudge.pop(tid, None)
        for tid in [k for k in self._stale_claim_streak if k not in current_ids]:
            self._stale_claim_streak.pop(tid, None)
        self._escalated_claims.intersection_update(current_ids)

        if not relevant:
            return

        idle = self._round.idle_seconds()
        if idle is None or idle < self._idle_claim_seconds:
            return

        now = time.time()
        for task in relevant:
            last_nudge = self._last_stale_nudge.get(task.task_id, 0.0)
            if now - last_nudge < self._idle_claim_seconds:
                continue
            self._last_stale_nudge[task.task_id] = now
            streak = self._stale_claim_streak.get(task.task_id, 0) + 1
            self._stale_claim_streak[task.task_id] = streak
            await self._self_nudge_idle_claim(task, idle)
            if streak >= self._STALE_CLAIM_ESCALATE_STREAK and task.task_id not in self._escalated_claims:
                self._escalated_claims.add(task.task_id)
                await self._escalate_stale_claim(task, idle)

    async def _self_nudge_idle_claim(self, task: Any, idle: float) -> None:
        """Feed an idle-stall nudge into this member's own agent loop.

        Rendered as a ``<team-event kind="stale-claim">`` and appended
        (``use_steer=False``) rather than steered: the nudge only tells the
        member to keep pushing a task it already owns, so it must not
        interrupt a round. The body carries just the task id + title + how
        long the member has been idle; full details come from ``view_task``.
        """
        content = t(
            "dispatcher.stale_idle_claim_self",
            task_id=task.task_id,
            title=task.title,
            minutes=int(idle // 60),
        )
        await self._round.deliver_input(
            render_event(kind="stale-claim", body=content, task_id=task.task_id),
            use_steer=False,
        )
        team_logger.info(
            "[{}] self-nudged idle-stalled task {} (idle {}s)",
            self._blueprint.member_name,
            task.task_id,
            int(idle),
        )

    async def _escalate_stale_claim(self, task: Any, idle: float) -> None:
        """Report a self-nudge-proof stall to the leader.

        Sent as an ordinary mailbox message *from the stalled member*, so the
        leader receives it through the same inbound path as any other member
        message and can check in, reassign, or replace the assignee. This is
        the member reporting itself — not the leader polling member state,
        and not the leader reaching across processes to nudge a member.
        Best-effort: a delivery failure only costs one escalation.
        """
        message_manager = self._infra.message_manager
        team_backend = self._infra.team_backend
        if message_manager is None or team_backend is None:
            return
        leader_name = await team_backend.resolve_leader_member_name()
        own_name = self._blueprint.member_name
        if not leader_name or leader_name == own_name:
            return
        content = t(
            "dispatcher.stale_idle_claim_escalate",
            task_id=task.task_id,
            title=task.title,
            minutes=int(idle // 60),
        )
        await message_manager.send_message(
            content=content,
            to_member_name=leader_name,
            from_member_name=own_name,
        )
        team_logger.info(
            "[{}] escalated idle-stalled task {} to leader {}",
            own_name,
            task.task_id,
            leader_name,
        )

    async def _check_stale_pending_tasks(self) -> None:
        """Leader-only: self-prompt about unclaimed work while someone is free.

        Fires off the *leader's own* idle clock: how long the leader has sat
        idle is the run-time measure of "the team has been quiet this long",
        and unlike any ``updated_at``-based measure it cannot be inflated by
        a pause window. Two further preconditions keep the prompt honest —
        there must be unassigned PENDING work, and at least one member must
        actually be free to take it. A team whose members are simply all busy
        has a normal queue, not a stall, and must not be prompted about it.

        The list stays minimal (id + title); the model reads each task's
        details via ``view_task`` and picks the assignee off the roster
        itself — the dispatcher does not do the matching. A per-task throttle
        prevents follow-up polls from re-prompting inside one stale window.
        """
        if self._blueprint.role != TeamRole.LEADER:
            return
        task_manager = self._infra.task_manager
        team_backend = self._infra.team_backend
        if task_manager is None or team_backend is None:
            return

        idle = self._round.idle_seconds()
        if idle is None or idle < self._idle_pending_seconds:
            return

        all_pending = await task_manager.list_tasks(status=TaskStatus.PENDING.value)
        pending = [tk for tk in all_pending if not tk.assignee]

        # GC throttle entries for tasks that are no longer unassigned-pending.
        current_ids = {tk.task_id for tk in pending}
        for tid in [k for k in self._last_pending_nudge if k not in current_ids]:
            self._last_pending_nudge.pop(tid, None)

        if not pending:
            return

        # ``list_member_roster`` already drops the caller, so the leader's own
        # READY status never counts as a free worker here.
        roster = await team_backend.list_member_roster()
        if not any(entry.status == MemberStatus.READY.value for entry in roster):
            return

        now = time.time()
        fresh: list = []
        for task in pending:
            last = self._last_pending_nudge.get(task.task_id, 0.0)
            if now - last < self._idle_pending_seconds:
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
            "[leader] self-prompted about {} unclaimed pending task(s) after {}s idle",
            len(fresh),
            int(idle),
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

    The claim sweep also keeps the legacy ``task.updated_at`` timing that the
    autonomous handler moved off of (F_65): re-basing scheduled dispatch onto
    the idle clock was deliberately left out of that change's scope. Note the
    pause defect that motivated F_65 applies here too — a scheduled member
    idle across a long pause still measures the pause as staleness. Migrating
    this sweep is tracked as known-residual in F_65.
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

    async def _check_stale_claimed_tasks(self) -> None:
        """Find own active tasks that have been running past the stale threshold.

        Pins the pre-F_65 behaviour for scheduled dispatch. "Active" spans the
        three owned non-terminal conditions — PLANNING, IN_PROGRESS and
        IN_REVIEW. Measures how long a task has been active by reading the
        database ``updated_at`` column (bumped on every status transition).
        When the elapsed time exceeds ``_STALE_CLAIM_SECONDS`` the member
        feeds a nudge into its own agent loop. Only tasks assigned to *this*
        member are swept — the leader does not nudge another member about
        that member's stale work. A per-task throttle prevents follow-up
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
