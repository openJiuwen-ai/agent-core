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

import asyncio

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
    settle_review_tally,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger
from openjiuwen.agent_teams.prompts.loader import load_template


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
        self._all_done_announced = False
        self._review_feedback_dispatched: set[tuple[str, int]] = set()
        self._review_feedback_tasks: set[asyncio.Task[Any]] = set()
        self._team_review_feedback_dispatched = False

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
                    team_logger.info("[judge-pass] task=%s round=%d tally(pass=%d fail=%d total=%d)",
                        task.task_id, task.review_round,
                        tally["pass_count"], tally["fail_count"], tally["reviewer_count"])
                    acted = True
            elif verdict == VERDICT_FAIL:
                if await self._settle_fail_or_escalate(task_manager, task, tally):
                    team_logger.info("[judge-fail] task=%s round=%d tally(pass=%d fail=%d total=%d)",
                        task.task_id, task.review_round,
                        tally["pass_count"], tally["fail_count"], tally["reviewer_count"])
                    acted = True
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
            (
                candidate
                for candidate in team_rails
                if getattr(candidate, "review_feedback_evolution_enabled", False)
            ),
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

        The harness inherits the team's base agent spec (model, filesystem
        tools) augmented with ``verify_task`` + ``view_task`` so it can
        inspect the deliverable and cast its vote.  The harness is built,
        run once, and disposed — a crash is logged and the next scheduler
        scan retries via ``_review_dispatched.discard()``.
        """
        team_logger.info("[scheduler] spawning temp harness", reviewer)
        asyncio.create_task(self._spawn_temp_reviewer(reviewer, task))

    async def _spawn_temp_reviewer(self, reviewer: str, task: Any) -> None:
        """Build a one-shot reviewer harness and run ``verify_task`` on it.

        The reviewer inherits the team's base agent spec (model, filesystem
        tools, etc.) and gets two extra team tools — ``verify_task`` +
        ``view_task`` — so it can inspect the deliverable and cast a vote.
        The harness is disposed immediately after ``run_once``, regardless of
        outcome; a crash is logged and retried on the next scan.
        """
        from openjiuwen.agent_teams.harness.team_harness import TeamHarness
        from openjiuwen.agent_teams.tools.locales import make_translator
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
        from openjiuwen.agent_teams.tools.tool_task import VerifyTaskTool, ViewTaskToolV2
        from openjiuwen.agent_teams.schema.team import TeamRole

        spec = self._blueprint.spec
        agents = getattr(spec, "agents", None) or {}
        base_agent_spec = agents.get("teammate") or agents.get("leader")
        if base_agent_spec is None:
            team_logger.error("[scheduler] no base agent spec for temp reviewer")
            return

        backend = self._infra.team_backend
        task_manager = self._infra.task_manager
        if backend is None or task_manager is None:
            team_logger.error("[scheduler] missing backend/task_manager for temp reviewer")
            return

        # Build a reviewer-scoped TeamTaskManager so that ``verify_task``'s
        # identity guard (``member_name in task.reviewers()``) passes against
        # the reviewer name stored on the task row.
        reviewer_tm = TeamTaskManager(
            team_name=backend.team_name,
            member_name=reviewer,
            db=backend.db,
            messager=self._infra.messager,
            dispatch_mode=self._blueprint.spec.dispatch_mode,
        )
        language = self._blueprint.language or "cn"
        tr = make_translator(language)

        verify_tool = VerifyTaskTool(reviewer_tm, tr, desc_key="verify_task_scheduled")
        view_tool = ViewTaskToolV2(backend, tr)

        member_name = reviewer
        harness = None
        try:
            # Resolve the reviewer's type and instruction from the task's
            # structured reviewer list so the correct prompt template is used.
            reviewer_type = "verifier"
            instruction = ""
            for detail in (task.reviewer_details() if hasattr(task, 'reviewer_details') else []):
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
                }
            )
            reviewer_ctx = self._build_context.derive(
                member_name=member_name,
                role=TeamRole.TEAMMATE.value,
                language=language,
            ) if self._build_context is not None else None

            # Use the review request message as the prompt: template
            # rendered at delivery-time against the current task row.
            review_prompt = await render.render_review_request_for_harness(
                task, language=language, reviewer=reviewer,
            )
            # Retry transient model-call failures up to 3 times.
            # Each attempt builds a fresh harness and disposes the old
            # one — ``run_once`` tears down tools on every invocation.
            result = None
            for attempt in range(3):
                harness = TeamHarness.build(
                    agent_spec=reviewer_spec,
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
                result = await harness.run_once(review_prompt)
                output_str = str(result)
                if "181001" not in output_str:
                    break
                team_logger.warning(
                    "[reviewer_retry] reviewer %s task=%s attempt=%d/3: %s",
                    reviewer, task.task_id, attempt + 1, output_str[:200],
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
        team_logger.info(
            "[scheduler] escalating task %s round %s to the leader, message: %s",
            task.task_id, task.review_round, content,
        )
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
        title = task.title if task is not None else ""
        await self._digest_task_done(task_manager, task_id, title, verified=verified)

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
