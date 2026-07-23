# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamScheduler — leader-side decision engine for scheduled dispatch (F_62).

The scheduler does not understand events; it understands the board. Every
trigger (task/member transport event, ``POLL_TASK`` tick, the
``SCHEDULER_SCAN`` echo of a leader-local mutation, activation) runs the same
idempotent scan pair:

* **start scan** — for each member with no active task, start its earliest
  ``PENDING(assignee)`` task via ``TeamTaskManager.start_task`` (CAS) and hand
  it over with a leader-identity mailbox message (delivery lazily starts the
  member runtime, so being offline is never a start precondition).
* **review scan** — dispatch review requests for freshly opened rounds, tally
  votes (``verdict.judge``), settle decided rounds via ``settle_review``,
  escalate to the leader when the round ceiling is exhausted or a round
  stalls, and re-nudge silent reviewers.

Crash recovery is the same code path: activation runs the scan, and the CAS
transitions make replays no-ops. The scheduler never delivers input to
another member's round — member handoffs go through the mailbox; only the
leader itself receives direct input injections (digests / escalations).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.scheduling import render
from openjiuwen.agent_teams.agent.scheduling.verdict import (
    VERDICT_FAIL,
    VERDICT_PASS,
    judge,
)
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
    from openjiuwen.agent_teams.agent.infra import TeamInfra

# Seconds before a silent reviewer of an open round gets one reminder DM.
# Package constant in the spirit of the stale-claim constants — the
# escalation timeout (spec-configurable ``review_stall_timeout``) is the
# knob; this softer step is not worth a spec field.
_REVIEW_RENUDGE_SECONDS = 600

# Fixpoint bound for one scan: a settle unblocks dependents whose starts the
# leader's own event echo would only pick up on the next wake; looping the
# scan a few times converges immediately instead. Idempotent CAS writes make
# extra passes no-ops.
_MAX_SCAN_PASSES = 4


@runtime_checkable
class SchedulerHost(Protocol):
    """Narrow host surface the scheduler needs from the owning TeamAgent.

    Deliberately tiny: ``deliver_input`` may only ever target the leader
    itself (digests / escalations), and ``auto_start_member`` is the
    idempotent lazy-startup primitive (UNSTARTED -> STARTING CAS) the
    mailbox handoffs piggyback on.
    """

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        """Inject content into the leader's own input stream."""
        ...

    async def auto_start_member(self, member_name: str) -> bool:
        """Best-effort start of one UNSTARTED member runtime."""
        ...


