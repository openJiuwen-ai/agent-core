# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task management tools: create, view, update, submit, claim, and complete."""

from typing import Any

from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


# ========== Task Management ==========


def _task_node_schema(
    t: Translator,
    *,
    extra_properties: dict[str, Any] | None = None,
    extra_required: list[str] | None = None,
) -> dict:
    """Build the per-task node schema shared by every ``create_task`` variant.

    Property descriptions resolve against the shared ``create_task.*`` locale
    keys, so a variant reuses them for free and only has to add a key for the
    properties it introduces (e.g. ``create_task.task.assignee``).

    Args:
        t: Locale resolver.
        extra_properties: Variant-specific properties merged into the node.
        extra_required: Variant-specific required property names.

    Returns:
        A JSON-schema object describing one task node.
    """
    properties: dict[str, Any] = {
        "task_id": {"type": "string", "description": t("create_task", "task.task_id")},
        "title": {"type": "string", "description": t("create_task", "task.title")},
        "content": {"type": "string", "description": t("create_task", "task.content")},
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": t("create_task", "task.depends_on"),
        },
        "depended_by": {
            "type": "array",
            "items": {"type": "string"},
            "description": t("create_task", "task.depended_by"),
        },
        "reviewer": {
            "type": "array",
            "items": {"type": "string"},
            "description": t("create_task", "task.reviewer"),
        },
    }
    properties.update(extra_properties or {})
    return {
        "type": "object",
        "properties": properties,
        "required": ["title", "content", *(extra_required or [])],
    }


def _spec_label(spec: dict) -> str:
    """Human-readable label for a task spec in error messages."""
    return spec.get("task_id") or spec.get("title") or "<unnamed>"


def _validate_task_batch(tasks: list[dict]) -> str | None:
    """Validate batch-level invariants shared by every ``create_task`` variant.

    Returns an error string, or None when the batch is well-formed. In-batch
    edges have exactly one representation (``depends_on`` on the dependent
    task), so a ``depended_by`` pointing at a task of the same call is
    rejected instead of silently deduplicated — the error teaches the caller
    the canonical form.
    """
    batch_ids: set[str] = set()
    for spec in tasks:
        if not spec.get("title") or not spec.get("content"):
            return f"Task {_spec_label(spec)!r} missing required title/content"
        task_id = spec.get("task_id")
        if task_id:
            if task_id in batch_ids:
                return f"Duplicate task_id {task_id!r} in this call"
            batch_ids.add(task_id)

    for spec in tasks:
        in_batch_targets = [dep for dep in spec.get("depended_by") or () if dep in batch_ids]
        if in_batch_targets:
            return (
                f"Task {_spec_label(spec)!r}: depended_by may only reference "
                f"tasks that already exist on the board, but {in_batch_targets} are created "
                f"in this same call — express in-batch edges with depends_on on the dependent task"
            )
    return None


def _clean_reviewers(spec: dict) -> list[str]:
    """Extract a spec's reviewer list, trimmed and de-blanked."""
    return [str(r).strip() for r in (spec.get("reviewer") or ()) if str(r).strip()]


async def _validate_reviewers(agent_team: TeamBackend, tasks: list[dict]) -> str | None:
    """Reject a batch whose reviewer names a non-member or the task's own author.

    Reviewers are untrusted input crossing the tool boundary; the DB column has
    no FK. A member may not review their own task (self-verification), so a
    reviewer equal to the task's ``assignee`` is rejected.
    """
    for spec in tasks:
        reviewers = _clean_reviewers(spec)
        if not reviewers:
            continue
        assignee = (spec.get("assignee") or "").strip()
        for reviewer in reviewers:
            if not await agent_team.member_exists(reviewer):
                return f"Task {_spec_label(spec)!r}: reviewer {reviewer!r} not found in the team"
            if assignee and reviewer == assignee:
                return (
                    f"Task {_spec_label(spec)!r}: reviewer {reviewer!r} cannot review their own task "
                    f"(they are the assignee)"
                )
    return None


