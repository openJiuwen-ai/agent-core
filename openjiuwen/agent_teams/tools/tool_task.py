# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task management tools: create, view, update, submit, claim, and complete."""

from typing import Any, Dict

from openjiuwen.agent_teams.schema.status import TaskStatus
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


class TaskCreateTool(TeamTool):
    """Create team tasks (Leader only).

    Unified creation: tasks with depended_by auto-route to add_with_priority(),
    plain tasks route to add() / add_batch().
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task"),
            )
        )
        self.task_manager = agent_team.task_manager

        _task_schema: dict = {
            "type": "object",
            "properties": {
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
            },
            "required": ["title", "content"],
        }

        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _task_schema,
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    async def _create_one(self, spec: dict):
        """Create one task via the right add path; returns a TaskCreateResult."""
        if spec.get("depended_by"):
            return await self.task_manager.add_with_priority(
                title=spec["title"],
                content=spec["content"],
                task_id=spec.get("task_id"),
                dependencies=spec.get("depends_on"),
                dependent_task_ids=spec.get("depended_by"),
            )
        return await self.task_manager.add(
            title=spec["title"],
            content=spec["content"],
            task_id=spec.get("task_id"),
            dependencies=spec.get("depends_on"),
        )

    @staticmethod
    def _spec_label(spec: dict) -> str:
        return spec.get("task_id") or spec.get("title") or "<unnamed>"

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        if len(tasks) == 1:
            spec = tasks[0]
            if not spec.get("title") or not spec.get("content"):
                return ToolOutput(
                    success=False,
                    error=f"Task {self._spec_label(spec)!r} missing required title/content",
                )
            result = await self._create_one(spec)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            return ToolOutput(success=True, data=result.task.brief())

        # Batch path — call add* one by one so we can capture per-task reasons
        # and return them to the LLM. The previous implementation routed
        # plain specs through add_batch() which silently dropped failures.
        created: list = []
        failures: list[dict] = []
        for spec in tasks:
            if not spec.get("title") or not spec.get("content"):
                failures.append(
                    {
                        "spec": self._spec_label(spec),
                        "reason": "missing required title/content",
                    }
                )
                continue
            result = await self._create_one(spec)
            if result.ok:
                created.append(result.task)
            else:
                failures.append(
                    {
                        "spec": self._spec_label(spec),
                        "reason": result.reason,
                    }
                )

        if not created and failures:
            joined = "; ".join(f"{f['spec']}: {f['reason']}" for f in failures)
            return ToolOutput(
                success=False,
                error=f"All {len(failures)} task creations failed: {joined}",
            )

        return ToolOutput(
            success=True,
            data={
                "tasks": [t.brief() for t in created],
                "count": len(created),
                "skipped": len(failures),
                "failures": failures,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        # Single task
        if "task_id" in d and "title" in d:
            return f"Task created: task_id={d['task_id']} title={d['title']}"
        # Batch
        tasks = d.get("tasks", [])
        lines = [f"task_id={t['task_id']} title={t['title']}" for t in tasks]
        lines.append(f"Created {d['count']}, skipped {d.get('skipped', 0)}")
        for f in d.get("failures", []) or []:
            lines.append(f"  - skipped {f['spec']}: {f['reason']}")
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
                    "enum": ["get", "list", "claimable"],
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

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action", "list")

        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="task_id required for get action")
            detail = await self.task_manager.get_task_detail(task_id=task_id)
            if detail:
                return ToolOutput(success=True, data=detail.model_dump(exclude_none=True))
            return ToolOutput(success=False, error="Task not found")

        if action == "claimable":
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
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("update_task", "add_blocked_by"),
                },
            },
            "required": ["task_id"],
        }

    async def _is_cancellable_assignee(self, assignee: str | None) -> bool:
        """Whether an assignee owns an execution process the team can cancel.

        Human-agent members are first-class team members but run no
        background process — cancel operations must skip all of them,
        otherwise the backend would try to stop something that never
        existed.
        """
        return bool(assignee) and not await self.agent_team.is_human_agent(assignee)

    async def _cancel_member_if_claimed(self, task_id: str) -> None:
        """Cancel the assignee if task is currently claimed.

        Skips human-agent members: they own no execution process to cancel.
        """
        task = await self.task_manager.get(task_id)
        if not task or task.status != TaskStatus.CLAIMED.value:
            return
        if await self._is_cancellable_assignee(task.assignee):
            await self.agent_team.cancel_member(member_name=task.assignee)

    async def _cancel_claimed_members(self) -> None:
        """Cancel all members who have claimed tasks.

        Skips human-agent members so a batch cancel does not try to
        cancel a member that has no execution process.
        """
        claimed_tasks = await self.task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        cancelled: set[str] = set()
        for task in claimed_tasks:
            if task.assignee in cancelled or not await self._is_cancellable_assignee(task.assignee):
                continue
            await self.agent_team.cancel_member(member_name=task.assignee)
            cancelled.add(task.assignee)

    async def _is_human_agent_locked(self, task) -> bool:
        """Whether a task is held by a human-agent member and therefore
        leader-immutable.

        The leader may not cancel or reassign such tasks — only the human
        collaborator can release them (by completing, or by the team
        being cleaned). The leader's only recourse is send_message nudges.
        """
        return await self.agent_team.is_human_agent(task.assignee) and task.status == TaskStatus.CLAIMED.value

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        status = inputs.get("status")
        title = inputs.get("title")
        content = inputs.get("content")
        assignee = inputs.get("assignee")
        add_blocked_by = inputs.get("add_blocked_by")

        # cancel_all: task_id="*" + status="cancelled"
        if task_id == "*" and status == "cancelled":
            await self._cancel_claimed_members()
            # Preserve every human-agent-claimed task in a single batch
            # cancel. Passing an empty set is fine — the backend treats
            # None and empty uniformly.
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
            await self._cancel_member_if_claimed(task_id)
            success = await self.agent_team.cancel_task(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to cancel task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

        # Collect all field updates in one pass
        updated: list[str] = []

        # Content update (title and/or content)
        if title or content:
            await self._cancel_member_if_claimed(task_id)
            result = await self.task_manager.update_task(task_id, title=title, content=content)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            if title:
                updated.append("title")
            if content:
                updated.append("content")

        # Assign task to member. When the task is already claimed by a
        # different member, treat this as a leader-driven reassignment:
        # cancel the previous claimer's execution, reset the task back to
        # PENDING, then assign to the new member. Same-member is idempotent.
        if assignee:
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
                await self.agent_team.cancel_member(member_name=task.assignee)
                reset_result = await self.task_manager.reset(task_id)
                if not reset_result.ok:
                    return ToolOutput(
                        success=False,
                        error=(
                            f"Failed to reset task before reassigning from "
                            f"{task.assignee} to {assignee}: {reset_result.reason}"
                        ),
                    )
            assign_result = await self.task_manager.assign(task_id, assignee)
            if not assign_result.ok:
                return ToolOutput(success=False, error=assign_result.reason)
            updated.append("assignee")

        # Add dependencies (blocked_by edges)
        if add_blocked_by:
            deps_result = await self.task_manager.add_dependencies(task_id, add_blocked_by)
            if not deps_result.ok:
                return ToolOutput(success=False, error=deps_result.reason)
            updated.append("blocked_by")

        if not updated:
            return ToolOutput(
                success=False, error="No update specified — provide status, title, content, assignee, or add_blocked_by"
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

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
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

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        status = inputs.get("status")

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        if status == "claimed":
            claim_result = await self.task_manager.claim(task_id=task_id)
            if not claim_result.ok:
                return ToolOutput(success=False, error=claim_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.CLAIMED.value}

        elif status == "completed":
            complete_result = await self.task_manager.complete(task_id=task_id)
            if not complete_result.ok:
                return ToolOutput(success=False, error=complete_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.COMPLETED.value}

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
    inheriting any of leader's coordination authority.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.member_complete_task",
                name="member_complete_task",
                description=t("member_complete_task"),
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

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
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

        note = (inputs.get("note") or "").strip() or None
        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "completed",
                "note": note,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to complete task"
        d = output.data
        line = f"Task #{d['task_id']} completed"
        if d.get("note"):
            line += f" (note: {d['note']})"
        return line
