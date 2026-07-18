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

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.external.format import render_task_line
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger


class TaskBoardHandler(BaseCoordinationHandler):
    """Handle TASK_CLAIMED / TASK_REVOKED + 6 task-board state-transition events."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
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

    async def _nudge_idle_agent(self, member_name: str, from_poll: bool = False) -> None:
        """Feed task context to an idle agent.

        Leader: reviews the full task board (every incomplete task) to
        decide whether to re-plan, assign, or conclude.
        Teammate: sees only claimable tasks (pending + unassigned) to
        pick one. Tasks already claimed / in-flight by others are not
        surfaced — a teammate is woken to take on new claimable work,
        not to survey what everyone else is doing.

        Args:
            member_name: The calling member's own name.
            from_poll: True when the nudge originates from a routine
                POLL_TASK tick. In that case an idle leader with no
                incomplete tasks (covers all-done / no-tasks /
                empty-team) returns silently — real task-completion
                prompts arrive via the TASK_EVENTS path so polling
                should not re-trigger them.
        """
        all_tasks = await self._infra.task_manager.list_tasks()
        terminal = {"completed", "cancelled"}
        incomplete = [tk for tk in all_tasks if tk.status not in terminal]

        if from_poll and self._blueprint.role == TeamRole.LEADER and not incomplete:
            return

        team_logger.debug("[{}] nudge_idle_agent: {} incomplete tasks", member_name, len(incomplete))
        if self._blueprint.role == TeamRole.LEADER:
            if not incomplete:
                lifecycle = self._blueprint.lifecycle
                if lifecycle == "persistent":
                    prompt = t("dispatcher.all_done_persistent")
                else:
                    prompt = t("dispatcher.all_done_temporary")
                await self._round.deliver_input(render_event(kind="all-done", body=prompt))
                return
            lines = [t("dispatcher.leader_task_board")]
            board_tasks = incomplete
        else:
            # Teammate: surface only claimable work (pending + unassigned).
            # No claimable task means nothing to pick up — stay idle
            # rather than dump others' in-flight tasks into the round.
            board_tasks = [task for task in incomplete if task.status == "pending" and not task.assignee]
            if not board_tasks:
                return
            lines = [t("dispatcher.teammate_task_list")]

        now_ms = get_current_time()
        for task in board_tasks:
            lines.append(render_task_line(task, now_ms=now_ms))

        await self._round.deliver_input(render_event(kind="task-board", body="\n".join(lines)))


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