class TaskCreateTool(TeamTool):
    """Create team tasks; tasks land unassigned and claimable (autonomous dispatch).

    The whole call is one atomic graph mutation via ``add_graph``: edges
    among tasks of the same call are expressed with ``depends_on`` only
    (forward references allowed), while ``depended_by`` is reserved for
    wiring *existing* tasks to depend on a new task. In-batch ``depended_by``
    targets are rejected at this boundary as redundant.
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _task_node_schema(t),
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        error = _validate_task_batch(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await _validate_reviewers(self.agent_team, tasks)
        if error:
            return ToolOutput(success=False, error=error)

        # One atomic graph mutation for the whole call: depends_on may
        # forward-reference tasks later in the batch, and either every
        # task lands or none does (with the real failure reason).
        result = await self.task_manager.add_graph(
            [
                TaskGraphSpec(
                    title=spec["title"],
                    content=spec["content"],
                    task_id=spec.get("task_id"),
                    depends_on=tuple(spec.get("depends_on") or ()),
                    depended_by=tuple(spec.get("depended_by") or ()),
                    reviewer=tuple(_clean_reviewers(spec)),
                )
                for spec in tasks
            ]
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        if len(result.tasks) == 1:
            return ToolOutput(success=True, data=result.tasks[0].brief())
        return ToolOutput(
            success=True,
            data={"tasks": [task.brief() for task in result.tasks], "count": len(result.tasks)},
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "task_id" in d and "title" in d:
            return f"Task created: task_id={d['task_id']} title={d['title']}"
        lines = [f"task_id={task['task_id']} title={task['title']}" for task in d.get("tasks", [])]
        lines.append(f"Created {d['count']}")
        return "\n".join(lines)


def _owner_phrase(task: dict) -> str:
    """Render a scheduled task's owner and whether it is ready or waiting."""
    if task.get("status") == TaskStatus.BLOCKED.value:
        return f"-> {task['assignee']} (blocked; starts once its dependencies complete)"
    return f"-> {task['assignee']} (assigned; the scheduler starts it)"


