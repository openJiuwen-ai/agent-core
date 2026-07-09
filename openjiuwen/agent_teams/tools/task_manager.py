# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Task Manager Module

This module provides task management functionality for agent teams.
"""

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from typing import (
    Any,
    List,
    Optional,
)

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskCancelledEvent,
    TaskClaimedEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskListDrainedEvent,
    TaskPlanRequestEvent,
    TaskPlanResponseEvent,
    TaskReleasedEvent,
    TaskRevisionRequestedEvent,
    TaskRevokedEvent,
    TaskStartedEvent,
    TaskSubmittedForReviewEvent,
    TaskUnblockedEvent,
    TaskUpdatedEvent,
    TaskVerifiedEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.schema.status import (
    TASK_TRANSITIONS,
    MemberMode,
    TaskStatus,
    is_valid_transition,
)
from openjiuwen.agent_teams.schema.task import (
    NewTaskSpec,
    TaskCreateResult,
    TaskDetail,
    TaskGraphResult,
    TaskGraphSpec,
    TaskListResult,
    TaskOpResult,
    TaskSummary,
)
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database import (
    TeamDatabase,
    TeamTaskBase,
    TeamTaskDependencyBase,
)
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.core.common.logging import team_logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_token(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    normalized = normalized.strip("._-")
    return normalized[:96] or fallback


def _json_read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        team_logger.warning("Failed to read team plan json %s: %s", path, exc)
        return {}


def _json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TeamTaskManager:
    """Manager for team tasks

    This class provides methods to add, claim, complete, and manage tasks
    within a team.

    Attributes:
        db: Team database instance
        team_name: Team identifier
        member_name: Current member identifier
        messager: Messager instance for event publishing
    """

    def __init__(
        self,
        team_name: str,
        member_name: str,
        db: TeamDatabase,
        messager: Messager,
        *,
        plans_dir: str | Path | None = None,
        team_plan_id: str | None = None,
        leader_member_name: str | None = None,
    ):
        """Initialize task manager.

        Args:
            db: Team database instance.
            team_name: Team identifier.
            member_name: Current member identifier.
            messager: Messager instance for event publishing.
            plans_dir: Directory that stores plan-mode artifacts.
            team_plan_id: Current team-level plan identifier.
            leader_member_name: Leader member name used for member plan
                review notifications. Falls back to the team row when
                omitted.
        """
        self.db = db
        self.team_name = team_name
        self.member_name = member_name
        self.messager = messager
        self.plans_dir = Path(plans_dir) if plans_dir else team_home(team_name) / "team-workspace" / "plans"
        self.team_plan_id = _safe_token(team_plan_id or get_session_id() or team_name, "team_plan")
        self.leader_member_name = str(leader_member_name or "").strip()

    def configure_plan_storage(
        self,
        *,
        plans_dir: str | Path | None = None,
        team_plan_id: str | None = None,
    ) -> None:
        """Configure where member plan files and approvals are persisted."""
        if plans_dir is not None:
            self.plans_dir = Path(plans_dir)
        if team_plan_id is not None:
            self.team_plan_id = _safe_token(team_plan_id, "team_plan")

    async def add_graph(self, specs: list[TaskGraphSpec]) -> TaskGraphResult:
        """Create a batch of tasks and their dependency edges atomically.

        The whole batch flows through a single ``mutate_dependency_graph``
        call, so ``depends_on`` edges may reference tasks created later in
        the same batch (forward references) and the batch either fully
        lands or fully rolls back with the real failure reason.

        ``depended_by`` edges wire *existing* tasks to depend on a new
        task (insert into an existing chain). The tool boundary rejects
        in-batch ``depended_by`` targets — see ``TaskGraphSpec``.

        ``spec.assignee`` pre-assigns a task (scheduled dispatch) inside the
        same transaction. Every task is seeded ``PENDING`` regardless of
        assignee — a scheduled task rests at ``PENDING`` *with an assignee*
        ("assigned, not yet started") until the scheduler calls
        ``start_task`` to move it to ``IN_PROGRESS``. Assignment and execution
        are separate events, so seeding an execution state here would conflate
        them. A task with unresolved ``depends_on`` is flipped to ``BLOCKED``
        by the refresh pass and carries its assignee through. ``depended_by``
        gates the tasks pointing at this one, not this task, so it is not
        consulted here.

        Args:
            specs: Task specs to create; missing ``task_id`` values are
                auto-generated.

        Returns:
            ``TaskGraphResult`` — created tasks with post-refresh statuses
            on success, the graph-mutation failure reason otherwise.
        """
        new_tasks: list[NewTaskSpec] = []
        edges: list[tuple[str, str]] = []
        for spec in specs:
            task_id = spec.task_id or str(uuid.uuid4())
            # Always seed PENDING. A scheduled pre-assigned task rests at
            # PENDING(assignee) until the scheduler starts it; the refresh
            # pass flips it to BLOCKED if it has unresolved dependencies.
            new_tasks.append(
                NewTaskSpec(
                    task_id=task_id,
                    title=spec.title,
                    content=spec.content,
                    initial_status=TaskStatus.PENDING.value,
                    assignee=spec.assignee,
                    reviewer=json.dumps(list(spec.reviewer)) if spec.reviewer else None,
                )
            )
            edges.extend((task_id, dep_id) for dep_id in spec.depends_on)
            edges.extend((dependent_id, task_id) for dependent_id in spec.depended_by)

        mutation = await self.db.task.mutate_dependency_graph(
            team_name=self.team_name,
            new_tasks=new_tasks,
            add_edges=edges,
        )
        if not mutation.ok:
            return TaskGraphResult.fail(mutation.reason)

        # The refresh pass may have flipped PENDING -> BLOCKED for tasks
        # with unresolved dependencies; reflect that in the returned tasks
        # and the task-created events.
        status_by_id = {t.task_id: t.status for t in mutation.refreshed_tasks}
        created: list[TeamTaskBase] = []
        for node in new_tasks:
            status = status_by_id.get(node.task_id, node.initial_status)
            await self._publish_task_created(node.task_id, status)
            created.append(
                TeamTaskBase(
                    task_id=node.task_id,
                    team_name=self.team_name,
                    title=node.title,
                    content=node.content,
                    status=status,
                    assignee=node.assignee,
                )
            )
        team_logger.debug(f"Added {len(created)} task(s) with {len(edges)} dependency edge(s)")
        return TaskGraphResult.success(created)

    async def add(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
    ) -> TaskCreateResult:
        """Add a single task to the team.

        Thin wrapper over ``add_graph`` for single-task callers (external
        operator client, internal seeding).

        Args:
            title: Task title.
            content: Task content.
            task_id: Optional custom task ID (auto-generated if not provided).
            dependencies: List of task IDs this task depends on.

        Returns:
            ``TaskCreateResult`` — on success ``result.task`` carries the
            created task (and attribute access like ``result.task_id``
            / ``result.title`` transparently delegates to it); on
            failure ``result.reason`` holds the specific cause.
        """
        result = await self.add_graph(
            [
                TaskGraphSpec(
                    title=title,
                    content=content,
                    task_id=task_id,
                    depends_on=tuple(dependencies or ()),
                )
            ]
        )
        if not result.ok:
            return TaskCreateResult.fail(f"Failed to create task {task_id or title!r}: {result.reason}")
        return TaskCreateResult.success(result.tasks[0])

    async def _publish_task_created(self, task_id: str, status: str) -> None:
        """Publish a task-created event; log-and-continue on failure."""
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskCreatedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        status=status,
                    )
                ),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

    async def list_tasks_with_deps(self, status: Optional[str] = None) -> TaskListResult:
        """List tasks with blocked_by info (summary view, no content).

        Returns a lightweight task list where each entry includes unresolved
        dependency IDs. Suitable for list/claimable actions.

        Args:
            status: Optional status filter.

        Returns:
            TaskListResult containing task summaries with dependency info.
        """
        # Two queries, independent of task count: all tasks + all dependency
        # edges for the team, grouped in memory. Replaces the previous N+1
        # (one get_dependencies per task) that turned a large board into
        # hundreds of per-task queries / connection checkouts.
        tasks = await self.list_tasks(status=status)
        all_deps = await self.db.task.get_team_dependencies(self.team_name)
        unresolved_by_task: dict[str, list[str]] = defaultdict(list)
        for dep in all_deps:
            if not dep.resolved:
                unresolved_by_task[dep.task_id].append(dep.depends_on_task_id)
        summaries = [
            TaskSummary(
                task_id=task.task_id,
                title=task.title,
                status=task.status,
                assignee=task.assignee,
                blocked_by=unresolved_by_task.get(task.task_id, []),
                updated_at=task.updated_at,
            )
            for task in tasks
        ]
        return TaskListResult(tasks=summaries, count=len(summaries))

    async def get_task_detail(self, task_id: str) -> Optional[TaskDetail]:
        """Get single task with full detail including dependency info.

        Returns complete task information with both upstream (blocked_by)
        and downstream (blocks) dependency relationships.

        Args:
            task_id: Task identifier.

        Returns:
            TaskDetail if found, None otherwise.
        """
        task = await self.get(task_id=task_id)
        if not task:
            return None

        deps = await self.get_dependencies(task_id)
        blocked_by = [d.depends_on_task_id for d in deps if not d.resolved]

        blocks = await self.db.task.get_dependent_task_ids(task_id)

        return TaskDetail(
            task_id=task.task_id,
            title=task.title,
            content=task.content,
            status=task.status,
            assignee=task.assignee,
            reviewer=task.reviewers(),
            blocked_by=blocked_by,
            blocks=blocks,
            updated_at=task.updated_at,
        )

    async def list_review_tasks(self, reviewer_name: str) -> TaskListResult:
        """List IN_REVIEW tasks awaiting ``reviewer_name`` (verify-gate view).

        Backs ``view_task(action=in_review)``: the tasks a reviewer is on the
        hook to verify. Summary shape mirrors the list view; dependency info is
        omitted (a task under review is past its prerequisites).
        """
        tasks = await self.get_review_tasks(reviewer_name)
        summaries = [
            TaskSummary(
                task_id=task.task_id,
                title=task.title,
                status=task.status,
                assignee=task.assignee,
                blocked_by=[],
                updated_at=task.updated_at,
            )
            for task in tasks
        ]
        return TaskListResult(tasks=summaries, count=len(summaries))

    async def get(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get a task by ID

        Args:
            task_id: Task identifier

        Returns:
            TeamTask object if found, None otherwise
        """
        return await self.db.task.get_task(task_id)

    async def assign(self, task_id: str, assignee: str) -> TaskOpResult:
        """Assign a task to a member (Leader only).

        Atomically sets the assignee and enters the member's entry gate,
        symmetric with a member self-serve: a ``BUILD_MODE`` assignee starts
        executing (``IN_PROGRESS``), a ``PLAN_MODE`` assignee enters the plan
        gate (``PLANNING``) and must submit a plan for approval before
        executing. Idempotent when the task is already held by ``assignee`` in
        that gate. Fails when the task is currently held by a different member;
        the caller must reset the task first (see ``reset``) for true
        reassignment. Sends a notification message to the assigned member's
        mailbox on success.

        Args:
            task_id: Task identifier.
            assignee: Member ID to assign.

        Returns:
            ``TaskOpResult.success()`` on assign (or idempotent re-assign);
            ``TaskOpResult.fail(reason)`` with the specific failure cause
            otherwise.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        # Validate the assignee is a real team member. The DB column has no
        # FK to team_member, so a typo here would silently leave the task
        # bound to a name nobody serves; surface it at this layer instead.
        member = await self.db.member.get_member(assignee, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {assignee} not found in team {self.team_name}")

        # Entry gate mirrors a member self-serve: plan-mode assignees land in
        # the plan gate (PLANNING) and must get approval; build-mode assignees
        # start executing (IN_PROGRESS).
        entry_status = (
            TaskStatus.PLANNING if member.mode == MemberMode.PLAN_MODE.value else TaskStatus.IN_PROGRESS
        )

        # Idempotent re-assign: same member, already at its entry gate -> no-op.
        if task.assignee == assignee and task.status == entry_status.value:
            team_logger.debug(f"Task {task_id} already assigned to {assignee}; no-op")
            return TaskOpResult.success()

        if task.assignee and task.assignee != assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}; reset the task before reassigning to {assignee}"
            )

        success = await self.db.task.claim_task(task_id, assignee, to_status=entry_status)
        if not success:
            return TaskOpResult.fail(
                f"Database rejected assign for task {task_id} (invalid state transition from {task.status})"
            )

        await self.messager.publish(
            topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
            message=EventMessage.from_event(
                TaskClaimedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    member_name=assignee,
                )
            ),
        )
        team_logger.info(f"Task {task_id} assigned to {assignee}, notification sent")
        return TaskOpResult.success()

    async def add_dependencies(self, task_id: str, depends_on_ids: List[str]) -> TaskOpResult:
        """Add dependencies to an existing task atomically.

        Routes through ``mutate_dependency_graph`` so the operation
        carries the same guarantees as a graph-shaping create:
        - Cycle detection runs against the post-mutation graph; a
          rejected mutation surfaces the cycle path in the failure
          reason.
        - BLOCKED/PENDING is refreshed in the same transaction as the
          edge writes, so the on-disk state is always consistent with
          the dependency graph.

        Args:
            task_id: Task to add dependencies to.
            depends_on_ids: Task IDs that this task should depend on.

        Returns:
            ``TaskOpResult`` carrying the mutation reason on failure.
        """
        if not depends_on_ids:
            return TaskOpResult.success()

        mutation = await self.db.task.mutate_dependency_graph(
            team_name=self.team_name,
            add_edges=[(task_id, dep_id) for dep_id in depends_on_ids],
        )
        if not mutation.ok:
            return TaskOpResult.fail(mutation.reason)
        return TaskOpResult.success()

    async def claim(self, task_id: str) -> TaskOpResult:
        """Claim a task for the current member.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` carrying the specific failure reason on error
            so the caller (typically a team tool) can pass it through to
            the LLM instead of dropping it to the log sink.
        """
        member_name = self.member_name
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        member = await self.db.member.get_member(member_name, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {member_name} not found in team {self.team_name}")
        if member.mode == MemberMode.PLAN_MODE.value:
            return TaskOpResult.fail(
                "PLAN_MODE members must call submit_plan first; "
                "leader approval moves the task from planning to in_progress"
            )

        # Idempotent re-claim: if the caller already owns this task, succeed silently.
        if task.assignee == member_name and task.status == TaskStatus.IN_PROGRESS.value:
            team_logger.debug(f"Task {task_id} already claimed by {member_name}; no-op")
            return TaskOpResult.success()

        # Claim conflict must be reported before the state-transition check —
        # otherwise a task held by someone else surfaces as the misleading
        # "invalid in_progress → in_progress transition" error.
        if task.assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}, {member_name} cannot claim it"
            )

        # Validate state transition (blocks e.g. COMPLETED/CANCELLED/BLOCKED claims).
        if not is_valid_transition(
            TaskStatus(task.status),
            TaskStatus.IN_PROGRESS,
            TASK_TRANSITIONS,
        ):
            return TaskOpResult.fail(
                f"Task {task_id} cannot be claimed from status '{task.status}' (only pending tasks are claimable)"
            )

        # Claim task
        success = await self.db.task.claim_task(task_id, member_name)
        if not success:
            return TaskOpResult.fail(f"Database rejected claim for task {task_id} (likely a concurrent claim race)")

        team_logger.info(f"Task {task_id} claimed by member {member_name}")

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskClaimedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        member_name=member_name,
                    )
                ),
            )
            team_logger.debug(f"Task claimed event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task claimed event for {task_id}: {e}")

        return TaskOpResult.success()

    async def complete(self, task_id: str) -> TaskOpResult:
        """Complete a task.

        Updates task status to 'completed' and unblocks dependent tasks.
        This operation is atomic to prevent race conditions.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        # Get member to check mode (drives the plan-index write below).
        member = await self.db.member.get_member(self.member_name, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {self.member_name} not found in team {self.team_name}")

        # Verify gate: when the task carries reviewers, "done" means "ready for
        # review" — route IN_PROGRESS -> IN_REVIEW instead of completing. The
        # member's mental model is unchanged ("I finished"); whether it enters
        # review is driven by the task's reviewer list, not the member.
        gated = await self.db.task.get_task(task_id)
        if gated is not None and gated.reviewers():
            return await self._submit_for_review(gated)

        # No plan-mode-specific gate is needed: the transition table forbids
        # PLANNING -> COMPLETED, so a plan-mode member cannot complete a task
        # that has not been approved (PLANNING -> IN_PROGRESS). Completion is
        # uniformly valid only from IN_PROGRESS.

        # Complete task atomically - this handles state validation, dependency resolution,
        # and unblocking dependent tasks in a single transaction to prevent race conditions
        result = await self.db.task.complete_task(task_id)
        if not result:
            current = await self.db.task.get_task(task_id)
            if current is None:
                return TaskOpResult.fail(f"Task {task_id} not found")
            return TaskOpResult.fail(
                f"Task {task_id} cannot be completed from status '{current.status}' "
                f"(must be in_progress)"
            )

        # Extract completed task info and unblocked tasks
        completed_task = result.get("task")
        unblocked_tasks = result.get("unblocked_tasks", [])

        team_logger.info(f"Task {task_id} completed")

        await self._publish_task_event(
            TaskCompletedEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=completed_task.assignee,
            ),
            error_label=f"Task completed event for {task_id}",
        )
        if member.mode == MemberMode.PLAN_MODE.value:
            plan_index = self._read_task_plan_index(task_id)
            latest_plan_id = str(plan_index.get("latest_plan_id") or "")
            self._write_task_plan_index(
                task_id,
                {
                    "task_id": task_id,
                    "plan_id": latest_plan_id,
                    "team_plan_id": self.team_plan_id,
                    "member_name": completed_task.assignee,
                    "status": TaskStatus.COMPLETED.value,
                    "completed_at": _now_iso(),
                    "updated_at": _now_iso(),
                },
            )
        await self._publish_unblocked_events(unblocked_tasks)
        await self._maybe_publish_task_list_drained()
        return TaskOpResult.success()

    async def _submit_for_review(self, task) -> TaskOpResult:
        """Route a completed-by-author task into the verify gate (IN_REVIEW).

        Called from ``complete`` when the task carries reviewers. Plain
        IN_PROGRESS -> IN_REVIEW flip (no dependency resolution — the task is
        not done yet); the author stays assigned. Publishes
        ``TASK_SUBMITTED_FOR_REVIEW`` carrying the reviewer list for the
        framework to dispatch/notify.
        """
        submitted = await self.db.task.submit_for_review(task.task_id)
        if not submitted:
            return TaskOpResult.fail(
                f"Task {task.task_id} cannot be submitted for review from status "
                f"'{task.status}' (must be in_progress)"
            )
        team_logger.info("Task %s submitted for review", task.task_id)
        await self._publish_task_event(
            TaskSubmittedForReviewEvent(
                team_name=self.team_name,
                task_id=task.task_id,
                member_name=task.assignee,
                reviewer=task.reviewers(),
            ),
            error_label=f"Task submitted-for-review event for {task.task_id}",
        )
        return TaskOpResult.success()

    async def verify_task(self, task_id: str, decision: str, feedback: str = "") -> TaskOpResult:
        """Reviewer verdict on a task in the verify gate (reviewer only).

        ``decision`` is ``pass`` (IN_REVIEW -> COMPLETED, unblocks dependents)
        or ``fail`` (IN_REVIEW -> IN_PROGRESS, rework loop with ``feedback``
        directed at the still-assigned author). Enforces that the task is in
        ``IN_REVIEW`` and the caller is one of its reviewers (not the author).

        Args:
            task_id: Task under review.
            decision: ``pass`` or ``fail``.
            feedback: Reviewer guidance (carried to the author on ``fail``).

        Returns:
            ``TaskOpResult`` carrying the failure reason on error.
        """
        normalized = decision.strip().lower()
        if normalized not in ("pass", "fail"):
            return TaskOpResult.fail(f"verify_task decision must be 'pass' or 'fail', got '{decision}'")

        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")
        if task.status != TaskStatus.IN_REVIEW.value:
            return TaskOpResult.fail(
                f"Task {task_id} is not under review (status '{task.status}'); nothing to verify"
            )
        reviewers = task.reviewers()
        if self.member_name not in reviewers:
            return TaskOpResult.fail(
                f"{self.member_name} is not a reviewer of task {task_id}; cannot verify it"
            )
        if self.member_name == task.assignee:
            return TaskOpResult.fail(f"{self.member_name} cannot verify their own task {task_id}")

        if normalized == "pass":
            return await self._verify_pass(task)
        return await self._verify_fail(task, feedback)

    async def _verify_pass(self, task) -> TaskOpResult:
        """Pass verdict: IN_REVIEW -> COMPLETED, unblocking dependents.

        Reuses ``complete_task`` (which resolves dependencies + cascades) —
        ``IN_REVIEW -> COMPLETED`` is a valid terminal transition, and the
        source was already guarded as IN_REVIEW by ``verify_task``.
        """
        result = await self.db.task.complete_task(task.task_id)
        if not result:
            return TaskOpResult.fail(f"Task {task.task_id} could not be completed from review")
        completed_task = result.get("task")
        unblocked_tasks = result.get("unblocked_tasks", [])
        team_logger.info("Task %s verified (passed) and completed", task.task_id)
        await self._publish_task_event(
            TaskVerifiedEvent(
                team_name=self.team_name,
                task_id=task.task_id,
                member_name=completed_task.assignee,
            ),
            error_label=f"Task verified event for {task.task_id}",
        )
        await self._publish_unblocked_events(unblocked_tasks)
        await self._maybe_publish_task_list_drained()
        return TaskOpResult.success()

    async def _verify_fail(self, task, feedback: str) -> TaskOpResult:
        """Fail verdict: IN_REVIEW -> IN_PROGRESS, rework loop to the author."""
        revised = await self.db.task.revise_task(task.task_id)
        if not revised:
            return TaskOpResult.fail(f"Task {task.task_id} could not be sent back for revision")
        team_logger.info("Task %s verification failed; sent back to %s for revision", task.task_id, task.assignee)
        await self._publish_task_event(
            TaskRevisionRequestedEvent(
                team_name=self.team_name,
                task_id=task.task_id,
                member_name=task.assignee,
                feedback=feedback,
            ),
            error_label=f"Task revision-requested event for {task.task_id}",
        )
        return TaskOpResult.success()

    async def get_review_tasks(self, reviewer_name: str) -> List[TeamTaskBase]:
        """Return IN_REVIEW tasks whose reviewer list contains ``reviewer_name``.

        Fetches the status-indexed IN_REVIEW rows and filters the reviewer
        membership in memory — v1 verification is team-scale, so a normalized
        reviewer join table is premature (see F_59). The author of a task is
        never returned to itself as a review target.
        """
        in_review = await self.list_tasks(status=TaskStatus.IN_REVIEW.value)
        return [
            task
            for task in in_review
            if reviewer_name in task.reviewers() and task.assignee != reviewer_name
        ]

    def _task_plan_dir(self, task_id: str) -> Path:
        return self.plans_dir / self.team_plan_id / "tasks" / _safe_token(task_id, "task")

    @staticmethod
    def _new_plan_id() -> str:
        return uuid.uuid4().hex

    def _task_plan_path(self, task_id: str, plan_id: str) -> Path:
        safe_plan_id = _safe_token(plan_id, "plan")
        return self._task_plan_dir(task_id) / "plans" / f"{safe_plan_id}.md"

    @staticmethod
    def _resolve_submitted_plan_path(plan_path: str) -> Path:
        raw_path = str(plan_path or "").strip()
        if not raw_path:
            raise ValueError("submit_plan requires plan_path")

        submitted_path = Path(raw_path).expanduser()
        if not submitted_path.is_absolute():
            try:
                from openjiuwen.core.sys_operation.cwd import get_cwd

                base_dir = Path(get_cwd()).expanduser()
            except Exception:
                base_dir = Path.cwd()
            submitted_path = base_dir / submitted_path
        submitted_path = submitted_path.resolve()
        if not submitted_path.is_file():
            raise FileNotFoundError(f"submit_plan plan_path does not exist or is not a file: {submitted_path}")
        return submitted_path

    async def _resolve_leader_member_name(self) -> str:
        if self.leader_member_name:
            return self.leader_member_name
        team = await self.db.team.get_team(self.team_name)
        leader_member_name = str(getattr(team, "leader_member_name", "") or "").strip() if team else ""
        self.leader_member_name = leader_member_name
        return leader_member_name

    @staticmethod
    def _render_plan_review_message(plan_record: dict[str, Any]) -> str:
        lines = [
            "Member task plan approval request.",
            f"Member: {plan_record.get('member_name')}",
            f"Task ID: {plan_record.get('task_id')}",
            f"Plan ID: {plan_record.get('plan_id')}",
            f"Plan file: {plan_record.get('member_plan_md')}",
        ]
        tool_call_id = str(plan_record.get("tool_call_id") or "").strip()
        if tool_call_id:
            lines.append(f"Tool Call ID: {tool_call_id}")
        lines.extend(
            [
                "",
                "Please review the plan file and call approve_plan with this plan_id.",
            ]
        )
        return "\n".join(lines)

    async def _notify_leader_of_plan(self, plan_record: dict[str, Any]) -> str | None:
        leader_member_name = await self._resolve_leader_member_name()
        if not leader_member_name:
            team_logger.warning(
                "submit_plan could not notify leader: team={} task_id={} plan_id={} has no leader_member_name",
                self.team_name,
                plan_record.get("task_id"),
                plan_record.get("plan_id"),
            )
            return None
        if leader_member_name == self.member_name:
            return None

        try:
            message_manager = TeamMessageManager(
                team_name=self.team_name,
                member_name=self.member_name,
                db=self.db,
                messager=self.messager,
            )
            return await message_manager.send_message(
                content=self._render_plan_review_message(plan_record),
                to_member_name=leader_member_name,
            )
        except Exception as exc:
            team_logger.warning(
                "submit_plan failed to notify leader {} for task {} plan {}: {}",
                leader_member_name,
                plan_record.get("task_id"),
                plan_record.get("plan_id"),
                exc,
            )
            return None

    def _write_task_plan_index(self, task_id: str, update: dict[str, Any]) -> None:
        index_path = self.plans_dir / "index.json"
        index = _json_read(index_path)
        tasks = index.get("tasks") if isinstance(index.get("tasks"), dict) else {}
        current = tasks.get(task_id) if isinstance(tasks.get(task_id), dict) else {}
        plan_id = str(update.get("plan_id") or "").strip()
        if plan_id:
            known_ids = current.get("plan_ids")
            if not isinstance(known_ids, list):
                known_ids = []
            if plan_id not in known_ids:
                known_ids.append(plan_id)
            current = {**current, "plan_ids": known_ids}
        task_record = {**current, **update}
        tasks[task_id] = task_record

        task_plans = index.get("task_plans") if isinstance(index.get("task_plans"), dict) else {}
        if plan_id:
            current_plan = task_plans.get(plan_id)
            if not isinstance(current_plan, dict):
                current_plan = {}
            task_plans[plan_id] = {**current_plan, **update, "task_id": task_id}

        index.update(
            {
                "team_name": self.team_name,
                "team_plan_id": self.team_plan_id,
                "plans_dir": str(self.plans_dir),
                "updated_at": _now_iso(),
                "tasks": tasks,
                "task_plans": task_plans,
            }
        )
        _json_write(index_path, index)

    def _read_task_plan_index(self, task_id: str) -> dict[str, Any]:
        index = _json_read(self.plans_dir / "index.json")
        tasks = index.get("tasks") if isinstance(index.get("tasks"), dict) else {}
        current = tasks.get(task_id) if isinstance(tasks.get(task_id), dict) else {}
        return dict(current)

    def _read_plan_index(self, plan_id: str) -> dict[str, Any]:
        index = _json_read(self.plans_dir / "index.json")
        task_plans = index.get("task_plans") if isinstance(index.get("task_plans"), dict) else {}
        current = task_plans.get(plan_id) if isinstance(task_plans.get(plan_id), dict) else {}
        return dict(current)

    def get_plan_record(self, plan_id: str) -> dict[str, Any]:
        """Return persisted metadata for one member plan submission."""
        return self._read_plan_index(plan_id)

    async def submit_plan(
        self,
        task_id: str,
        plan_path: str,
        plan_id: str | None = None,
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        """Snapshot a member execution plan file and reserve the task as PLANNING."""
        member_name = self.member_name
        member = await self.db.member.get_member(member_name, self.team_name)
        if not member:
            return {"success": False, "task_id": task_id, "message": f"Member {member_name} not found"}
        if member.mode != MemberMode.PLAN_MODE.value:
            return {"success": False, "task_id": task_id, "message": "submit_plan is only for PLAN_MODE"}

        task = await self.get(task_id)
        if not task:
            return {"success": False, "task_id": task_id, "message": f"Task {task_id} not found"}
        if task.assignee and task.assignee != member_name:
            return {
                "success": False,
                "task_id": task_id,
                "message": f"Task {task_id} is assigned to {task.assignee}, not {member_name}",
            }
        # PENDING (first submission, reserved into PLANNING below), PLANNING
        # (re-submit after a reject, stays PLANNING), and IN_PROGRESS (scheduled
        # owner submits a plan against an already-started task without
        # re-claiming) are the states a member plan can be recorded from.
        if task.status not in (
            TaskStatus.PENDING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
        ):
            return {
                "success": False,
                "task_id": task_id,
                "status": task.status,
                "message": f"Task {task_id} cannot accept a member plan from status '{task.status}'",
            }

        plan_id = _safe_token(plan_id or self._new_plan_id(), "plan")
        if self._read_plan_index(plan_id):
            return {
                "success": False,
                "task_id": task_id,
                "plan_id": plan_id,
                "message": f"Plan ID {plan_id} already exists; use a new plan_id",
            }
        try:
            submitted_plan_path = self._resolve_submitted_plan_path(plan_path)
        except (FileNotFoundError, ValueError) as exc:
            return {
                "success": False,
                "task_id": task_id,
                "plan_id": plan_id,
                "message": str(exc),
            }

        if task.status == TaskStatus.PENDING.value:
            # A PENDING task with no owner is reserved into the plan gate
            # (PENDING -> PLANNING) by the first submission. Scheduled tasks
            # never reach here as PENDING with a free assignee.
            claimed = await self.db.task.claim_task(task_id, member_name, to_status=TaskStatus.PLANNING)
            if not claimed:
                return {"success": False, "task_id": task_id, "message": "Failed to reserve task for planning"}
            active_status = TaskStatus.PLANNING.value
        elif task.assignee != member_name:
            return {
                "success": False,
                "task_id": task_id,
                "message": f"Task {task_id} is assigned to {task.assignee}, not {member_name}",
            }
        else:
            # Already owned by this member: PLANNING (re-plan after reject) or
            # IN_PROGRESS (scheduled). Planning records against it without a
            # status change.
            active_status = task.status

        member_plan_path = self._task_plan_path(task_id, plan_id)
        member_plan_path.parent.mkdir(parents=True, exist_ok=True)
        if submitted_plan_path != member_plan_path.resolve():
            shutil.copyfile(submitted_plan_path, member_plan_path)
        plan_record = {
            "task_id": task_id,
            "plan_id": plan_id,
            "team_plan_id": self.team_plan_id,
            "latest_plan_id": plan_id,
            "member_name": member_name,
            "status": active_status,
            "member_plan_md": str(member_plan_path),
            "source_plan_path": str(submitted_plan_path),
            "tool_call_id": tool_call_id,
            "decision": "pending",
            "submitted_at": _now_iso(),
        }
        self._write_task_plan_index(task_id, {**plan_record, "updated_at": _now_iso()})

        await self._publish_task_event(
            TaskPlanRequestEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=member_name,
                status=active_status,
                plan_id=plan_id,
                member_plan_md=str(member_plan_path),
                tool_call_id=tool_call_id,
            ),
            error_label=f"Task plan request event for {task_id}",
        )
        leader_message_id = await self._notify_leader_of_plan(plan_record)
        if leader_message_id:
            self._write_task_plan_index(
                task_id,
                {
                    "plan_id": plan_id,
                    "leader_message_id": leader_message_id,
                    "updated_at": _now_iso(),
                },
            )
        return {
            "success": True,
            "task_id": task_id,
            "plan_id": plan_id,
            "status": active_status,
            "member_plan_md": str(member_plan_path),
            "leader_message_id": leader_message_id,
            "message": "Member plan submitted. Wait for leader approval before execution.",
        }

    async def cancel(self, task_id: str) -> Optional[TeamTaskBase]:
        """Cancel a task and notify any tasks unblocked as a side effect.

        Args:
            task_id: Task identifier.

        Returns:
            The cancelled task, or ``None`` if the task is missing or
            the transition is invalid.
        """
        result = await self.db.task.cancel_task(task_id)
        if result is None:
            return None

        task = result["task"]
        unblocked_tasks = result.get("unblocked_tasks") or []
        team_logger.info(f"Task {task_id} cancelled")

        # Carry the (former) assignee so its dispatcher steers it off the
        # cancelled task via on_task_cancelled, instead of a member-wide cancel.
        await self._publish_task_event(
            TaskCancelledEvent(team_name=self.team_name, task_id=task_id, member_name=task.assignee),
            error_label=f"Task cancelled event for {task_id}",
        )
        await self._publish_unblocked_events(unblocked_tasks)
        await self._maybe_publish_task_list_drained()
        return task

    async def cancel_all_tasks(
        self,
        skip_assignees: Optional[set[str]] = None,
    ) -> List[TeamTaskBase]:
        """Cancel every active task in the team in a single transaction.

        Args:
            skip_assignees: Member names whose claimed tasks must be
                preserved. Any task assigned to one of these members is
                left untouched.

        Returns:
            The cancelled tasks. Side effect: publishes a CANCELLED
            event for each cancelled task and an UNBLOCKED event for
            each task that flipped from BLOCKED to PENDING during the
            cascade.
        """
        result = await self.db.task.cancel_all_tasks(
            self.team_name,
            skip_assignees=skip_assignees,
        )
        cancelled_tasks: List[TeamTaskBase] = result.get("cancelled_tasks") or []
        unblocked_tasks: List[TeamTaskBase] = result.get("unblocked_tasks") or []

        if not cancelled_tasks:
            team_logger.info(f"No tasks to cancel in team {self.team_name}")
            return []

        for task in cancelled_tasks:
            await self._publish_task_event(
                TaskCancelledEvent(
                    team_name=self.team_name,
                    task_id=task.task_id,
                    member_name=task.assignee,
                ),
                error_label=f"Task cancelled event for {task.task_id}",
            )
        await self._publish_unblocked_events(unblocked_tasks)
        await self._maybe_publish_task_list_drained()
        return cancelled_tasks

    async def _publish_task_event(self, event, *, error_label: str) -> None:
        """Publish a task event on the team task topic; log on failure."""
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(event),
            )
            team_logger.debug(f"Published: {error_label}")
        except Exception as e:
            team_logger.error(f"Failed to publish {error_label}: {e}")

    async def _publish_unblocked_events(self, unblocked_tasks: List[TeamTaskBase]) -> None:
        """Notify the team about tasks that just transitioned to PENDING."""
        if not unblocked_tasks:
            return
        for task in unblocked_tasks:
            await self._publish_task_event(
                TaskUnblockedEvent(team_name=self.team_name, task_id=task.task_id),
                error_label=f"Task unblocked event for {task.task_id}",
            )
        team_logger.info(f"Unblocked {len(unblocked_tasks)} tasks")

    async def _maybe_publish_task_list_drained(self) -> None:
        """Publish TASK_LIST_DRAINED if every task on the board is now terminal.

        Uses one aggregate COUNT query (total + non-terminal) instead of
        loading the whole task list and iterating — this fires on every
        terminal transition, so a full scan here is pure waste. No-op when the
        board is empty (an empty board is not "drained"). Idempotency across
        repeated terminal transitions is the consumer's concern — the manager
        just reports the fact each time it observes it.
        """
        total, non_terminal = await self.db.task.count_tasks_terminality(self.team_name)
        if total == 0 or non_terminal > 0:
            return
        await self._publish_task_event(
            TaskListDrainedEvent(team_name=self.team_name, task_count=total),
            error_label=f"Task list drained event for team {self.team_name}",
        )

    async def list_tasks(self, status: Optional[str] = None) -> List[TeamTaskBase]:
        """List all tasks for the team

        Args:
            status: Optional status filter

        Returns:
            List of TeamTask objects

        Example:
            # Get all pending tasks
            pending_tasks = task_manager.list_tasks(status="pending")

            # Get all tasks
            all_tasks = task_manager.list_tasks()
        """
        return await self.db.task.get_team_tasks(self.team_name, status)

    async def get_dependencies(self, task_id: str) -> List[TeamTaskDependencyBase]:
        """Get task dependencies

        Args:
            task_id: Task identifier

        Returns:
            List of TeamTaskDependency objects
        """
        return await self.db.task.get_task_dependencies(task_id)

    async def get_claimable_tasks(self) -> List[TeamTaskBase]:
        """Get tasks that can be claimed

        Returns tasks that are in 'pending' status.

        Returns:
            List of claimable Task objects
        """
        return await self.list_tasks(status=TaskStatus.PENDING.value)

    async def get_tasks_by_assignee(self, member_name: str, status: Optional[str] = None) -> List[TeamTaskBase]:
        """Get tasks assigned to a specific member

        Args:
            member_name: Member identifier who is assigned tasks
            status: Optional status filter

        Returns:
            List of Task objects assigned to the member
        """
        return await self.db.task.get_tasks_by_assignee(self.team_name, member_name, status)

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> TaskOpResult:
        """Update task title / content.

        Publishes ``TASK_UPDATED`` on success. Does not bump the task's
        ``updated_at`` column — that timestamp tracks status transitions
        only.

        Args:
            task_id: Task identifier.
            title: Optional new title.
            content: Optional new content.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        success = await self.db.task.update_task(task_id, title=title, content=content)
        if not success:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be edited while in status '{task.status}'; "
                f"content updates are allowed on pending / blocked / planning / in-progress tasks "
                f"(in-review tasks are locked)"
            )

        team_logger.info(f"Task {task_id} updated")

        # A claimed task stays claimed on edit; carry the assignee so its
        # dispatcher tells it to re-read the revised content (on_task_updated)
        # instead of the old reset-to-pending + cancel_member.
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskUpdatedEvent(team_name=self.team_name, task_id=task_id, member_name=task.assignee)
                ),
            )
            team_logger.debug(f"Task updated event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task updated event for {task_id}: {e}")

        return TaskOpResult.success()

    async def set_reviewer(self, task_id: str, reviewer_names: list[str]) -> TaskOpResult:
        """Set a task's verify-gate reviewers (Leader only).

        Persists the reviewer member-name list (empty clears the gate). Caller
        (the tool boundary) validates that reviewers are real members and none
        is the task's author. Independent of status — reviewers may be attached
        before or during execution; the list is consulted at completion time.

        Args:
            task_id: Task to (re)assign reviewers on.
            reviewer_names: Reviewer member names; empty list clears reviewers.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        reviewer_json = json.dumps(list(reviewer_names)) if reviewer_names else None
        ok = await self.db.task.set_reviewer(task_id, reviewer_json)
        if not ok:
            return TaskOpResult.fail(f"Task {task_id} reviewer could not be set")
        team_logger.info("Task %s reviewers set to %s", task_id, reviewer_names or "[]")
        return TaskOpResult.success()

    async def reset(self, task_id: str) -> TaskOpResult:
        """Reset a claimed task back to PENDING and clear assignee.

        Useful for re-assigning a task to another member, or for
        releasing a leaving member's claims back into the claimable pool.
        Publishes ``TASK_RELEASED`` on success so idle teammates learn
        the task is claimable again (same downstream nudge as
        ``TASK_UNBLOCKED``).

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        existing = await self.db.task.get_task(task_id)
        if not existing:
            return TaskOpResult.fail(f"Task {task_id} not found")

        result = await self.db.task.reset_task(task_id)
        if not result:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be reset from status '{existing.status}'; "
                f"only planning / in-progress / in-review tasks can be reset"
            )
        team_logger.info(f"Task {task_id} reset successfully")
        await self._publish_task_event(
            TaskReleasedEvent(team_name=self.team_name, task_id=task_id),
            error_label=f"Task released event for {task_id}",
        )
        return TaskOpResult.success()

    async def get_other_active_task_id(self, member_name: str, exclude_task_id: str) -> str | None:
        """Return the task_id of a member's active task other than
        ``exclude_task_id``, or None.

        Backs the one-active-task-per-member invariant enforced at the tool
        boundary: a claim / leader assignment / scheduler start is rejected
        when this returns a task id. "Active" spans the three owned non-terminal
        conditions — ``PLANNING`` / ``IN_PROGRESS`` / ``IN_REVIEW``. ``exclude_task_id``
        keeps an idempotent re-claim / re-assign / re-start of the same task
        from being flagged as a conflict. Probes a single ``task_id`` column
        (``LIMIT 1``) at the DB layer rather than reading the member's full
        active set.

        Args:
            member_name: Member whose active tasks to inspect.
            exclude_task_id: Task id to ignore (the one being acted on).

        Returns:
            The task_id of one other active task, or ``None`` when the member
            holds none besides ``exclude_task_id``.
        """
        return await self.db.task.get_other_active_task_id(self.team_name, member_name, exclude_task_id)

    async def start_task(self, task_id: str) -> TaskOpResult:
        """Move a scheduled task from PENDING(assignee) to IN_PROGRESS.

        Called by the scheduler when it dispatches an already-assigned task to
        its owner and execution begins — assignment (at create time) and
        execution-start are separate events in scheduled dispatch. Enforces
        the one-active-task invariant before the CAS: the owner must not
        already hold another active (PLANNING / IN_PROGRESS / IN_REVIEW) task.

        Args:
            task_id: The assigned task to start. Its ``assignee`` names the
                owner; there is no separate member argument.

        Returns:
            ``TaskOpResult`` carrying the failure reason on error.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")
        member_name = task.assignee
        if not member_name:
            return TaskOpResult.fail(f"Task {task_id} has no assignee; cannot start an unassigned task")

        # Idempotent re-start: already running for its owner -> no-op success.
        if task.status == TaskStatus.IN_PROGRESS.value:
            team_logger.debug("Task %s already started by %s; no-op", task_id, member_name)
            return TaskOpResult.success()

        # One active task per member (PLANNING / IN_PROGRESS / IN_REVIEW).
        busy_task_id = await self.get_other_active_task_id(member_name, task_id)
        if busy_task_id:
            return TaskOpResult.fail(
                f"Member {member_name} already has an active task {busy_task_id}; "
                f"finish it before starting {task_id}"
            )

        started = await self.db.task.start_task(task_id, member_name)
        if not started:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be started from status '{task.status}' "
                f"(only a pending task assigned to {member_name} can start)"
            )

        await self._publish_task_event(
            TaskStartedEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=member_name,
            ),
            error_label=f"Task started event for {task_id}",
        )
        return TaskOpResult.success()

    async def reassign(self, task_id: str, new_assignee: str) -> TaskOpResult:
        """Hand an in-progress task from its current owner to another member.

        Atomic assignee swap — the task stays IN_PROGRESS throughout and never
        bounces through PENDING, so no spurious ``TASK_RELEASED`` wakes idle
        teammates and there is no claimable-pool window to race into. Fires
        exactly two targeted events: ``TASK_REVOKED`` tells the former owner
        to steer off the now-foreign task, ``TASK_CLAIMED`` tells the new
        owner to pick it up. The former owner's other claims and in-flight
        round stay intact — this touches only the one task.

        The new member is validated *before* the swap so a bad target never
        disturbs the task.

        Args:
            task_id: Task to reassign (must be IN_PROGRESS).
            new_assignee: Member to hand the task to.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        member = await self.db.member.get_member(new_assignee, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {new_assignee} not found in team {self.team_name}")

        old_assignee = task.assignee
        if not old_assignee:
            return TaskOpResult.fail(f"Task {task_id} has no current assignee to reassign from")

        swapped = await self.db.task.reassign_task(task_id, old_assignee, new_assignee)
        if not swapped:
            return TaskOpResult.fail(
                f"Task {task_id} could not be reassigned; it is no longer claimed by {old_assignee}"
            )

        await self._publish_task_event(
            TaskRevokedEvent(team_name=self.team_name, task_id=task_id, member_name=old_assignee),
            error_label=f"Task revoked event for {task_id} (from {old_assignee})",
        )
        await self._publish_task_event(
            TaskClaimedEvent(team_name=self.team_name, task_id=task_id, member_name=new_assignee),
            error_label=f"Task claimed event for {task_id} (to {new_assignee})",
        )
        return TaskOpResult.success()

    async def approve_plan(
        self,
        plan_id: str,
        approved: bool = True,
        feedback: str = "",
        leader_name: str | None = None,
    ) -> TaskOpResult:
        """Approve or reject a member plan submission for PLAN_MODE members.

        Plan mode drives the plan gate:
        PENDING -> PLANNING when a member submits a plan, then
        PLANNING -> IN_PROGRESS when the leader approves it. A rejection
        keeps the task in PLANNING so the member can revise and resubmit a
        new plan id.

        Args:
            plan_id: Exact member plan submission identifier to review.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        if not plan_id:
            return TaskOpResult.fail("approve_plan requires plan_id")

        plan_index = self._read_plan_index(plan_id)
        if not plan_index:
            return TaskOpResult.fail(f"Plan {plan_id} not found")

        indexed_task_id = str(plan_index.get("task_id") or "").strip()
        if not indexed_task_id:
            return TaskOpResult.fail(f"Plan {plan_id} has no task_id")
        task_id = indexed_task_id

        existing = await self.db.task.get_task(task_id)
        if not existing:
            return TaskOpResult.fail(f"Task {task_id} not found")
        # PLANNING is the plan-gate state a plan is decided from.
        if existing.status != TaskStatus.PLANNING.value:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be plan-approved from status "
                f"'{existing.status}'; only a task in planning can be approved or rejected"
            )
        if not existing.assignee:
            return TaskOpResult.fail(f"Task {task_id} has no assignee")

        task_plan_index = self._read_task_plan_index(task_id)
        latest_plan_id = str(task_plan_index.get("latest_plan_id") or "")
        if latest_plan_id and plan_id != latest_plan_id:
            return TaskOpResult.fail(
                f"Plan {plan_id} is stale; review latest plan_id {latest_plan_id}"
            )

        if plan_index.get("decision") != "pending":
            return TaskOpResult.fail(
                f"Plan {plan_id} was already {plan_index.get('decision')}; "
                "the member must call submit_plan again before another approval decision"
            )

        plan_path_raw = str(plan_index.get("member_plan_md") or "").strip()
        plan_path = Path(plan_path_raw) if plan_path_raw else self._task_plan_path(task_id, plan_id)
        if not plan_path.is_file():
            return TaskOpResult.fail(
                f"Plan {plan_id} for task {task_id} has no submitted plan file; "
                "the member must call submit_plan first"
            )

        tool_call_id = str(plan_index.get("tool_call_id") or "")

        # Rejection keeps the task in PLANNING so the member can revise and
        # resubmit; approval advances the plan gate to IN_PROGRESS.
        next_status = TaskStatus.IN_PROGRESS.value if approved else existing.status
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        approval = {
            "task_id": task_id,
            "plan_id": plan_id,
            "team_plan_id": self.team_plan_id,
            "latest_plan_id": plan_id,
            "decision": "approve" if approved else "reject",
            "status": next_status,
            "feedback": feedback,
            "leader_name": leader_name or self.member_name or "leader",
            "member_name": existing.assignee,
            "member_plan_md": str(plan_path),
            "decided_at": _now_iso(),
        }

        if not approved:
            self._write_task_plan_index(
                task_id,
                {**approval, "tool_call_id": tool_call_id, "updated_at": _now_iso()},
            )
            await self._publish_task_event(
                TaskPlanResponseEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    member_name=existing.assignee,
                    approved=False,
                    status=existing.status,
                    plan_id=plan_id,
                    feedback=feedback,
                    tool_call_id=tool_call_id,
                ),
                error_label=f"Task plan response event for {task_id}",
            )
            team_logger.info("Task %s plan rejected; task remains %s", task_id, existing.status)
            return TaskOpResult.success()

        task = await self.db.task.approve_plan_task(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} could not transition to plan_approved")
        self._write_task_plan_index(
            task_id,
            {**approval, "tool_call_id": tool_call_id, "updated_at": _now_iso()},
        )
        await self._publish_task_event(
            TaskPlanResponseEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=task.assignee,
                approved=True,
                status=TaskStatus.IN_PROGRESS.value,
                plan_id=plan_id,
                feedback=feedback,
                tool_call_id=tool_call_id,
            ),
            error_label=f"Task plan response event for {task_id}",
        )
        team_logger.info(f"Task {task_id} approved successfully")
        return TaskOpResult.success()