class TeamScheduler:
    """Leader-side scheduled-dispatch decision engine. See module docstring."""

    def __init__(
        self,
        host: SchedulerHost,
        *,
        blueprint: "TeamAgentBlueprint",
        infra: "TeamInfra",
    ) -> None:
        self._host = host
        self._blueprint = blueprint
        self._infra = infra
        spec = blueprint.spec
        self._threshold: float = spec.verify_vote_threshold
        self._default_max_rounds: int = spec.default_max_review_rounds
        self._stall_timeout_seconds: int = spec.review_stall_timeout
        self._active = False
        # Per-(task_id, review_round) bookkeeping. All in-memory: a leader
        # restart at worst re-sends one review request / escalation, which a
        # reader can correlate; the board truth itself lives in the DB.
        self._review_dispatched: set[tuple[str, int]] = set()
        self._renudged_at: dict[tuple[str, int], int] = {}
        self._escalated: set[tuple[str, int]] = set()
        self._digested_tasks: set[str] = set()
        self._all_done_announced = False

    @property
    def is_active(self) -> bool:
        """Whether the scheduler is currently driving the board."""
        return self._active

    async def activate(self) -> None:
        """Arm the scheduler and reconcile the board once.

        Called when ``build_team`` chose scheduled dispatch and on every
        ``kernel.start`` against a team whose persisted effective mode is
        scheduled (warm resume / cold recovery) — activation *is* the
        recovery sweep. Idempotent.
        """
        first = not self._active
        self._active = True
        self._all_done_announced = False
        if first:
            team_logger.info("[scheduler] activated for team %s", self._blueprint.team_name or "?")
        await self._scan()

    def deactivate(self) -> None:
        """Disarm the scheduler (kernel pause/stop)."""
        self._active = False

    async def on_event(self, event: CoordinationEvent) -> None:
        """Wake hint from the kernel's composed wake callback.

        Any task/member transport event, the ``POLL_TASK`` tick, and the
        ``SCHEDULER_SCAN`` echo all mean the same thing — "the board may have
        changed" — and trigger the same scan. Terminal-transition events
        additionally feed the leader digests (those cannot be derived from a
        scan: an event fires once, a scan sees the same terminal row forever).
        """
        if not self._active:
            return
        try:
            if isinstance(event, InnerEventMessage):
                if event.event_type in (InnerEventType.POLL_TASK, InnerEventType.SCHEDULER_SCAN):
                    await self._scan()
                return
            event_type = event.event_type
            if event_type == TeamEvent.TASK_COMPLETED:
                await self._digest_completion(event, verified=False)
            elif event_type == TeamEvent.TASK_VERIFIED:
                await self._digest_completion(event, verified=True)
            elif event_type == TeamEvent.TASK_LIST_DRAINED:
                await self._announce_all_done(event)
            if event_type.startswith("task_") or event_type.startswith("member_"):
                await self._scan()
        except Exception:
            # Mirror the coordination framework's swallow semantics: a scan
            # failure must never kill the event-bus loop; the next wake or
            # poll retries the same idempotent pass.
            team_logger.error("[scheduler] scan failed", exc_info=True)

    # ------------------------------------------------------------------
    # Scan pair
    # ------------------------------------------------------------------

    async def _scan(self) -> None:
        """Run both reconcile passes to a bounded fixpoint."""
        task_manager = self._infra.task_manager
        if task_manager is None:
            return
        for _ in range(_MAX_SCAN_PASSES):
            acted = await self._reconcile_starts(task_manager)
            acted = await self._reconcile_reviews(task_manager) or acted
            if not acted:
                return

    async def _reconcile_starts(self, task_manager) -> bool:
        """Start each idle member's earliest assigned PENDING task."""
        pending = await task_manager.list_tasks(status=TaskStatus.PENDING.value)
        queue_by_member: dict[str, list] = {}
        for task in pending:
            if task.assignee:
                queue_by_member.setdefault(task.assignee, []).append(task)

        acted = False
        for member_name, queue in queue_by_member.items():
            candidate = min(queue, key=lambda task: (task.updated_at or 0, task.task_id))
            busy_task_id = await task_manager.get_other_active_task_id(member_name, candidate.task_id)
            if busy_task_id:
                continue
            result = await task_manager.start_task(candidate.task_id)
            if not result.ok:
                # Lost a race or the member turned busy — the next wake retries.
                team_logger.debug(
                    "[scheduler] start of task %s for %s did not apply: %s",
                    candidate.task_id,
                    member_name,
                    result.reason,
                )
                continue
            started = await task_manager.get(candidate.task_id)
            if started is None:
                continue
            await self._send_as_leader(member_name, render.meta_task_start(started))
            acted = True
        return acted

    async def _reconcile_reviews(self, task_manager) -> bool:
        """Dispatch, judge, settle, escalate and re-nudge open review rounds."""
        in_review = await task_manager.list_tasks(status=TaskStatus.IN_REVIEW.value)
        now_ms = get_current_time()
        acted = False
        for task in in_review:
            reviewers = task.reviewers()
            if not reviewers:
                # A reviewer-less task cannot be IN_REVIEW through the normal
                # flow; leave it to the leader's board view rather than guess.
                team_logger.warning("[scheduler] task %s is in_review without reviewers", task.task_id)
                continue

            round_key = (task.task_id, task.review_round)
            if round_key not in self._review_dispatched:
                for reviewer in reviewers:
                    await self._send_as_leader(reviewer, render.meta_review_request(task))
                self._review_dispatched.add(round_key)

            tally = await task_manager.get_review_tally(task)
            verdict = judge(
                tally["pass_count"],
                tally["fail_count"],
                tally["reviewer_count"],
                self._threshold,
            )
            if verdict == VERDICT_PASS:
                acted = await self._settle_pass(task_manager, task) or acted
            elif verdict == VERDICT_FAIL:
                acted = await self._settle_fail_or_escalate(task_manager, task, tally) or acted
            else:
                await self._handle_undecided(task, tally, now_ms)
        return acted

    async def _settle_pass(self, task_manager, task) -> bool:
        result = await task_manager.settle_review(task.task_id, "pass")
        if not result.ok:
            team_logger.debug("[scheduler] pass settle for %s did not apply: %s", task.task_id, result.reason)
            return False
        if task.assignee:
            await self._send_as_leader(task.assignee, render.meta_verified_report(task))
        await self._digest_task_done(task_manager, task.task_id, task.title, verified=True)
        return True

    async def _settle_fail_or_escalate(self, task_manager, task, tally: dict) -> bool:
        max_rounds = task.max_review_rounds or self._default_max_rounds
        feedback = render.format_fail_feedback(tally["fail_feedback"])
        if task.review_round >= max_rounds:
            await self._escalate(task, render.render_leader_escalation_rounds(task, feedback))
            return False
        result = await task_manager.settle_review(task.task_id, "fail", feedback)
        if not result.ok:
            team_logger.debug("[scheduler] fail settle for %s did not apply: %s", task.task_id, result.reason)
            return False
        if task.assignee:
            await self._send_as_leader(task.assignee, render.meta_rework(task, max_rounds, feedback))
        return True

    async def _handle_undecided(self, task, tally: dict, now_ms: int) -> None:
        """Stall handling for an open round: soft re-nudge, then escalation."""
        round_key = (task.task_id, task.review_round)
        age_ms = now_ms - (task.updated_at or now_ms)
        if age_ms >= self._stall_timeout_seconds * 1000:
            voted = list(tally["voted"])
            pending = [name for name in task.reviewers() if name not in tally["voted"]]
            await self._escalate(
                task,
                render.render_leader_escalation_stall(
                    task,
                    minutes=age_ms // 60000,
                    voted=voted,
                    pending=pending,
                ),
            )
            return
        if age_ms < _REVIEW_RENUDGE_SECONDS * 1000:
            return
        last = self._renudged_at.get(round_key, 0)
        if now_ms - last < _REVIEW_RENUDGE_SECONDS * 1000:
            return
        self._renudged_at[round_key] = now_ms
        for reviewer in task.reviewers():
            if reviewer not in tally["voted"]:
                await self._send_as_leader(reviewer, render.meta_review_renudge(task))

    # ------------------------------------------------------------------
    # Delivery primitives
    # ------------------------------------------------------------------

    async def _send_as_leader(self, member_name: str, meta: dict) -> None:
        """Leader-identity mailbox handoff + idempotent lazy member startup.

        The row carries the delivery payload, not the text: ``content`` is
        empty and ``meta`` names the template plus the task it binds to, so the
        recipient's mailbox drain renders it against the task row as it stands
        *then* (F_63). The row lands first (durable — an offline member drains
        it on its first mailbox sweep), then the runtime is started best-effort
        via the same ``UNSTARTED -> STARTING`` CAS the send_message tool uses;
        an already-running member simply gets the MESSAGE wake. Per-recipient
        failures are logged and never abort the scan.
        """
        message_manager = self._infra.message_manager
        if message_manager is None:
            return
        try:
            message_id = await message_manager.send_message(content="", to_member_name=member_name, meta=meta)
            if not message_id:
                team_logger.error("[scheduler] handoff message to %s was not delivered", member_name)
            await self._host.auto_start_member(member_name)
        except Exception:
            team_logger.error("[scheduler] handoff to %s failed", member_name, exc_info=True)

    async def _escalate(self, task, content: str) -> None:
        """Inject an escalation into the leader once per (task, round)."""
        round_key = (task.task_id, task.review_round)
        if round_key in self._escalated:
            return
        self._escalated.add(round_key)
        team_logger.info("[scheduler] escalating task %s round %s to the leader", task.task_id, task.review_round)
        await self._host.deliver_input(content, use_steer=False)

    async def _digest_task_done(self, task_manager, task_id: str, title: str, *, verified: bool) -> None:
        """One-line terminal digest to the leader, once per task.

        Also the all-done fallback for leader-settled boards: the drained
        event a leader-local settle publishes is self-filtered off the bus,
        so a zero-remaining digest announces the wrap-up directly.
        """
        if task_id in self._digested_tasks:
            return
        self._digested_tasks.add(task_id)
        tasks = await task_manager.list_tasks()
        terminal = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
        remaining = sum(1 for task in tasks if task.status not in terminal)
        await self._host.deliver_input(
            render.render_leader_task_done(task_id, title, verified=verified, remaining=remaining),
            use_steer=False,
        )
        if remaining == 0 and tasks and not self._all_done_announced:
            self._all_done_announced = True
            await self._host.deliver_input(
                render.render_leader_all_done(len(tasks)),
                use_steer=False,
            )

    async def _digest_completion(self, event: EventMessage, *, verified: bool) -> None:
        """Digest a completion observed via a transport event.

        Covers transitions the scheduler did not perform itself (a member's
        direct no-reviewer completion). Settles performed locally are digested
        at the settle site — the leader's own events never come back through
        the bus (self-filtered).
        """
        task_manager = self._infra.task_manager
        if task_manager is None:
            return
        payload = event.get_payload()
        task_id = payload.task_id
        if task_id in self._digested_tasks:
            return
        task = await task_manager.get(task_id)
        title = task.title if task is not None else ""
        await self._digest_task_done(task_manager, task_id, title, verified=verified)

    async def _announce_all_done(self, event: EventMessage) -> None:
        """Inject the final all-terminal digest into the leader, once."""
        if self._all_done_announced:
            return
        self._all_done_announced = True
        payload = event.get_payload()
        await self._host.deliver_input(
            render.render_leader_all_done(payload.task_count),
            use_steer=False,
        )
