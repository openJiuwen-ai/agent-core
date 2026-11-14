# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Task-board coordination events.

Owns ``TASK_CLAIMED`` (targeted assignment to one member) and the
six board-state events (``TASK_CREATED`` / ``TASK_UPDATED`` /
``TASK_COMPLETED`` / ``TASK_CANCELLED`` / ``TASK_UNBLOCKED`` /
``TASK_RELEASED``) that nudge an idle agent to re-evaluate the board.
A leader is nudged on every one; a teammate only on the subset that
can grow the claimable pool (see ``_TEAMMATE_NUDGE_EVENTS``).
Stale-task sweeping on poll ticks lives in :class:`StaleTaskHandler`.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.external.format import render_task_line
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.status import ExecutionStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.timefmt import format_absolute_time
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.task_loop.loop_queues import DeferredInput

# defer 的有界重试窗口（秒）：报告在途闸 defer 后多久重投看板再评。
# 取值权衡：正常消息投递是毫秒级，5s 覆盖事件循环饱和下的投递乱序；对死信/卡死场景只是把总结推迟 5s，可忽略。
_ALL_DONE_DEFER_RETRY_SECONDS = 5.0


class TaskBoardHandler(BaseCoordinationHandler):
    """Handle TASK_CLAIMED / TASK_REVOKED + 6 task-board state-transition events."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        # One-shot startup board survey (F_69), enqueued by the kernel.
        InnerEventType.INITIAL_POLL_TASK.value: "on_initial_task_poll",
        # Targeted to the affected member (self-branch), else board fallback
        TeamEvent.TASK_CLAIMED: "on_task_claimed",
        TeamEvent.TASK_REVOKED: "on_task_revoked",
        TeamEvent.TASK_CANCELLED: "on_task_cancelled",
        TeamEvent.TASK_UPDATED: "on_task_updated",
        # Task board (nudge idle agent / leader observes)
        TeamEvent.TASK_CREATED: "on_task_board_event",
        TeamEvent.TASK_PLAN_REQUEST: "on_task_board_event",
        TeamEvent.TASK_PLAN_RESPONSE: "on_task_plan_decision",
        # A scheduled task begins execution — the leader observes it; a
        # teammate is not nudged (a started task is neither pending nor
        # claimable, so it never grows the claimable pool).
        TeamEvent.TASK_STARTED: "on_task_board_event",
        TeamEvent.TASK_COMPLETED: "on_task_board_event",
        TeamEvent.TASK_UNBLOCKED: "on_task_board_event",
        TeamEvent.TASK_RELEASED: "on_task_board_event",
        # Verify gate (F_59): submit targets reviewers, verdict targets the author
        TeamEvent.TASK_SUBMITTED_FOR_REVIEW: "on_task_submitted_for_review",
        TeamEvent.TASK_VERIFIED: "on_task_verified",
        TeamEvent.TASK_REVISION_REQUESTED: "on_task_revision_requested",
    }

    # Board events that can *grow* the set of claimable tasks a teammate
    # cares about: a brand-new pending task appears (TASK_CREATED), a
    # blocked task's dependencies clear so it flips to pending
    # (TASK_UNBLOCKED), or a claimed task is reset back to pending and
    # re-enters the pool (TASK_RELEASED). No other board transition adds
    # claimable work — TASK_UPDATED only edits an existing pending/blocked
    # task's title/content, TASK_COMPLETED / TASK_CANCELLED remove tasks,
    # and TASK_CLAIMED / a plan request shrink or reserve the pool. An
    # idle teammate is therefore woken only by these three; every other
    # board event would spend a wasted round re-scanning an unchanged
    # claimable set. The leader is exempt (see ``on_task_board_event``)
    # because it owns board-level decisions and must observe every
    # transition.
    _TEAMMATE_NUDGE_EVENTS: ClassVar[frozenset[str]] = frozenset(
        {TeamEvent.TASK_CREATED, TeamEvent.TASK_UNBLOCKED, TeamEvent.TASK_RELEASED}
    )

    def __init__(self, host, blueprint, infra, poll_ctrl) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        # 上一次 all-done 实际渲染时刻（wall ms，与任务行 updated_at 同源）。
        # 这是「总结过」锚而非「见过」锚：报告轮只薄回执（提示词分工），
        # 见过不再意味总结过。None=本运行时还没渲染过 all-done。
        self._last_all_done_rendered_ms: int | None = None
        # 本运行时的起点（handler 随 dispatcher 在 kernel start 时重建）。
        # 板面最新终态早于此 = 上个生命周期的旧板（恢复回声），与本运行时
        # 有无轮次无关——终止性审计：「零轮次即抑制」会把
        # 恢复后本生命周期内的新完成一并压死（薄回执分工下再无总结），
        # 故改用运行时起点做旧板判据。
        self._runtime_started_ms: int = get_current_time()
        # 报告在途闸的 defer 代数签名（= 当时的 max(terminal updated_at)）。
        # 同一代板面只 defer 一次，第二次渲染放行（fail-open）——防成员死亡/
        # 未启动死信导致的永不总结。
        self._all_done_deferred_for: int | None = None
        # defer 的有界重试定时器：None=无挂起重试；一次性任务，
        # 渲染成功时取消、随 dispatcher 重建自然消亡。
        self._all_done_defer_retry_task: asyncio.Task | None = None

    async def on_task_claimed(self, event: EventMessage) -> None:
        """Directed assignment from another node.

        Self-claims are filtered upstream via ``sender_id``. When the
        claim targets self, route through ``deliver_input`` (steer /
        queue / start, picked by round state) and skip the board nudge
        — the targeted message already names the task. When the claim
        targets someone else, fall through to ``on_task_board_event``.
        In practice only the leader is nudged there: a foreign claim
        shrinks the claimable pool, so ``on_task_board_event`` filters
        it out for a teammate (TASK_CLAIMED is not in
        ``_TEAMMATE_NUDGE_EVENTS``). Without this fallback an idle
        leader would miss the board change until the next stale-pending
        poll, which can be up to ``_STALE_PENDING_SECONDS`` away.

        Self-assignment rendering is role-aware: a teammate / leader
        sees the teammate-oriented ``dispatcher.task_assigned_to_self``
        prompt ("call view_task and start working"). A human_agent
        avatar sees the HITT-specific ``hitt.task_assigned_to_self_human``
        prompt, which frames the event as a notification for the
        controlling human and tells the avatar LLM not to autonomously
        call tools — the avatar's actions are driven only by Inbox
        instructions from its controller.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        backend = self._infra.team_backend
        is_self_human = backend is not None and await backend.is_human_agent(member_name)
        if payload.member_name != member_name:
            # A claim targeting someone else nudges idle teammates / the
            # leader with the refreshed board. A human-agent avatar never
            # autonomously surveys the board for claimable work, so it
            # ignores other members' claims — only a claim addressed to
            # the avatar itself (its controller's assignment notification,
            # rendered below) is delivered.
            if is_self_human:
                return
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()

        if is_self_human:
            # Title lookup is best-effort: a glitch must not break the
            # dispatch loop. Same exception discipline as
            # ``MessageHandler._notify_human_agent_inbound``.
            title = ""
            try:
                task = await self._infra.task_manager.get(payload.task_id)
                if task is not None:
                    title = task.title or ""
            except Exception as exc:
                team_logger.warning(
                    "task_assigned_to_human_agent: title lookup failed for {}: {}",
                    payload.task_id,
                    exc,
                )
            content = render_event(
                kind="task-assigned",
                body=t("hitt.assigned_event", task_id=payload.task_id, title=title),
                task_id=payload.task_id,
                for_controller=True,
                note_kind="hitt-silence",
                note_text=t("hitt.silence_note"),
            )
        else:
            content = render_event(
                kind="task-assigned",
                body=t("dispatcher.task_assigned_to_self", task_id=payload.task_id),
                task_id=payload.task_id,
            )

        team_logger.info(
            "[{}] received TASK_CLAIMED for self, task_id={}, human_agent={}",
            member_name,
            payload.task_id,
            is_self_human,
        )
        await self._round.deliver_input(content)

    async def on_task_revoked(self, event: EventMessage) -> None:
        """A task this member held was reassigned away by the leader.

        Mirror of ``on_task_claimed`` for the *losing* side: the payload's
        ``member_name`` is the former assignee. When it targets self, steer
        the member off the now-foreign task (it may be mid-work on it) and
        point it back at the board. Events for other members are ignored —
        the reset already fired TASK_RELEASED to nudge the idle pool, and
        the leader drove the reassignment itself.

        A human-agent's claimed task is leader-immutable (reassign is
        refused upstream by ``UpdateTaskTool`` via ``_is_human_agent_locked``),
        so a revoke never targets a human avatar — no HITT rendering branch
        is needed here.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-revoked",
            body=t("dispatcher.task_revoked_from_self", task_id=payload.task_id),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_REVOKED for self, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_cancelled(self, event: EventMessage) -> None:
        """A task was cancelled; steer its assignee off it if that's me.

        ``member_name`` carries the cancelled task's (former) assignee. When
        it targets self, deliver a stop-and-resurvey prompt (the member may be
        mid-work on the now-void task). For any other member, fall through to
        the board handler so an idle leader still observes the board change
        (TASK_CANCELLED removes a task — it never nudges teammates).

        A human-agent's claimed task is leader-immutable (cancel is refused
        upstream), so a cancel never targets a human avatar — no HITT branch.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-cancelled",
            body=t("dispatcher.task_cancelled_to_self", task_id=payload.task_id),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_CANCELLED for self, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_updated(self, event: EventMessage) -> None:
        """A task's content was edited; tell its assignee to re-read if me.

        ``member_name`` carries the current owner (the task stays CLAIMED on
        edit). When it targets self, steer the member to re-read the revised
        content via view_task and continue — it keeps the task. For any other
        member, fall through to the board handler (leader observes; an edit
        does not grow the claimable pool, so teammates are not nudged).

        A human-agent's claimed task is leader-immutable (edit is refused
        upstream), so an update never targets a human avatar — no HITT branch.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-updated",
            body=t("dispatcher.task_content_updated_to_self", task_id=payload.task_id),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_UPDATED for self, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_plan_decision(self, event: EventMessage) -> None:
        """Notify a member when the leader approves or rejects its plan."""
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        if getattr(payload, "tool_call_id", ""):
            team_logger.debug(
                "[{}] task plan decision resumes pending interrupt, skip extra deliver_input",
                member_name,
            )
            return
        key = "dispatcher.task_plan_approved_to_self" if payload.approved else "dispatcher.task_plan_rejected_to_self"
        kind = "plan-approved" if payload.approved else "plan-rejected"
        content = render_event(
            kind=kind,
            body=t(
                key,
                task_id=payload.task_id,
                feedback=getattr(payload, "feedback", "") or "",
            ),
            task_id=payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_submitted_for_review(self, event: EventMessage) -> None:
        """A task entered the verify gate; wake its reviewers to act.

        The payload's ``member_name`` is the author and ``reviewer`` lists the
        members who must verify. When *this* member is one of the reviewers,
        steer it to inspect the deliverable and call ``verify_task``. For any
        other member, fall through to the board handler so an idle leader still
        observes the transition (a submit does not grow the claimable pool, so
        non-reviewer teammates are not nudged).
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        reviewers = getattr(payload, "reviewer", None) or []
        if member_name not in reviewers:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-review-request",
            body=t(
                "dispatcher.task_submitted_for_review_to_reviewer",
                task_id=payload.task_id,
                author=payload.member_name or "?",
            ),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_SUBMITTED_FOR_REVIEW as reviewer, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_revision_requested(self, event: EventMessage) -> None:
        """A reviewer failed the task; steer its author back to rework.

        ``member_name`` carries the author, who still holds the task (it moved
        IN_REVIEW -> IN_PROGRESS). When it targets self, deliver the reviewer's
        feedback and point the author at resubmission. For any other member,
        fall through to the board handler (leader observes).
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-revision",
            body=t(
                "dispatcher.task_revision_requested_to_self",
                task_id=payload.task_id,
                feedback=getattr(payload, "feedback", "") or "",
            ),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_REVISION_REQUESTED for self, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_verified(self, event: EventMessage) -> None:
        """A reviewer passed the task; free its author to pick up new work.

        ``member_name`` carries the author. The task completed while the author
        held it in review (the one-active invariant counts IN_REVIEW), so on a
        pass the author is unblocked — steer it back to the board. For any other
        member, fall through to the board handler (leader observes; downstream
        unblocks arrive separately via TASK_UNBLOCKED).
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()
        content = render_event(
            kind="task-verified",
            body=t("dispatcher.task_verified_to_self", task_id=payload.task_id),
            task_id=payload.task_id,
        )
        team_logger.info(
            "[{}] received TASK_VERIFIED for self, task_id={}",
            member_name,
            payload.task_id,
        )
        await self._round.deliver_input(content)

    async def on_task_board_event(self, event: EventMessage) -> None:
        """Nudge idle agent on TASK_CREATED/UPDATED/COMPLETED/CANCELLED/UNBLOCKED/RELEASED.

        Gates on the task-level check, not ``is_agent_running``: nudging
        during the pre-stream or finalize window would call
        ``_start_agent`` and overwrite the still-live agent task.
        TASK_CLAIMED is routed separately to ``on_task_claimed``.

        A teammate is woken only when the claimable set may have grown
        (``_TEAMMATE_NUDGE_EVENTS`` = TASK_CREATED / TASK_UNBLOCKED /
        TASK_RELEASED).
        Any other board churn — a member editing / completing /
        cancelling a task, or someone else claiming one — never adds
        claimable work, so waking an idle teammate for it just burns a
        round. The leader stays exempt: it owns board-level decisions
        (re-plan / assign / conclude) and must see every transition.
        ``resume_polls`` fires for every event regardless, so the nudge
        filter never stalls periodic polling.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        await self._poll.resume_polls()
        if self._blueprint.role != TeamRole.LEADER and event.event_type not in self._TEAMMATE_NUDGE_EVENTS:
            team_logger.debug(
                "[{}] skip teammate nudge: event {} does not grow claimable set",
                member_name,
                event.event_type,
            )
            return
        team_logger.debug("task trigger detected, nudging idle agent: member_name={}", member_name)
        await self._nudge_idle_agent(member_name)

    async def on_initial_task_poll(self, event: InnerEventMessage) -> None:
        """First board survey after this member's runtime comes up (F_69).

        The symmetric counterpart of the startup mailbox sweep: that one
        picks up messages sent while the member was down, this one picks up
        work assigned while it was down. A task assigned at creation time is
        announced only by a transient ``TASK_CLAIMED`` event, so a member
        that had not started yet never sees it and would otherwise sit idle
        until the stale-claim window elapses.

        Surfaces the same board slice as any other nudge — claimable work
        plus tasks assigned to this member — and inherits its silence on an
        empty slice, so a member starting into a board with nothing for it
        never burns a round.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        await self._nudge_idle_agent(member_name)

    async def _nudge_idle_agent(self, member_name: str, from_poll: bool = False) -> None:
        """Feed task context to an idle agent.

        Leader: reviews the full task board (every incomplete task) to
        decide whether to re-plan, assign, or conclude.
        Teammate: sees claimable tasks (pending + unassigned) plus tasks
        explicitly assigned to itself. Tasks claimed / in-flight by others are
        not surfaced — a teammate is woken to take on work it can act on, not
        to survey what everyone else is doing.

        The board rides the follow-up queue as a **deferred input**: it
        renders when the queue is drained (consumption time), not when the
        triggering event arrived. A member busy through a long round reads
        the board as of round end — never a survey rendered mid-round that
        is stale by delivery — and a board that would say nothing anymore
        (a poll finding all done, an empty teammate slice) drops out
        silently. Deferred boards share one collapse key, so a batch of
        them drains as a single fresh render.

        Args:
            member_name: The calling member's own name.
            from_poll: True when the nudge originates from a routine
                POLL_TASK tick. In that case an idle leader with no
                incomplete tasks (covers all-done / no-tasks /
                empty-team) renders nothing — real task-completion
                prompts arrive via the TASK_EVENTS path so polling
                should not re-trigger them.
        """

        async def render_board() -> str | None:
            all_tasks = await self._infra.task_manager.list_tasks()
            terminal = {"completed", "cancelled"}
            incomplete = [tk for tk in all_tasks if tk.status not in terminal]

            if from_poll and self._blueprint.role == TeamRole.LEADER and not incomplete:
                return None

            team_logger.debug("[{}] nudge_idle_agent: {} incomplete tasks", member_name, len(incomplete))
            if self._blueprint.role == TeamRole.LEADER:
                if not incomplete:
                    if not all_tasks:
                        # 零任务空板（新团首巡 / 任务行被删光）：无成果可汇总。
                        return None
                    # 见证锚=总结过而非见过：报告轮只薄回执，
                    # 见过不再意味总结过。两道时间闸：
                    # - 已渲染过 all-done 且之后无更新的终态迁移 → 回声/滞后
                    #   事件，抑制；
                    # - 板面最新终态早于本运行时起点（handler 随 kernel start
                    #   重建）→ 上个生命周期的旧板（恢复回声），抑制——未送达
                    #   的成果汇报由邮箱首扫兜底。注意不能用「零轮次」做判据：
                    #   恢复后本生命周期内的新完成（leader 尚无轮次）必须放行，
                    #   否则薄回执分工下该批成果永无总结。
                    terminal_ms = [
                        tk.updated_at for tk in all_tasks if tk.updated_at is not None
                    ]
                    sig = max(terminal_ms) if terminal_ms else None
                    last_rendered = self._last_all_done_rendered_ms
                    if last_rendered is not None and sig is not None and (
                            sig <= last_rendered):
                        team_logger.info(
                            "[{}] all-done suppressed: no terminal transition newer "
                            "than last all-done render (last_rendered={})",
                            member_name,
                            last_rendered,
                        )
                        return None
                    if sig is not None and sig <= self._runtime_started_ms:
                        team_logger.info(
                            "[{}] all-done suppressed: board drained before this "
                            "runtime started (runtime_started={})",
                            member_name,
                            self._runtime_started_ms,
                        )
                        return None
                    if not terminal_ms:
                        # 有任务行但全缺 updated_at（异常数据）——保守放行，维持旧行为。
                        team_logger.warning(
                            "[{}] all-done witness skipped: tasks missing updated_at",
                            member_name,
                        )
                    # 报告在途闸：交付物可能还在消息通道里
                    # （成员先完成后报告）或尚未被消费——本轮不渲染，改为挂一个
                    # 有界定时器，N 秒后重投看板再评（直接重排在 leader 空闲
                    # 时会被立即消费，同代 fail-open 微秒级触发，闸形同虚设；
                    # 定时窗口给消息投递留出到达时间）。同一代板面（同一
                    # max(updated_at)）只 defer 一次，重试渲染仍命中闸即
                    # fail-open——防成员死亡/未启动死信导致的永不总结。
                    if await self._reports_in_flight(member_name):
                        if self._all_done_deferred_for != sig:
                            self._all_done_deferred_for = sig
                            if (
                                    self._all_done_defer_retry_task is not None
                                    and not self._all_done_defer_retry_task.done()
                            ):
                                # 新代板面：继续沿用旧定时器会把本代应得的完整
                                # 重试窗口压缩到上一代剩下的时间——取消重排，
                                # 每一代都拿到完整窗口。
                                self._all_done_defer_retry_task.cancel()
                                self._all_done_defer_retry_task = None
                            if self._all_done_defer_retry_task is None:
                                self._all_done_defer_retry_task = asyncio.create_task(
                                    self._retry_all_done_after_defer(
                                        member_name, render_board
                                    )
                                )
                            team_logger.info(
                                "[{}] all-done deferred: report in flight "
                                "(unread message or member busy), retry in {}s",
                                member_name,
                                _ALL_DONE_DEFER_RETRY_SECONDS,
                            )
                            return None
                        team_logger.warning(
                            "[{}] all-done defer fail-open: report still in "
                            "flight on second render (sig={})",
                            member_name,
                            sig,
                        )
                    self._all_done_deferred_for = None
                    if self._all_done_defer_retry_task is not None:
                        # 渲染成功即清掉挂起的重试。本次渲染多半就是重试定时
                        # 器自己投递的——但它投递前已把字段清为 None（见
                        # _retry_all_done_after_defer），走到这里只可能是另
                        # 一条路径先放行了渲染；只取消仍在睡眠的定时器，不碰
                        # 已完成的。
                        retry_task = self._all_done_defer_retry_task
                        self._all_done_defer_retry_task = None
                        if not retry_task.done():
                            retry_task.cancel()
                    # 渲染锚=本代签名 sig（最新终态迁移的 updated_at），不是
                    # 打锚时刻的壁钟：快照（list_tasks）与打锚之间发生的完成
                    # 若用壁钟锚会被误判为「已总结的回声」而永失总结；
                    # sig 锚只放行严格更新的终态迁移，语义与
                    # _all_done_deferred_for 的代际一致。
                    self._last_all_done_rendered_ms = (
                        sig if sig is not None else get_current_time()
                    )
                    lifecycle = self._blueprint.lifecycle
                    if lifecycle == "persistent":
                        prompt = t("dispatcher.all_done_persistent")
                    else:
                        prompt = t("dispatcher.all_done_temporary")
                    return render_event(kind="all-done", body=prompt)
                lines = [t("dispatcher.leader_task_board")]
                board_tasks = incomplete
            else:
                # Teammate: surface claimable work (pending + unassigned) and
                # explicitly assigned self work. Assigned pending tasks may have
                # been created before this member started; feeding them here makes
                # the startup poll actionable without exposing other members' work.
                board_tasks = [
                    task
                    for task in incomplete
                    if (task.status == "pending" and not task.assignee) or task.assignee == member_name
                ]
                if not board_tasks:
                    return None
                lines = [t("dispatcher.teammate_task_list")]

            now_ms = get_current_time()
            for task in board_tasks:
                lines.append(render_task_line(task, now_ms=now_ms))
            lines.append(t("dispatcher.task_board_rendered_at", render_time=format_absolute_time(now_ms)))
            return render_event(kind="task-board", body="\n".join(lines))

        # A board survey is a reminder, not a reason to drop what the member is
        # doing (decision dimension 3): it never says "the work you are on is
        # void", so it goes to the follow-up queue and is read when the current
        # round ends. Steering it in used to interrupt the member's reasoning
        # with a survey it had no reason to act on yet. The all-done variant
        # rides the same deferred path — it too arrives fresh at round end
        # rather than steering into the active round.
        await self._round.deliver_input(
            DeferredInput(
                kind="task-board",
                collapse_key="task-board",
                renderer=render_board,
            ),
            use_steer=False,
        )

    async def _reports_in_flight(self, member_name: str) -> bool:
        """是否有交付物尚在消息通道/成员手中：未读消息 ∨ 成员执行中。

        供 all-done 渲染前的报告在途闸使用：未读消息判定复用
        message_manager.has_unread_messages（不含广播——广播是频道语义，
        不携带定向交付物）；成员在途判定读成员行的 execution_status（排除
        调用者自己——leader 的忙闲不是报告信号）。all-done 渲染每代板面
        至多一次，这两个读取的稳态增量可忽略。fail-open：读失败按无在途
        放行并 WARNING——一次瞬时读失败不该永久压住总结。
        """
        try:
            mm = self._infra.message_manager
            if mm is not None and await mm.has_unread_messages(include_broadcast=False):
                return True
            backend = self._infra.team_backend
            if backend is None:
                return False
            members = await backend.db.member.get_team_members(backend.team_name)
            return any(
                m.member_name != member_name
                and m.execution_status == ExecutionStatus.RUNNING.value
                for m in members
            )
        except Exception:
            team_logger.warning(
                "[{}] all-done in-flight probe failed; rendering unsuppressed",
                member_name,
                exc_info=True,
            )
            return False

    async def _retry_all_done_after_defer(self, member_name, renderer) -> None:
        """有界定时器重试：defer 后 N 秒重投看板，让闸在报告投递后重评。

        不直接重排的原因：leader 空闲时重排的 DeferredInput 会被立即消费，
        同代 fail-open 微秒级触发，闸形同虚设。重投的渲染若仍命中闸，按
        同代 fail-open 放行——死信/卡死最多把总结推迟一个窗口。重试失败
        不向上抛：渲染会随后续任务事件自然再评，一次定时器故障不该吃掉
        总结链路。
        """
        try:
            await asyncio.sleep(_ALL_DONE_DEFER_RETRY_SECONDS)
            # 投递前清字段：本次重投触发的渲染在成功路径会看到字段已空，
            # 不会回头取消正在 await 投递返回的本任务自身。
            if self._all_done_defer_retry_task is asyncio.current_task():
                self._all_done_defer_retry_task = None
            await self._round.deliver_input(
                DeferredInput(
                    kind="task-board",
                    collapse_key="task-board",
                    renderer=renderer,
                ),
                use_steer=False,
            )
        except asyncio.CancelledError:
            # 本任务的取消只有两个来源：渲染成功路径的主动清理、新代板面
            # defer 的重排——都是我们自己的刻意中止，不是外部关停语义。
            # 无人 await 本任务的终态（done() 两种结局都为真），静默返回
            # 即可；重新抛出只会让任务落 cancelled 态制造噪音。
            # 注意这是本任务专用的收窄豁免，不代表别处可以吞 CancelledError。
            return
        except Exception:
            team_logger.warning(
                "[{}] all-done defer retry failed",
                member_name,
                exc_info=True,
            )


