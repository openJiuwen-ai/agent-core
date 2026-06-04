# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Task Manager Module

This module provides task management functionality for agent teams.
"""

import json
import shutil
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
    TaskUnblockedEvent,
    TaskUpdatedEvent,
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
    TaskListResult,
    TaskOpResult,
    TaskSummary,
)
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database import (
    TASK_TERMINAL_STATUSES,
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

    async def add_batch(self, tasks: List[dict]) -> List[TaskCreateResult]:
        """Add multiple tasks to the team in batch.

        Creates multiple tasks in a single operation for efficiency. Each
        task in the list is a dictionary with keys ``title``, ``content``,
        optional ``task_id`` and ``dependencies``. Invalid specs (missing
        title/content) are skipped silently.

        Args:
            tasks: List of task dictionaries.

        Returns:
            List of ``TaskCreateResult`` — one per successfully created
            task. Failed specs (missing fields, creation errors) are
            omitted so callers can still treat the return value as a
            list of created tasks. Because ``TaskCreateResult`` delegates
            attribute lookups to the wrapped task, existing call sites
            that iterate and read ``.task_id`` / ``.title`` work
            unchanged.
        """
        created_tasks: List[TaskCreateResult] = []
        for task_spec in tasks:
            title = task_spec.get("title")
            content = task_spec.get("content")
            task_id = task_spec.get("task_id")
            dependencies = task_spec.get("dependencies")

            if not title or not content:
                team_logger.warning(f"Skipping invalid task: {task_spec}")
                continue

            result = await self.add(
                title=title,
                content=content,
                task_id=task_id,
                dependencies=dependencies,
            )
            if result.ok:
                created_tasks.append(result)
            else:
                team_logger.warning(f"Batch add skipped task {task_id or title!r}: {result.reason}")

        team_logger.info(f"Batch added {len(created_tasks)} tasks")
        return created_tasks

    async def add(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
    ) -> TaskCreateResult:
        """Add a task to the team.

        Creates a task in the team_task table and optionally adds
        task dependencies to the team_task_dependency table.

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
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Initial seed: PENDING for an isolated task; the graph mutation
        # path's refresh pass will flip it to BLOCKED if any dependency
        # is still unresolved at insert time.
        status = TaskStatus.PENDING.value

        if dependencies:
            # Edges + node go through the unified mutation primitive so
            # cycle detection and BLOCKED/PENDING refresh fire as one
            # atomic step.
            mutation = await self.db.task.mutate_dependency_graph(
                team_name=self.team_name,
                new_tasks=[
                    NewTaskSpec(
                        task_id=task_id,
                        title=title,
                        content=content,
                        initial_status=status,
                    )
                ],
                add_edges=[(task_id, dep_id) for dep_id in dependencies],
            )
            if not mutation.ok:
                return TaskCreateResult.fail(f"Failed to create task {task_id}: {mutation.reason}")
            # Refresh pass may have flipped PENDING -> BLOCKED; reflect
            # that in the response so the caller and downstream event
            # carry the right status.
            for refreshed in mutation.refreshed_tasks:
                if refreshed.task_id == task_id:
                    status = refreshed.status
                    break
            team_logger.debug(f"Added task {task_id} with dependencies: {dependencies}")
        else:
            success = await self.db.task.create_task(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
            )
            if not success:
                return TaskCreateResult.fail(f"Failed to create task {task_id} (likely a task_id collision)")

        # Publish task created event
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

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def add_with_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        dependent_task_ids: Optional[List[str]] = None,
    ) -> TaskCreateResult:
        """Add a task with bidirectional dependency support (prioritized task)

        This method allows creating a task that can:
        1. Depend on existing tasks (dependencies parameter)
        2. Have existing tasks depend on it (dependent_task_ids parameter)
        3. Both of the above (inserting the task between other tasks in the dependency chain)

        When existing tasks are made to depend on the new task (dependent_task_ids),
        their status is automatically updated from 'pending' to 'blocked' if applicable.

        This operation is atomic and prevents circular dependencies.

        Args:
            title: Task title
            content: Task content
            task_id: Optional custom task ID (auto-generated if not provided)
            dependencies: List of existing task IDs that the new task depends on
            dependent_task_ids: List of existing task IDs that should depend on the new task

        Returns:
            ``TaskCreateResult`` describing the outcome. On failure
            ``result.reason`` typically points at a circular dependency
            or a conflicting task_id.
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Determine initial status based on dependencies
        # If the task has dependencies, it starts as BLOCKED
        if dependencies and len(dependencies) > 0:
            status = TaskStatus.BLOCKED.value
        else:
            status = TaskStatus.PENDING.value

        # Use database method for atomic operation with cycle detection
        success = await self.db.task.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=dependencies,
            dependent_task_ids=dependent_task_ids,
        )

        if not success:
            return TaskCreateResult.fail(
                f"Failed to create prioritized task {task_id} "
                f"(circular dependency, missing dependent task, or task_id collision)"
            )

        # Publish task created event
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

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def add_as_top_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
    ) -> TaskCreateResult:
        """Add a task as top priority (blocks all existing pending/blockable tasks)

        This method creates a new task and makes all existing tasks that can be blocked
        (pending or claimed status) depend on it. This ensures the new task is executed
        before those tasks.

        This is useful for inserting urgent tasks that must be processed before
        any other pending work.

        This operation is atomic and prevents circular dependencies.

        Args:
            title: Task title.
            content: Task content.
            task_id: Optional custom task ID (auto-generated if not provided).

        Returns:
            ``TaskCreateResult`` describing the outcome.
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Get all tasks that can be blocked (pending or claimed status)
        # These tasks will be made to depend on the new top priority task
        pending_tasks = await self.list_tasks(status=TaskStatus.PENDING.value)

        dependent_task_ids = [task.task_id for task in pending_tasks]

        # Top priority task has no dependencies, so it starts as PENDING.
        status = TaskStatus.PENDING.value

        # Use database method for atomic operation with cycle detection
        success = await self.db.task.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=None,
            dependent_task_ids=dependent_task_ids if dependent_task_ids else None,
        )

        if not success:
            return TaskCreateResult.fail(
                f"Failed to create top priority task {task_id} (circular dependency or task_id collision)"
            )

        team_logger.info(f"Added top priority task {task_id}, blocking {len(dependent_task_ids)} existing tasks")

        # Publish task created event
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

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def list_tasks_with_deps(self, status: Optional[str] = None) -> TaskListResult:
        """List tasks with blocked_by info (summary view, no content).

        Returns a lightweight task list where each entry includes unresolved
        dependency IDs. Suitable for list/claimable actions.

        Args:
            status: Optional status filter.

        Returns:
            TaskListResult containing task summaries with dependency info.
        """
        tasks = await self.list_tasks(status=status)
        summaries = []
        for task in tasks:
            deps = await self.get_dependencies(task.task_id)
            unresolved = [d.depends_on_task_id for d in deps if not d.resolved]
            summaries.append(
                TaskSummary(
                    task_id=task.task_id,
                    title=task.title,
                    status=task.status,
                    assignee=task.assignee,
                    blocked_by=unresolved,
                    updated_at=task.updated_at,
                )
            )
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

        downstream = await self.db.task.get_tasks_depending_on(task_id)
        blocks = [t.task_id for t in downstream]

        return TaskDetail(
            task_id=task.task_id,
            title=task.title,
            content=task.content,
            status=task.status,
            assignee=task.assignee,
            blocked_by=blocked_by,
            blocks=blocks,
            updated_at=task.updated_at,
        )

    async def get(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get a task by ID

        Args:
            task_id: Task identifier

        Returns:
            TeamTask object if found, None otherwise
        """
        return await self.db.task.get_task(task_id)

    async def assign(self, task_id: str, assignee: str) -> TaskOpResult:
        """Assign a task to a member and mark it as claimed (Leader only).

        Atomically sets the assignee and transitions the task to ``CLAIMED``
        so leader-driven assignment is symmetric with a member self-claim.
        Idempotent when the task is already claimed by ``assignee``. Fails
        when the task is currently held by a different member; the caller
        must reset the task first (see ``reset``) for true reassignment.
        Sends a notification message to the assigned member's mailbox on
        success.

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

        # Idempotent re-assign: same member, status already claimed -> no-op success.
        if task.assignee == assignee and task.status == TaskStatus.CLAIMED.value:
            team_logger.debug(f"Task {task_id} already assigned to {assignee}; no-op")
            return TaskOpResult.success()

        if task.assignee and task.assignee != assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}; reset the task before reassigning to {assignee}"
            )

        success = await self.db.task.claim_task(task_id, assignee)
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
                "leader approval moves the task from claimed to plan_approved"
            )

        # Idempotent re-claim: if the caller already owns this task, succeed silently.
        if task.assignee == member_name and task.status == TaskStatus.CLAIMED.value:
            team_logger.debug(f"Task {task_id} already claimed by {member_name}; no-op")
            return TaskOpResult.success()

        # Claim conflict must be reported before the state-transition check —
        # otherwise a CLAIMED task held by someone else surfaces as the
        # misleading "invalid claimed → claimed transition" error.
        if task.assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}, {member_name} cannot claim it"
            )

        # Validate state transition (blocks e.g. COMPLETED/CANCELLED/BLOCKED claims).
        if not is_valid_transition(
            TaskStatus(task.status),
            TaskStatus.CLAIMED,
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
        # Get member to check mode
        member = await self.db.member.get_member(self.member_name, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {self.member_name} not found in team {self.team_name}")

        # Check if member is in PLAN_MODE
        if member.mode == MemberMode.PLAN_MODE.value:
            task = await self.db.task.get_task(task_id)
            if not task:
                return TaskOpResult.fail(f"Task {task_id} not found")

            # PLAN_MODE members can only complete PLAN_APPROVED tasks.
            if task.status != TaskStatus.PLAN_APPROVED.value:
                return TaskOpResult.fail(
                    f"PLAN_MODE member cannot complete task {task_id} in status "
                    f"'{task.status}'; only plan_approved tasks can be completed"
                )

        # Complete task atomically - this handles state validation, dependency resolution,
        # and unblocking dependent tasks in a single transaction to prevent race conditions
        result = await self.db.task.complete_task(task_id)
        if not result:
            current = await self.db.task.get_task(task_id)
            if current is None:
                return TaskOpResult.fail(f"Task {task_id} not found")
            return TaskOpResult.fail(
                f"Task {task_id} cannot be completed from status '{current.status}' (must be claimed or plan_approved)"
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
        """Snapshot a member execution plan file and reserve the task as CLAIMED."""
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
        if task.status not in (TaskStatus.PENDING.value, TaskStatus.CLAIMED.value):
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
            claimed = await self.db.task.claim_task(task_id, member_name)
            if not claimed:
                return {"success": False, "task_id": task_id, "message": "Failed to reserve task for planning"}
        elif task.assignee != member_name:
            return {
                "success": False,
                "task_id": task_id,
                "message": f"Task {task_id} is assigned to {task.assignee}, not {member_name}",
            }

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
            "status": TaskStatus.CLAIMED.value,
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
                status=TaskStatus.CLAIMED.value,
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
            "status": TaskStatus.CLAIMED.value,
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

        await self._publish_task_event(
            TaskCancelledEvent(team_name=self.team_name, task_id=task_id),
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
                TaskCancelledEvent(team_name=self.team_name, task_id=task.task_id),
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
        """Publish TASK_LIST_DRAINED if every task in the list is now terminal.

        Re-reads the full task list; no-op when the list is empty (an empty
        board is not "drained"). Idempotency across repeated terminal
        transitions is the consumer's concern — the manager just reports the
        fact each time it observes it.
        """
        tasks = await self.list_tasks()
        if not tasks:
            return
        if any(tk.status not in TASK_TERMINAL_STATUSES for tk in tasks):
            return
        await self._publish_task_event(
            TaskListDrainedEvent(team_name=self.team_name, task_count=len(tasks)),
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
                f"content updates are only allowed on pending / blocked tasks"
            )

        team_logger.info(f"Task {task_id} updated")

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskUpdatedEvent(team_name=self.team_name, task_id=task_id)),
            )
            team_logger.debug(f"Task updated event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task updated event for {task_id}: {e}")

        return TaskOpResult.success()

    async def reset(self, task_id: str) -> TaskOpResult:
        """Reset a claimed task back to PENDING and clear assignee.

        Useful for re-assigning a task to another member.

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
                f"Task {task_id} cannot be reset from status '{existing.status}'; only claimed tasks can be reset"
            )
        team_logger.info(f"Task {task_id} reset successfully")
        return TaskOpResult.success()

    async def approve_plan(
        self,
        plan_id: str,
        approved: bool = True,
        feedback: str = "",
        leader_name: str | None = None,
    ) -> TaskOpResult:
        """Approve or reject a member plan submission for PLAN_MODE members.

        Plan mode reuses the existing task state machine:
        PENDING -> CLAIMED when a member submits a plan, then
        CLAIMED -> PLAN_APPROVED when the leader approves it. A rejection
        keeps the task in CLAIMED so the member can revise and resubmit a
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
        if existing.status != TaskStatus.CLAIMED.value:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be plan-approved from status "
                f"'{existing.status}'; only claimed tasks can be approved or rejected"
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

        next_status = TaskStatus.PLAN_APPROVED.value if approved else TaskStatus.CLAIMED.value
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
                    status=TaskStatus.CLAIMED.value,
                    plan_id=plan_id,
                    feedback=feedback,
                    tool_call_id=tool_call_id,
                ),
                error_label=f"Task plan response event for {task_id}",
            )
            team_logger.info(f"Task {task_id} plan rejected; task remains claimed")
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
                status=TaskStatus.PLAN_APPROVED.value,
                plan_id=plan_id,
                feedback=feedback,
                tool_call_id=tool_call_id,
            ),
            error_label=f"Task plan response event for {task_id}",
        )
        team_logger.info(f"Task {task_id} approved successfully")
        return TaskOpResult.success()