class ScheduledTaskCreateTool(TeamTool):
    """Create team tasks, each naming its owner (scheduled dispatch).

    Same atomic ``add_graph`` and same edge rules as ``TaskCreateTool``, plus
    a required ``assignee`` that rides along in the same mutation: the task
    rests at PENDING (or BLOCKED, if it has dependencies) *with its owner on
    record*, and the scheduler moves it to STARTED when execution begins.
    Members never claim in this mode, so a task without an assignee would
    never run — hence ``assignee`` is required
    and the result echoes the owner and landing status.
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task_scheduled"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _task_node_schema(
                        t,
                        extra_properties={
                            "assignee": {"type": "string", "description": t("create_task", "task.assignee")},
                        },
                        extra_required=["assignee"],
                    ),
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    async def _validate_assignees(self, tasks: list[dict]) -> str | None:
        """Reject a batch that names an assignee the roster does not have.

        The DB column carries no FK to team_member, and the whole batch is
        one transaction — catching a typo here keeps the graph from landing
        bound to a member nobody serves.
        """
        for spec in tasks:
            assignee = (spec.get("assignee") or "").strip()
            if not assignee:
                return (
                    f"Task {_spec_label(spec)!r} missing required 'assignee' — "
                    f"scheduled tasks are never claimed, so every task must name its owner"
                )
            if not await self.agent_team.member_exists(assignee):
                return f"Task {_spec_label(spec)!r}: member {assignee!r} not found in the team"
        return None

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        error = _validate_task_batch(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await self._validate_assignees(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await _validate_reviewers(self.agent_team, tasks)
        if error:
            return ToolOutput(success=False, error=error)

        result = await self.task_manager.add_graph(
            [
                TaskGraphSpec(
                    title=spec["title"],
                    content=spec["content"],
                    task_id=spec.get("task_id"),
                    depends_on=tuple(spec.get("depends_on") or ()),
                    depended_by=tuple(spec.get("depended_by") or ()),
                    assignee=(spec.get("assignee") or "").strip(),
                    reviewer=tuple(_clean_reviewers(spec)),
                )
                for spec in tasks
            ]
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        # The owner and landing status are the whole point of this variant:
        # the leader must tell "started now" from "waiting on dependencies"
        # without a follow-up view_task.
        briefs = [{**task.brief(), "assignee": task.assignee} for task in result.tasks]
        if len(briefs) == 1:
            return ToolOutput(success=True, data=briefs[0])
        return ToolOutput(success=True, data={"tasks": briefs, "count": len(briefs)})

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "task_id" in d and "title" in d:
            return f"Task created: task_id={d['task_id']} title={d['title']} {_owner_phrase(d)}"
        lines = [f"task_id={task['task_id']} title={task['title']} {_owner_phrase(task)}" for task in d.get("tasks", [])]
        lines.append(f"Created {d['count']}")
        return "\n".join(lines)


class ViewTaskToolV2(TeamTool):
    """Unified task viewing tool (V2).

    Explicit action enum instead of implicit param-based dispatch.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.view_task",
                name="view_task",
                description=t("view_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "claimable", "in_review"],
                    "description": t("view_task", "action"),
                },
                "task_id": {"type": "string", "description": t("view_task", "task_id")},
                "status": {
                    "type": "string",
                    "description": t("view_task", "status"),
                },
            },
            "required": [],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action", "list")

        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="task_id required for get action")
            detail = await self.task_manager.get_task_detail(task_id=task_id)
            if detail:
                return ToolOutput(success=True, data=detail.model_dump(exclude_none=True))
            return ToolOutput(success=False, error="Task not found")

        if action == "in_review":
            # Tasks this member must verify (it is a reviewer and status=IN_REVIEW).
            result = await self.task_manager.list_review_tasks(self.task_manager.member_name)
        elif action == "claimable":
            result = await self.task_manager.list_tasks_with_deps(
                status=TaskStatus.PENDING.value,
            )
        else:
            result = await self.task_manager.list_tasks_with_deps(
                status=inputs.get("status"),
            )

        return ToolOutput(success=True, data=result.model_dump())

    def map_result(self, output: ToolOutput) -> str:
        """Map view_task result — tiered output by action.

        Both tiers render the task's last-transition time as ``<absolute
        local time> (<relative diff>)`` so the model can tell how long a
        task has been sitting in its current status.
        """
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        now_ms = get_current_time()
        # Detail view (get action) — mirrors TaskGetTool
        if "content" in d:
            lines = [
                f"Task #{d['task_id']}: {d['title']}",
                f"Status: {d['status']}",
                f"Content: {d['content']}",
            ]
            if d.get("assignee"):
                lines.append(f"Assignee: {d['assignee']}")
            if d.get("reviewer"):
                lines.append(f"Reviewers: {', '.join(d['reviewer'])}")
            if d.get("updated_at") is not None:
                lines.append(f"Updated: {format_time_context(d['updated_at'], now_ms)}")
            if d.get("blocked_by"):
                lines.append(f"Blocked by: {', '.join(f'#{tid}' for tid in d['blocked_by'])}")
            if d.get("blocks"):
                lines.append(f"Blocks: {', '.join(f'#{tid}' for tid in d['blocks'])}")
            return "\n".join(lines)
        # List view (list/claimable action) — mirrors TaskListTool
        tasks = d.get("tasks", [])
        if not tasks:
            return "No tasks found"
        lines = []
        for task in tasks:
            parts = [f"#{task['task_id']} [{task['status']}] {task['title']}"]
            if task.get("assignee"):
                parts.append(f"({task['assignee']})")
            if task.get("updated_at") is not None:
                parts.append(f"({format_time_context(task['updated_at'], now_ms)})")
            if task.get("blocked_by"):
                parts.append(f"[blocked by {', '.join(f'#{tid}' for tid in task['blocked_by'])}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)


class UpdateTaskTool(TeamTool):
    """Update task content or cancel tasks (Leader only)."""

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.update_task",
                name="update_task",
                description=t("update_task"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.t = t
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("update_task", "task_id")},
                "status": {
                    "type": "string",
                    "enum": ["cancelled"],
                    "description": t("update_task", "status"),
                },
                "title": {"type": "string", "description": t("update_task", "title")},
                "content": {"type": "string", "description": t("update_task", "content")},
                "assignee": {"type": "string", "description": t("update_task", "assignee")},
                "reviewer": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("update_task", "reviewer"),
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("update_task", "add_blocked_by"),
                },
            },
            "required": ["task_id"],
        }

    async def _is_human_agent_locked(self, task) -> bool:
        """Whether a task is held by a human-agent member and therefore
        leader-immutable.

        The leader may not cancel or reassign such tasks — only the human
        collaborator can release them (by completing, or by the team
        being cleaned). The leader's only recourse is send_message nudges.
        """
        active = (
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.IN_REVIEW.value,
        )
        return await self.agent_team.is_human_agent(task.assignee) and task.status in active

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        status = inputs.get("status")
        title = inputs.get("title")
        content = inputs.get("content")
        assignee = inputs.get("assignee")
        reviewer = inputs.get("reviewer")
        add_blocked_by = inputs.get("add_blocked_by")

        # cancel_all: task_id="*" + status="cancelled"
        if task_id == "*" and status == "cancelled":
            # Each cancelled task fires a targeted TASK_CANCELLED carrying its
            # assignee, so every affected member is steered off its task via
            # on_task_cancelled — no member-wide cancel needed. Preserve every
            # human-agent-claimed task (empty set is treated as None).
            skip = set(await self.agent_team.human_agent_names())
            count = await self.agent_team.cancel_all_tasks(skip_assignees=skip or None)
            return ToolOutput(success=True, data={"cancelled_count": count})

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        # Cancel single task
        if status == "cancelled":
            if await self._is_human_agent_locked(task):
                return ToolOutput(
                    success=False,
                    error=self.t(
                        "update_task",
                        "error_human_agent_locked_cancel",
                        task_id=task_id,
                    ),
                )
            success = await self.agent_team.cancel_task(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to cancel task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

        # Collect all field updates in one pass
        updated: list[str] = []

        # Content update (title and/or content). A human-agent-claimed task is
        # leader-immutable (same rule as cancel / reassign); refuse the edit.
        if title or content:
            if await self._is_human_agent_locked(task):
                return ToolOutput(
                    success=False,
                    error=self.t("update_task", "error_human_agent_locked_edit", task_id=task_id),
                )
            result = await self.task_manager.update_task(task_id, title=title, content=content)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            if title:
                updated.append("title")
            if content:
                updated.append("content")

        # Assign task to member. When the task is already claimed by a
        # different member, treat this as a leader-driven reassignment:
        # reset the task back to PENDING and hand it to the new member. The
        # former assignee is told via a targeted TASK_REVOKED event (not a
        # member-wide cancel), so only this one task moves — its other
        # claims and in-flight round survive. Same-member is idempotent.
        if assignee:
            # One active task per member: reject before any state change so a
            # rejected assign never disturbs the current owner or this task.
            busy_task_id = await self.task_manager.get_other_active_task_id(assignee, task_id)
            if busy_task_id:
                return ToolOutput(
                    success=False,
                    error=(
                        f"Member '{assignee}' already has an active task #{busy_task_id}; "
                        f"wait for it to complete before assigning another."
                    ),
                )
            if task.assignee and task.assignee != assignee:
                if await self._is_human_agent_locked(task):
                    return ToolOutput(
                        success=False,
                        error=self.t(
                            "update_task",
                            "error_human_agent_locked_reassign",
                            task_id=task_id,
                            new_assignee=assignee,
                        ),
                    )
                assign_result = await self.task_manager.reassign(task_id, assignee)
            else:
                assign_result = await self.task_manager.assign(task_id, assignee)
            if not assign_result.ok:
                return ToolOutput(success=False, error=assign_result.reason)
            updated.append("assignee")

        # Set / clear verify-gate reviewers. A leader may (re)assign reviewers
        # at any status; an empty list clears the gate. Reviewers must be real
        # members and none may be the task's author (no self-verification).
        if reviewer is not None:
            reviewer_names = [str(r).strip() for r in reviewer if str(r).strip()]
            current_assignee = (assignee or task.assignee or "").strip()
            for name in reviewer_names:
                if not await self.agent_team.member_exists(name):
                    return ToolOutput(success=False, error=f"Reviewer '{name}' not found in the team")
                if current_assignee and name == current_assignee:
                    return ToolOutput(
                        success=False,
                        error=f"Reviewer '{name}' cannot review their own task (they are the assignee)",
                    )
            reviewer_result = await self.task_manager.set_reviewer(task_id, reviewer_names)
            if not reviewer_result.ok:
                return ToolOutput(success=False, error=reviewer_result.reason)
            updated.append("reviewer")

        # Add dependencies (blocked_by edges)
        if add_blocked_by:
            deps_result = await self.task_manager.add_dependencies(task_id, add_blocked_by)
            if not deps_result.ok:
                return ToolOutput(success=False, error=deps_result.reason)
            updated.append("blocked_by")

        if not updated:
            return ToolOutput(
                success=False,
                error="No update specified — provide status, title, content, assignee, reviewer, or add_blocked_by",
            )

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "updated",
                "updated_fields": updated,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "cancelled_count" in d:
            return f"Cancelled {d['cancelled_count']} tasks"
        return f"Task #{d['task_id']} {d['status']}"


class SubmitPlanTool(TeamTool):
    """Submit an execution plan for a plan-mode task."""

    def __init__(
        self,
        task_manager: TeamTaskManager,
        t: Translator,
        *,
        name: str = "submit_plan",
        tool_id: str = "team.submit_plan",
    ):
        super().__init__(
            ToolCard(
                id=tool_id,
                name=name,
                description=t("submit_plan"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("submit_plan", "task_id")},
                "plan_id": {"type": "string", "description": t("submit_plan", "plan_id")},
                "plan_path": {"type": "string", "description": t("submit_plan", "plan_path")},
            },
            "required": ["task_id", "plan_path"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        result = await self.task_manager.submit_plan(
            task_id=inputs.get("task_id"),
            plan_id=inputs.get("plan_id"),
            plan_path=inputs.get("plan_path") or "",
        )
        return ToolOutput(
            success=bool(result.get("success")),
            data=result,
            error=None if result.get("success") else result.get("message", "Failed to submit member plan"),
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to submit member plan"
        d = output.data
        return (
            f"Member plan submitted: task_id={d.get('task_id')} plan_id={d.get('plan_id')} "
            f"status={d.get('status')} "
            f"member_plan_md={d.get('member_plan_md')}"
        )


class ClaimTaskTool(TeamTool):
    """Claim or complete a task (Teammate only)."""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.claim_task",
                name="claim_task",
                description=t("claim_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("claim_task", "task_id")},
                "status": {
                    "type": "string",
                    "enum": ["claimed", "completed"],
                    "description": t("claim_task", "status"),
                },
            },
            "required": ["task_id", "status"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        status = inputs.get("status")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        if status == "claimed":
            # One active task per member: refuse a second concurrent claim so a
            # teammate finishes its current task before picking up another.
            busy_task_id = await self.task_manager.get_other_active_task_id(self.task_manager.member_name, task_id)
            if busy_task_id:
                return ToolOutput(
                    success=False,
                    error=(
                        f"You already have an active task #{busy_task_id}; "
                        f"complete it before claiming another."
                    ),
                )
            claim_result = await self.task_manager.claim(task_id=task_id)
            if not claim_result.ok:
                return ToolOutput(success=False, error=claim_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.IN_PROGRESS.value}

        elif status == "completed":
            complete_result = await self.task_manager.complete(task_id=task_id)
            if not complete_result.ok:
                return ToolOutput(success=False, error=complete_result.reason)
            # A reviewer-carrying task enters IN_REVIEW instead of completing.
            settled = await self.task_manager.get(task_id)
            to_status = (
                TaskStatus.IN_REVIEW.value
                if settled and settled.status == TaskStatus.IN_REVIEW.value
                else TaskStatus.COMPLETED.value
            )
            status_change = {"from": task.status, "to": to_status}

        else:
            return ToolOutput(success=False, error=f"Invalid status: {status}")

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "updated_fields": ["status"],
                "status_change": status_change,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        """Map claim_task result with behavior guidance on completion."""
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        sc = d["status_change"]
        result = f"Task #{d['task_id']} {sc['from']} → {sc['to']}"
        if sc["to"] == TaskStatus.COMPLETED.value:
            result += "\n\nTask completed. Call view_task now to find your next available task."
        return result


class MemberCompleteTaskTool(TeamTool):
    """Complete a task whose ``assignee`` is the calling member.

    Self-only by design: the tool refuses any task whose ``assignee``
    differs from the caller's ``member_name``. Distinct from
    ``ClaimTaskTool`` (which couples claim and complete and is
    teammate-only) and from leader's ``UpdateTaskTool`` (which manages
    the team-wide task graph). Wired into ``HUMAN_AGENT_TOOLS`` so the
    user's avatar can mark its leader-assigned tasks as done without
    inheriting any of leader's coordination authority, and into the
    scheduled-dispatch member set, where no member claims its own work.

    Behaviour is identical either way; only the description differs, so the
    caller picks the ``desc_key`` instead of the class.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator, *, desc_key: str = "member_complete_task"):
        super().__init__(
            ToolCard(
                id="team.member_complete_task",
                name="member_complete_task",
                description=t(desc_key),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": t("member_complete_task", "task_id"),
                },
                "note": {
                    "type": "string",
                    "description": t("member_complete_task", "note"),
                },
            },
            "required": ["task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        try:
            task = await self.task_manager.get(task_id)
        except Exception as e:
            team_logger.error("member_complete_task: get(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not task:
            return ToolOutput(success=False, error=f"Task '{task_id}' not found")

        caller = self.task_manager.member_name
        if task.assignee != caller:
            return ToolOutput(
                success=False,
                error=(
                    f"Task '{task_id}' is assigned to "
                    f"'{task.assignee or '<unassigned>'}', not '{caller}'; "
                    "you can only complete tasks assigned to yourself"
                ),
            )

        try:
            result = await self.task_manager.complete(task_id=task_id)
        except Exception as e:
            team_logger.error("member_complete_task: complete(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        # The task carries reviewers -> it entered the verify gate (IN_REVIEW)
        # rather than completing. Report the true outcome so the author knows a
        # reviewer decision is pending.
        settled = await self.task_manager.get(task_id)
        outcome = "in_review" if settled and settled.status == TaskStatus.IN_REVIEW.value else "completed"

        note = (inputs.get("note") or "").strip() or None
        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": outcome,
                "note": note,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to complete task"
        d = output.data
        if d.get("status") == "in_review":
            line = f"Task #{d['task_id']} submitted for review — awaiting a reviewer's verdict"
        else:
            line = f"Task #{d['task_id']} completed"
        if d.get("note"):
            line += f" (note: {d['note']})"
        return line


class VerifyTaskTool(TeamTool):
    """Reviewer verdict on a task in the verify gate (reviewer only).

    A task carrying reviewers enters ``IN_REVIEW`` when its author completes.
    A reviewer named on the task calls this to pass it (``IN_REVIEW ->
    COMPLETED``, unblocking dependents) or fail it (``IN_REVIEW ->
    IN_PROGRESS``, rework loop with ``feedback`` directed at the author). The
    manager enforces that the caller is a reviewer of the task and not its
    author.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.verify_task",
                name="verify_task",
                description=t("verify_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("verify_task", "task_id")},
                "decision": {
                    "type": "string",
                    "enum": ["pass", "fail"],
                    "description": t("verify_task", "decision"),
                },
                "feedback": {"type": "string", "description": t("verify_task", "feedback")},
            },
            "required": ["task_id", "decision"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")
        decision = (inputs.get("decision") or "").strip()
        feedback = (inputs.get("feedback") or "").strip()

        result = await self.task_manager.verify_task(task_id, decision, feedback)
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(
            success=True,
            data={"task_id": task_id, "decision": decision.lower(), "feedback": feedback or None},
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to verify task"
        d = output.data
        if d["decision"] == "pass":
            return f"Task #{d['task_id']} verified and completed."
        line = f"Task #{d['task_id']} sent back for revision"
        if d.get("feedback"):
            line += f" (feedback: {d['feedback']})"
        return line