class ScheduledTaskBoardHandler(TaskBoardHandler):
    """Task-board reactions of a scheduled-dispatch team instance (F_62).

    Under scheduled dispatch every task handoff — start, review request,
    rework, verified — arrives as a leader-identity mailbox message from the
    scheduler (``agent/scheduling/``), and the leader's board awareness comes
    from the scheduler's digests and escalations. What remains for the
    event-driven side is therefore only:

    * the targeted ownership steers inherited from the base class
      (``TASK_CLAIMED`` / ``TASK_REVOKED`` / ``TASK_CANCELLED`` /
      ``TASK_UPDATED`` — the leader's ``update_task`` works the same in both
      modes), and
    * keeping periodic polling alive on board churn.

    The verify-gate self-steers and the idle-agent board surveys of the base
    class would double-deliver next to the scheduler's mails, so this class
    redefines what a board event *means* in this mode: resume polls, nothing
    else. Selection happens where handlers are assembled
    (``EventDispatcher``), never inside a handler method.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        **TaskBoardHandler.EVENT_METHOD_MAP,
        # Review voting exists only under scheduled dispatch: votes are pure
        # board observability — the leader-side scheduler tallies them.
        TeamEvent.TASK_REVIEW_VOTE: "on_task_board_event",
        # The verify-gate handoffs are the scheduler's mails in this mode.
        TeamEvent.TASK_SUBMITTED_FOR_REVIEW: "on_task_board_event",
        TeamEvent.TASK_VERIFIED: "on_task_board_event",
        TeamEvent.TASK_REVISION_REQUESTED: "on_task_board_event",
    }

    async def on_initial_task_poll(self, event: InnerEventMessage) -> None:
        """No startup board survey under scheduled dispatch (F_69).

        The autonomous survey exists because a member that was down when
        work was assigned to it has no other way to learn of it. Under
        scheduled dispatch it does: the scheduler's start scan runs on every
        wake, and its handoff lands in the mailbox as a durable row that the
        startup mailbox sweep drains. Surveying the board here would deliver
        the same task a second time, next to the scheduler's mail.
        """
        team_logger.debug(
            "[{}] scheduled dispatch: startup board survey skipped",
            self._blueprint.member_name,
        )

    async def on_task_board_event(self, event: EventMessage) -> None:
        """Board churn in scheduled dispatch: keep polling alive, wake nobody.

        There is no claimable pool (every task is pre-assigned) and the
        scheduler owns both member handoffs and leader awareness — an
        event-driven survey here would only burn rounds.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        await self._poll.resume_polls()
        team_logger.debug(
            "[{}] scheduled dispatch: board event {} observed, no nudge",
            member_name,
            event.event_type,
        )
