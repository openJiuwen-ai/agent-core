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
* **handoff scan** — for each active task, make sure its *current* owner
  holds a start handoff in the mailbox; an owner that acquired the task by
  ``update_task`` reassignment (status preserved, so the start scan never
  fires for it) is handed the same leader-identity start message, lazily
  started like any other recipient.
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

import asyncio
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.scheduling import render
from openjiuwen.agent_teams.agent.scheduling.reviewer_spec import build_reviewer_agent_spec
from openjiuwen.agent_teams.agent.scheduling.verdict import (
    VERDICT_FAIL,
    VERDICT_PASS,
    settle_review_tally,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.message_template import parse_meta, task_links
from openjiuwen.agent_teams.prompts.loader import TemplateLoader, load_template, make_template_loader
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

# Map reviewer type to prompt template basename.
_REVIEWER_TEMPLATE_MAP = {
    "verifier": "reviewer_verifier",
    "inspector": "reviewer_inspector",
    "challenger": "reviewer_challenger",
}


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
        build_context: Any = None,
    ) -> None:
        self._host = host
        self._blueprint = blueprint
        self._infra = infra
        self._build_context = build_context
        spec = blueprint.spec
        self._default_max_rounds: int = spec.default_max_review_rounds
        self._stall_timeout_seconds: int = spec.review_stall_timeout
        self._active = False
        # Per-(task_id, review_round) bookkeeping. All in-memory: a leader
        # restart at worst re-sends one review request / escalation, which a
        # reader can correlate; the board truth itself lives in the DB.
        self._review_dispatched: set[tuple[str, int]] = set()
        self._renudged_at: dict[tuple[str, int], int] = {}
        self._escalated: set[tuple[str, int]] = set()
        self._summary_requested: set[tuple[str, int]] = set()
        self._digested_tasks: set[str] = set()
        # (task_id, owner) pairs whose start handoff is known to sit in the
        # mailbox — written when this process sends one, or when the handoff
        # scan finds one already there. Saves the scan a mailbox query per
        # active task on every wake; after a restart each pair costs exactly
        # one query to re-learn.
        self._handoff_known: set[tuple[str, str]] = set()
        self._all_done_announced = False
        self._review_feedback_dispatched: set[tuple[str, int]] = set()
        self._review_feedback_tasks: set[asyncio.Task[Any]] = set()
        self._team_review_feedback_dispatched = False
        # A-class loader bound lazily from the team backend's workspace cache:
        # reviewer_* templates read evolved values.
        self._template_loader_cache: TemplateLoader | None = None

    @property
    def _template_loader(self) -> TemplateLoader:
        """Per-team A-class loader, bound once from ``_infra.team_backend``."""
        loader = self._template_loader_cache
        if loader is None:
            backend = self._infra.team_backend
            loader = make_template_loader(backend.workspace_cache if backend is not None else None)
            self._template_loader_cache = loader
        return loader

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
        self._team_review_feedback_dispatched = False
        if first:
            team_logger.info("[scheduler] activated for team %s", self._blueprint.spec.team_name or "?")
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
        """Run the four reconcile passes to a bounded fixpoint."""
        task_manager = self._infra.task_manager
        if task_manager is None:
            return
        for _ in range(_MAX_SCAN_PASSES):
            acted = await self._reconcile_starts(task_manager)
            acted = await self._reconcile_handoffs(task_manager) or acted
            acted = await self._reconcile_reviews(task_manager) or acted
            acted = await self._reconcile_retirements(task_manager) or acted
            if not acted:
                return

    async def _reconcile_retirements(self, task_manager) -> bool:
        """Request retirement of task-scoped members whose board is empty (F_118).

        A member declared inline on ``create_task`` carries a lifetime policy
        on its row. Once it is live and nothing is left for it, the scheduler
        issues the ordinary ``shutdown_member`` request; the member settles
        into SHUTDOWN or DORMANT itself, from that same policy, when it is
        truly idle (S_03 mechanism 18) — this pass never touches a round.

        Three conditions, each closing one hole:

        * idle (``READY``) — never BUSY (mid-round: replies to what it is
          saying would be stranded), UNSTARTED (row landed, task not yet),
          RESTARTING (a wake in progress), or already requested;
        * no non-terminal task on its board — ``IN_REVIEW`` / ``PENDING`` /
          ``BLOCKED`` all count as open, so a submitted task keeps its author
          around for rework and the post-verify report;
        * nothing unread — a just-woken DORMANT member is READY before it has
          read the message that woke it; requesting retirement in that window
          would settle it without a round and strand the message.
        """
        backend = self._infra.team_backend
        message_manager = self._infra.message_manager
        if backend is None or message_manager is None:
            return False
        candidates = await backend.list_retirable_members()
        if not candidates:
            return False
        open_counts = await task_manager.open_task_counts_by_assignee()
        unread = await message_manager.members_with_unread([name for name, _status, _lifetime in candidates])
        acted = False
        for member_name, status, lifetime in candidates:
            if open_counts.get(member_name, 0) or member_name in unread:
                continue
            result = await backend.shutdown_member(member_name)
            if not result.ok:
                team_logger.debug(
                    "[scheduler] retirement request for %s (status=%s) did not apply: %s",
                    member_name,
                    status,
                    result.reason,
                )
                continue
            team_logger.info(
                "[scheduler] retirement requested for %s (lifetime=%s, was %s)",
                member_name,
                lifetime,
                status,
            )
            acted = True
        return acted

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
            await self._send_task_start(started)
            acted = True
        return acted

    async def _reconcile_handoffs(self, task_manager) -> bool:
        """Hand every active task to an owner that never received its start.

        The start scan hands a task over exactly when it flips it out of
        ``PENDING``. A leader ``update_task(assignee=...)`` on an already
        active task (``TASK_REASSIGNABLE_STATUSES`` keeps the status) therefore
        produces no handoff: the former owner is steered off by
        ``TASK_REVOKED``, but the new owner only sees a transient
        ``TASK_CLAIMED`` — nothing if its runtime is not up yet, and under
        scheduled dispatch there is no startup board survey to catch up from
        (``ScheduledTaskBoardHandler.on_initial_task_poll``). The task then
        sits ``IN_PROGRESS`` with nobody working on it until the owner's own
        stale sweep fires or the leader notices.

        This pass closes that gap from the board alone: for each active task
        the current owner must hold a leader-identity start handoff bound to
        that task in its mailbox (durable, so an offline owner drains it on
        startup). Missing → send the same start message the start scan would
        have sent, which also lazily starts the owner. Idempotent via the
        mailbox itself plus the in-process ``_handoff_known`` memo; a
        restarted leader re-learns each pair with one mailbox read.
        """
        message_manager = self._infra.message_manager
        if message_manager is None:
            return False
        active: list[Any] = []
        for status in (TaskStatus.PLANNING.value, TaskStatus.IN_PROGRESS.value):
            active.extend(await task_manager.list_tasks(status=status))
        acted = False
        for task in active:
            owner = task.assignee
            if not owner or (task.task_id, owner) in self._handoff_known:
                continue
            if await self._owner_has_start_handoff(message_manager, owner, task.task_id):
                self._handoff_known.add((task.task_id, owner))
                continue
            team_logger.info(
                "[scheduler] active task %s has no start handoff for its owner %s; sending one",
                task.task_id,
                owner,
            )
            if await self._send_task_start(task):
                acted = True
        return acted

    async def _owner_has_start_handoff(self, message_manager, owner: str, task_id: str) -> bool:
        """Whether ``owner``'s mailbox holds a leader start handoff for ``task_id``.

        Read or unread does not matter — a drained handoff was delivered, an
        undrained one will be on the owner's next mailbox sweep.
        """
        rows = await message_manager.get_messages(
            to_member_name=owner,
            from_member_name=message_manager.member_name,
        )
        for row in rows:
            if render.is_task_start_meta(parse_meta(row.meta), task_id):
                return True
        return False

    async def _send_task_start(self, task) -> bool:
        """Send the owner its start handoff and remember that it is in the mailbox."""
        owner = task.assignee
        sent = await self._send_as_leader(owner, render.meta_task_start(task))
        if sent:
            self._handoff_known.add((task.task_id, owner))
        return sent

    async def _reconcile_reviews(self, task_manager) -> bool:
        """Dispatch, judge, settle, escalate and re-nudge open review rounds."""
        backend = self._infra.team_backend
        if backend is not None and not backend.task_verification_enabled():
            return False
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
                self._review_dispatched.add(round_key)
                for reviewer in reviewers:
                    await self._dispatch_to_reviewer(reviewer, task)

            tally = await task_manager.get_review_tally(task)
            verdict = settle_review_tally(tally)
            if verdict == VERDICT_PASS:
                if await self._settle_pass(task_manager, task):
                    team_logger.info(
                        "[judge-pass] task=%s round=%d tally(pass=%d fail=%d total=%d)",
                        task.task_id,
                        task.review_round,
                        tally["pass_count"],
                        tally["fail_count"],
                        tally["reviewer_count"],
                    )
                    acted = True
            elif verdict == VERDICT_FAIL:
                if await self._settle_fail_or_escalate(task_manager, task, tally):
                    team_logger.info(
                        "[judge-fail] task=%s round=%d tally(pass=%d fail=%d total=%d)",
                        task.task_id,
                        task.review_round,
                        tally["pass_count"],
                        tally["fail_count"],
                        tally["reviewer_count"],
                    )
                    acted = True
            else:
                await self._handle_undecided(task, tally, now_ms)
        return acted

    async def _settle_pass(self, task_manager, task) -> bool:
        result = await task_manager.settle_review(task.task_id, "pass")
        if not result.ok:
            team_logger.debug("[scheduler] pass settle for %s did not apply: %s", task.task_id, result.reason)
            return False
        # The author is not woken for a pass (F_119): the leader gets the result
        # through the digest, and the author learns it on its next input via
        # the task-outcome channel of its own rail.
        await self._digest_task_done(task_manager, task, verified=True)
        return True

    async def _settle_fail_or_escalate(self, task_manager, task, tally: dict) -> bool:
        max_rounds = task.max_review_rounds or self._default_max_rounds
        feedback = render.format_fail_feedback(tally["fail_feedback"])
        inspector_avg = tally.get("inspector_avg")
        if inspector_avg is not None:
            status = t("scheduler.inspector_avg_pass") if inspector_avg >= 0.85 else t("scheduler.inspector_avg_fail")
            feedback += t("scheduler.inspector_avg_line", avg=inspector_avg, status=status)
        if task.review_round >= max_rounds:
            await self._escalate(task, render.render_leader_escalation_rounds(task, feedback))
            round_key = (task.task_id, task.review_round)
            if task.assignee and round_key not in self._summary_requested:
                self._summary_requested.add(round_key)
                await self._send_as_leader(task.assignee, render.meta_rework_summary(task, max_rounds))
            self._dispatch_review_feedback(task, feedback)
            return False
        result = await task_manager.settle_review(task.task_id, "fail", feedback)
        if not result.ok:
            team_logger.debug("[scheduler] fail settle for %s did not apply: %s", task.task_id, result.reason)
            return False
        if task.assignee:
            await self._send_as_leader(task.assignee, render.meta_rework(task, max_rounds, feedback))
        self._dispatch_review_feedback(task, feedback)
        return True

    def _dispatch_review_feedback(self, task: Any, feedback: str) -> None:
        """Notify the mounted Core team-evolution Rail after a failed round."""

        normalized_feedback = str(feedback or "").strip()
        if not normalized_feedback:
            return
        rail = self._review_feedback_rail()
        handler = getattr(rail, "handle_review_feedback", None)
        if not callable(handler):
            return

        round_key = (str(task.task_id), int(task.review_round))
        if round_key in self._review_feedback_dispatched:
            return
        self._review_feedback_dispatched.add(round_key)
        payload = {
            "team_id": str(getattr(self._blueprint.spec, "team_name", "") or ""),
            "session_id": str(getattr(self._build_context, "session_id", "") or ""),
            "task_id": str(task.task_id),
            "review_round": int(task.review_round),
            "task_title": str(getattr(task, "title", "") or ""),
            "task_content": str(getattr(task, "content", "") or ""),
            "assignee": str(getattr(task, "assignee", "") or ""),
            "feedback": normalized_feedback,
        }
        background = asyncio.create_task(
            self._invoke_review_feedback_rail(handler, payload),
            name=f"review-feedback-{task.task_id}-{task.review_round}",
        )
        self._review_feedback_tasks.add(background)
        background.add_done_callback(self._review_feedback_tasks.discard)

    @staticmethod
    async def _invoke_review_feedback_rail(handler: Any, payload: dict[str, Any]) -> None:
        try:
            result = handler(payload)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            team_logger.error(
                "[scheduler] review feedback Rail failed: task=%s round=%s",
                payload.get("task_id"),
                payload.get("review_round"),
                exc_info=True,
            )

    async def _dispatch_team_review_feedback(self) -> None:
        """Finalize reviewer-feedback evolution after the board drains.

        Per-task feedback callbacks run in the background so they never delay
        task handoff. The terminal callback waits for those jobs before asking
        the mounted Rail to aggregate them, which prevents the last task's
        feedback from racing the team-level evolution pass.
        """
        if self._team_review_feedback_dispatched:
            return
        self._team_review_feedback_dispatched = True

        pending = list(self._review_feedback_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        rail = self._review_feedback_rail()
        callback = getattr(rail, "finalize_review_feedback", None)
        if not callable(callback):
            return
        payload = {
            "team_id": str(getattr(self._blueprint.spec, "team_name", "") or ""),
            "session_id": str(getattr(self._build_context, "session_id", "") or ""),
        }
        try:
            result = callback(payload)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            team_logger.error(
                "[scheduler] team review feedback Rail failed: team=%s",
                payload["team_id"],
                exc_info=True,
            )

    def _review_feedback_rail(self) -> Any | None:
        """Resolve the mounted team Rail using the normal harness lifecycle."""
        harness = getattr(self._host, "harness", None)
        find_rails = getattr(harness, "find_rails", None)
        if not callable(find_rails):
            return None

        from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillEvolutionRail

        team_rails = find_rails(TeamSkillEvolutionRail)
        rail = next(
            (candidate for candidate in team_rails if getattr(candidate, "review_feedback_evolution_enabled", False)),
            None,
        )
        if rail is None:
            return None
        creation_rails = find_rails(TeamSkillCreateRail)
        rail.bind_review_feedback_skill_create_rail(
            next(iter(creation_rails), None),
        )
        return rail

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

    async def _dispatch_to_reviewer(self, reviewer: str, task: Any) -> None:
        """Dispatch a review request by spawning a one-shot temp reviewer harness.

        The harness runs on the reviewer base spec (see
        :meth:`_spawn_temp_reviewer`) augmented with ``verify_task`` +
        ``view_task`` so it can inspect the deliverable and cast its vote.  The harness is built,
        run once, and disposed — a crash is logged and the next scheduler
        scan retries via ``_review_dispatched.discard()``.
        """
        team_logger.info("[scheduler] spawning temp harness", reviewer)
        asyncio.create_task(self._spawn_temp_reviewer(reviewer, task))

    async def _spawn_temp_reviewer(self, reviewer: str, task: Any) -> None:
        """Build a one-shot reviewer harness and run ``verify_task`` on it.

        The reviewer runs on ``spec.reviewer_agent`` when the host provides
        one, else on the teammate spec narrowed by the framework
        (:func:`build_reviewer_agent_spec`). It gets two extra team tools:
        ``verify_task`` to cast its vote and ``view_task`` as a fallback only,
        since the review request already carries what it needs. The harness
        is disposed immediately after ``run_once``, regardless of outcome; a
        crash is logged and retried on the next scan.

        For trajectory attribution the reviewer mounts the same observability
        rail pair as a team member and runs under its own execution subject per
        (task, reviewer, round, attempt), so each fresh-context run groups into
        a lane of its own instead of falling into the unassigned main group or
        sharing a lane with another task's same-named reviewer.
        """
        from openjiuwen.agent_teams.agent.team_agent import team_reviewer_execution_subject
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.harness.team_harness import TeamHarness
        from openjiuwen.agent_teams.rails.elements import observability_rail_specs
        from openjiuwen.agent_teams.schema.team import TeamRole
        from openjiuwen.agent_teams.tools.locales import make_translator
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
        from openjiuwen.agent_teams.tools.tool_task import VerifyTaskTool, ViewTaskToolV2
        from openjiuwen.core.session.agent_team import Session as TeamSession
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness.execution_subject import execution_subject_scope

        spec = self._blueprint.spec
        base_agent_spec = getattr(spec, "reviewer_agent", None)
        if base_agent_spec is None:
            agents = getattr(spec, "agents", None) or {}
            member_spec = agents.get("teammate") or agents.get("leader")
            if member_spec is None:
                team_logger.error("[scheduler] no base agent spec for temp reviewer")
                return
            base_agent_spec = build_reviewer_agent_spec(member_spec)

        backend = self._infra.team_backend
        task_manager = self._infra.task_manager
        if backend is None or task_manager is None:
            team_logger.error("[scheduler] missing backend/task_manager for temp reviewer")
            return

        # Build a reviewer-scoped TeamTaskManager so that ``verify_task``'s
        # identity guard (``member_name in task.reviewers()``) passes against
        # the reviewer name stored on the task row. ``view_task`` reads through
        # the same manager, so ``in_review`` lists this reviewer's queue.
        reviewer_tm = TeamTaskManager(
            team_name=backend.team_name,
            member_name=reviewer,
            db=backend.db,
            messager=self._infra.messager,
            dispatch_mode=self._blueprint.spec.dispatch_mode,
        )
        language = self._blueprint.language or "cn"
        # temp-reviewer tool descriptions resolve evolved values through the
        # team backend's cache (it delegates to the workspace manager — the
        # single source every consumer uses). ``None`` keeps the framework
        # default.
        cache = backend.workspace_cache
        tr = make_translator(language, ws_cache=cache)

        verify_tool = VerifyTaskTool(reviewer_tm, tr, desc_key="verify_task_scheduled")
        view_tool = ViewTaskToolV2(reviewer_tm, tr, desc_key="view_task_reviewer")

        member_name = reviewer
        harness = None
        try:
            # Resolve the reviewer's type and instruction from the task's
            # structured reviewer list so the correct prompt template is used.
            reviewer_type = "verifier"
            instruction = ""
            for detail in task.reviewer_details() if hasattr(task, "reviewer_details") else []:
                if detail.get("reviewer_id") == reviewer:
                    reviewer_type = detail.get("type", "verifier")
                    instruction = detail.get("instruction", "")
                    break
            template_name = _REVIEWER_TEMPLATE_MAP.get(reviewer_type, "reviewer_verifier")
            # Inspector loads its scoring dimensions from a shared template
            # when the leader did not provide one; verifier uses the per-task
            # ``instruction``. Challenger needs neither.
            if reviewer_type == "inspector" and not instruction:
                instruction = load_template("reviewer_dims_for_inspector", language).content
            system_prompt = load_template(template_name, language).content.format(
                reviewer=reviewer,
                instruction=instruction,
            )
            reviewer_spec = base_agent_spec.model_copy(
                update={
                    "system_prompt": system_prompt,
                    "tools": list(base_agent_spec.tools or []) + [verify_tool, view_tool],
                    "rails": list(base_agent_spec.rails or []) + observability_rail_specs(),
                }
            )
            reviewer_ctx = (
                self._build_context.derive(
                    member_name=member_name,
                    role=TeamRole.TEAMMATE.value,
                    language=language,
                )
                if self._build_context is not None
                else None
            )

            # Use the review request message as the prompt: template
            # rendered at delivery-time against the current task row.
            # The reviewer runs with a fresh context every round; its own
            # previous-round vote is the only carry-over it gets.
            previous_vote = await task_manager.get_previous_round_vote(task, reviewer)
            upstream, _downstream = await task_links(backend.db, task.task_id)
            review_prompt = await render.render_review_request_for_harness(
                task,
                language=language,
                reviewer=reviewer,
                loader=self._template_loader,
                previous_vote=previous_vote,
                upstream=upstream,
            )
            team_session_id = get_session_id() or ""
            # Retry transient model-call failures up to 3 times.
            # Each attempt builds a fresh harness and disposes the old
            # one — ``run_once`` tears down tools on every invocation.
            result = None
            for attempt in range(3):
                # A fresh card id per attempt: agent state is checkpointed
                # under (session id, card id), and the reviewer runs on the
                # team session (below), so a reused id would restore the
                # previous attempt's / round's context into a run that must
                # start clean.
                attempt_spec = reviewer_spec.model_copy(
                    update={
                        "card": AgentCard(
                            id=f"{backend.team_name}_{reviewer}_review_{uuid.uuid4().hex[:12]}",
                            name=reviewer,
                            description="scheduled-mode temp reviewer",
                        ),
                    }
                )
                harness = TeamHarness.build(
                    agent_spec=attempt_spec,
                    role=TeamRole.TEAMMATE,
                    member_name=member_name,
                    build_context=reviewer_ctx,
                )
                if attempt == 0:
                    team_logger.info(
                        "[reviewer_built] temp reviewer harness built for %s, task=%s",
                        reviewer,
                        task.task_id,
                    )
                # Run on a child of the team session: the observability rail
                # resolves the run root by session id, so a standalone session
                # (fresh random id) finds no root and opens no agent span —
                # leaving the reviewer's records without its subject.
                team_session = (
                    TeamSession(session_id=team_session_id, team_id=backend.team_name)
                    if team_session_id
                    else None
                )
                reviewer_subject = team_reviewer_execution_subject(
                    session_id=team_session_id,
                    team_name=backend.team_name,
                    task_id=task.task_id,
                    reviewer=reviewer,
                    review_round=task.review_round,
                    attempt=attempt,
                    assignee=task.assignee,
                )
                with execution_subject_scope(reviewer_subject):
                    result = await harness.run_once(review_prompt, team_session=team_session)
                output_str = str(result)
                if "181001" not in output_str:
                    break
                team_logger.warning(
                    "[reviewer_retry] reviewer %s task=%s attempt=%d/3: %s",
                    reviewer,
                    task.task_id,
                    attempt + 1,
                    output_str[:200],
                )
                try:
                    await harness.dispose()
                except Exception:
                    team_logger.debug("[scheduler] temp reviewer dispose failed for %s", reviewer)
                await asyncio.sleep(2 * attempt)
            team_logger.info(
                "[reviewer_finish] reviewer %s, task=%s, output=%s",
                reviewer,
                task.task_id,
                str(result)[:2000] if result else "",
            )
            # If all retries exhausted, let the next scan re-dispatch.
            if result is not None and "181001" in str(result):
                self._review_dispatched.discard((task.task_id, task.review_round))
        except Exception:
            team_logger.error(
                "[reviewer_fail] temp reviewer %s failed for task %s",
                reviewer,
                task.task_id,
                exc_info=True,
            )
            self._review_dispatched.discard((task.task_id, task.review_round))
        finally:
            if harness is not None:
                try:
                    await harness.dispose()
                except Exception:
                    team_logger.debug("[scheduler] temp reviewer dispose failed for %s", reviewer)

    async def _send_as_leader(self, member_name: str, meta: dict) -> bool:
        """Leader-identity mailbox handoff + idempotent lazy member startup.

        The row carries the delivery payload, not the text: ``content`` is
        empty and ``meta`` names the template plus the task it binds to, so the
        recipient's mailbox drain renders it against the task row as it stands
        *then* (F_63). The row lands first (durable — an offline member drains
        it on its first mailbox sweep), then the runtime is started best-effort
        via the same ``UNSTARTED -> STARTING`` CAS the send_message tool uses;
        an already-running member simply gets the MESSAGE wake. Per-recipient
        failures are logged and never abort the scan.

        Returns:
            True iff the mailbox row landed (the member startup is best-effort
            and does not affect the result).
        """
        message_manager = self._infra.message_manager
        if message_manager is None:
            return False
        try:
            message_id = await message_manager.send_message(content="", to_member_name=member_name, meta=meta)
        except Exception:
            team_logger.error("[scheduler] handoff to %s failed", member_name, exc_info=True)
            return False
        if not message_id:
            team_logger.error("[scheduler] handoff message to %s was not delivered", member_name)
        # The row is the handoff; startup is a separate best-effort step whose
        # failure must not make the caller believe the mail never landed (it
        # would re-send on the next scan).
        try:
            await self._host.auto_start_member(member_name)
        except Exception:
            team_logger.error("[scheduler] member startup after handoff to %s failed", member_name, exc_info=True)
        return bool(message_id)

    async def _escalate(self, task, content: str) -> None:
        """Inject an escalation into the leader once per (task, round)."""
        round_key = (task.task_id, task.review_round)
        if round_key in self._escalated:
            return
        self._escalated.add(round_key)
        team_logger.info(
            "[scheduler] escalating task %s round %s to the leader, message: %s",
            task.task_id,
            task.review_round,
            content,
        )
        await self._host.deliver_input(content, use_steer=False)

    async def _digest_task_done(self, task_manager, task: Any, *, verified: bool) -> None:
        """Terminal digest to the leader, once per task.

        Carries the task's completion summary (F_119), which stands in for the
        member's own completion report.

        Also the all-done fallback for leader-settled boards: the drained
        event a leader-local settle publishes is self-filtered off the bus,
        so a zero-remaining digest announces the wrap-up directly.
        """
        if task.task_id in self._digested_tasks:
            return
        self._digested_tasks.add(task.task_id)
        tasks = await task_manager.list_tasks()
        terminal = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
        remaining = sum(1 for row in tasks if row.status not in terminal)
        await self._host.deliver_input(
            render.render_leader_task_done(task, verified=verified),
            use_steer=False,
        )
        if remaining == 0 and tasks and not self._all_done_announced:
            self._all_done_announced = True
            await self._host.deliver_input(
                render.render_leader_all_done(len(tasks)),
                use_steer=False,
            )
        if remaining == 0 and tasks:
            await self._dispatch_team_review_feedback()

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
        if task is None:
            # The row was swept between the event and this digest: announce the
            # id alone rather than drop the completion.
            task = SimpleNamespace(task_id=task_id, title="", summary=None, note=None)
        await self._digest_task_done(task_manager, task, verified=verified)

    async def _announce_all_done(self, event: EventMessage) -> None:
        """Inject the final all-terminal digest into the leader, once."""
        payload = event.get_payload()
        if not self._all_done_announced:
            self._all_done_announced = True
            await self._host.deliver_input(
                render.render_leader_all_done(payload.task_count),
                use_steer=False,
            )
        await self._dispatch_team_review_feedback()
