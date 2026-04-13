# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Tools Module

This module provides tool wrappers for agent team functionality,
exposing team management, member management, task management,
and messaging capabilities as tools for agents to use.
"""
import json
from abc import ABC
from functools import wraps
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from pydantic import PrivateAttr

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import (
    TeamBackend,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import (
    Tool,
    ToolCard,
)
from openjiuwen.harness.tools.base_tool import ToolOutput


class MappedToolOutput(ToolOutput):
    """ToolOutput with custom string representation for LLM consumption.

    The ability_manager converts tool results to LLM messages via str(result).
    This subclass overrides __str__ to return model-optimized text instead of
    Pydantic's default representation.
    """

    _mapped_content: str = PrivateAttr(default="")

    @classmethod
    def from_output(cls, output: ToolOutput, mapped_content: str) -> "MappedToolOutput":
        """Create a MappedToolOutput from an existing ToolOutput."""
        obj = cls(success=output.success, data=output.data, error=output.error)
        obj._mapped_content = mapped_content
        return obj

    def __str__(self) -> str:
        return self._mapped_content


class TeamTool(Tool, ABC):
    """Base class for team tools with model-facing result mapping.

    Subclasses override map_result() to control what the LLM sees.
    Default implementation returns JSON for success, error text for failure.
    """

    def map_result(self, output: ToolOutput) -> str:
        """Map tool output to model-facing text.

        Override in subclasses for custom formatting. The returned string
        becomes the ToolMessage.content that the LLM receives.
        """
        if not output.success:
            return output.error or "Operation failed"
        if output.data is None:
            return "OK"
        return json.dumps(output.data, ensure_ascii=False)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError("TeamTool does not support streaming")


# ========== Tool Permission Sets ==========

# Tools that only the leader can use
LEADER_ONLY_TOOLS: Set[str] = {
    "build_team",              # Create a new team
    "clean_team",              # Clean up a team
    "spawn_member",            # Create a new team member
    "shutdown_member",         # Shutdown a team member
    # "approve_plan",            # Approve or reject a member's plan
    # "approve_tool",            # Approve or reject a teammate tool call
    "create_task",             # Create tasks (batch / with deps)
    "update_task",             # Update task content / cancel tasks
    "list_members",            # List all members
}

# Tools that only members can use
MEMBER_ONLY_TOOLS: Set[str] = {
    "claim_task",              # Claim or complete a task
    # Worktree tools — members work in isolated worktrees
    # "enter_worktree",          # Enter an isolated git worktree
    # "exit_worktree",           # Exit the current worktree session
}

# Tools that both leader and members can use
SHARED_TOOLS: Set[str] = {
    # Query tools
    # "get_team_info",           # Get team information
    # "get_member",              # Get member information

    "view_task",              # View tasks (unified - supports get/list/claimable)
    # Messaging tools
    "send_message",            # Send a message (point-to-point or broadcast)
    "workspace_meta",          # Workspace lock management and version history
}

# All tools available to leader
LEADER_TOOLS: Set[str] = LEADER_ONLY_TOOLS | SHARED_TOOLS

# All tools available to members
MEMBER_TOOLS: Set[str] = MEMBER_ONLY_TOOLS | SHARED_TOOLS


# ========== Team Management ==========

class BuildTeamTool(TeamTool):
    """Create a new team"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.build_team", name="build_team", description=t("build_team"))
        )
        self.team = team
        self.db = team.db
        self.messager = team.messager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": t("build_team", "display_name")},
                "team_desc": {"type": "string", "description": t("build_team", "team_desc")},
                "leader_display_name": {
                    "type": "string",
                    "description": t("build_team", "leader_display_name"),
                },
                "leader_desc": {"type": "string", "description": t("build_team", "leader_desc")},
            },
            "required": ["display_name", "team_desc", "leader_display_name", "leader_desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        display_name = inputs.get("display_name")
        leader_display_name = inputs["leader_display_name"]
        await self.team.build_team(
            display_name=display_name,
            desc=inputs.get("team_desc"),
            leader_display_name=leader_display_name,
            leader_desc=inputs["leader_desc"],
        )
        return ToolOutput(
            success=True,
            data={
                "team_name": self.team.team_name,
                "display_name": display_name,
                "leader_member_name": self.team.member_name,
                "leader_display_name": leader_display_name,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to build team"
        d = output.data or {}
        return (
            f"Team created: team_name={d.get('team_name')} "
            f"display_name={d.get('display_name')} "
            f"leader_member_name={d.get('leader_member_name')} "
            f"leader_display_name={d.get('leader_display_name')}"
        )


class CleanTeamTool(TeamTool):
    """Clean up a team when all members are shutdown"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.clean_team", name="clean_team", description=t("clean_team"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        try:
            team_name = self.team.team_name
            success = await self.team.clean_team()
            if not success:
                return ToolOutput(
                    success=False,
                    error="Active members remain. Use shutdown_member to close all members first.",
                )
            return ToolOutput(success=True, data={"team_name": team_name})
        except Exception as e:
            team_logger.error(f"clean_team failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to clean team"
        return f"Team cleaned: team_name={output.data['team_name']}"


# ========== Member Management ==========

class SpawnMemberTool(TeamTool):
    """Create a new team member"""

    def __init__(
        self, team: TeamBackend, t: Translator, *,
        model_config_allocator: Optional[Callable[[], Optional[str]]] = None,
    ):
        super().__init__(
            ToolCard(id="team.spawn_member", name="spawn_member", description=t("spawn_member"))
        )
        self.team = team
        self._allocate_model_config = model_config_allocator
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_member", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_member", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_member", "desc")},
                "prompt": {"type": "string", "description": t("spawn_member", "prompt")},
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.agent_teams.schema.status import MemberMode

        member_name = inputs.get("member_name")
        display_name = inputs.get("display_name")
        desc = inputs.get("desc", "")
        mode_str = self.team.teammate_mode.value
        mode = MemberMode(mode_str)

        member_model = self._allocate_model_config() if self._allocate_model_config else None

        card_id = f"{self.team.team_name}_{member_name}"
        agent_card = AgentCard(id=card_id, name=display_name, description=desc)
        success = await self.team.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=agent_card,
            desc=desc,
            prompt=inputs.get("prompt"),
            mode=mode,
            member_model=member_model,
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name, "display_name": display_name},
            error=None if success else "Failed to spawn member",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to spawn member"
        d = output.data
        return f"Member spawned: member_name={d['member_name']} display_name={d['display_name']}"


class ShutdownMemberTool(TeamTool):
    """Shutdown a team member"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.shutdown_member", name="shutdown_member", description=t("shutdown_member"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("shutdown_member", "member_name"),
                },
                "force": {"type": "boolean", "description": t("shutdown_member", "force")},
            },
            "required": ["member_name"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        success = await self.team.shutdown_member(
            member_name=member_name,
            force=inputs.get("force", False),
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name},
            error=None if success else "Failed to shutdown member",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to shutdown member"
        return f"Member shutdown: member_name={output.data['member_name']}"


class ApprovePlanTool(TeamTool):
    """Approve or reject a member's plan"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.approve_plan", name="approve_plan", description=t("approve_plan"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("approve_plan", "member_name"),
                },
                "approved": {"type": "boolean", "description": t("approve_plan", "approved")},
                "feedback": {"type": "string", "description": t("approve_plan", "feedback")},
            },
            "required": ["member_name", "approved"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        approved = inputs.get("approved")
        success = await self.team.approve_plan(
            member_name=member_name,
            approved=approved,
            feedback=inputs.get("feedback"),
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name, "approved": approved},
            error=None if success else "Failed to approve/reject plan",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject plan"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return f"Plan {decision}: member_name={d['member_name']} decision={decision}"


class ApproveToolCallTool(TeamTool):
    """Approve or reject one teammate tool call."""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.approve_tool", name="approve_tool", description=t("approve_tool"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("approve_tool", "member_name"),
                },
                "tool_call_id": {"type": "string", "description": t("approve_tool", "tool_call_id")},
                "approved": {"type": "boolean", "description": t("approve_tool", "approved")},
                "feedback": {"type": "string", "description": t("approve_tool", "feedback")},
                "auto_confirm": {"type": "boolean", "description": t("approve_tool", "auto_confirm")},
            },
            "required": ["member_name", "tool_call_id", "approved"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        tool_call_id = inputs.get("tool_call_id")
        approved = inputs.get("approved")
        success = await self.team.approve_tool(
            member_name=member_name,
            tool_call_id=tool_call_id,
            approved=approved,
            feedback=inputs.get("feedback"),
            auto_confirm=inputs.get("auto_confirm", False),
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name, "tool_call_id": tool_call_id, "approved": approved},
            error=None if success else "Failed to approve/reject tool call",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject tool call"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return (
            f"Tool call {decision}: tool_call_id={d['tool_call_id']} "
            f"member_name={d['member_name']} decision={decision}"
        )


class ListMembersTool(TeamTool):
    """List all team members"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.list_members", name="list_members", description=t("list_members"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        members = await self.team.list_members()
        return ToolOutput(
            success=True,
            data={"members": [member.model_dump() for member in members], "count": len(members)}
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list members"
        members = output.data["members"]
        if not members:
            return "No members"
        lines = [
            f"member_name={m['member_name']} display_name={m['display_name']} status={m['status']}"
            for m in members
        ]
        return "\n".join(lines)


# ========== Task Management ==========

class TaskCreateTool(TeamTool):
    """Create team tasks (Leader only).

    Unified creation: tasks with depended_by auto-route to add_with_priority(),
    plain tasks route to add() / add_batch().
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.create_task", name="create_task", description=t("create_task"))
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

    async def _create_single(self, spec: dict) -> ToolOutput:
        """Create one task, routing by presence of depended_by."""
        if spec.get("depended_by"):
            task = await self.task_manager.add_with_priority(
                title=spec["title"],
                content=spec["content"],
                task_id=spec.get("task_id"),
                dependencies=spec.get("depends_on"),
                dependent_task_ids=spec.get("depended_by"),
            )
        else:
            task = await self.task_manager.add(
                title=spec["title"],
                content=spec["content"],
                task_id=spec.get("task_id"),
                dependencies=spec.get("depends_on"),
            )
        if task:
            return ToolOutput(success=True, data=task.brief())
        return ToolOutput(success=False, error="Failed to create task (possibly circular dependency)")

    @staticmethod
    def _normalize(spec: dict) -> dict:
        """Map input schema keys to internal task_manager keys."""
        return {
            "title": spec.get("title"),
            "content": spec.get("content"),
            "task_id": spec.get("task_id"),
            "dependencies": spec.get("depends_on"),
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        if len(tasks) == 1:
            return await self._create_single(tasks[0])

        # Batch: separate priority tasks (with depended_by) from normal tasks
        priority_specs = [s for s in tasks if s.get("depended_by")]
        normal_specs = [s for s in tasks if not s.get("depended_by")]

        created = []
        if normal_specs:
            normalized = [self._normalize(s) for s in normal_specs]
            created.extend(await self.task_manager.add_batch(normalized))
        for spec in priority_specs:
            task = await self.task_manager.add_with_priority(
                title=spec["title"],
                content=spec["content"],
                task_id=spec.get("task_id"),
                dependencies=spec.get("depends_on"),
                dependent_task_ids=spec.get("depended_by"),
            )
            if task:
                created.append(task)

        return ToolOutput(
            success=True,
            data={
                "tasks": [t.brief() for t in created],
                "count": len(created),
                "skipped": len(tasks) - len(created),
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
        return "\n".join(lines)


class ViewTaskToolV2(TeamTool):
    """Unified task viewing tool (V2).

    Explicit action enum instead of implicit param-based dispatch.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(id="team.view_task", name="view_task", description=t("view_task"))
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
        """Map view_task result — tiered output by action."""
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        # Detail view (get action) — mirrors TaskGetTool
        if "content" in d:
            lines = [
                f"Task #{d['task_id']}: {d['title']}",
                f"Status: {d['status']}",
                f"Content: {d['content']}",
            ]
            if d.get("assignee"):
                lines.append(f"Assignee: {d['assignee']}")
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
        for t in tasks:
            parts = [f"#{t['task_id']} [{t['status']}] {t['title']}"]
            if t.get("assignee"):
                parts.append(f"({t['assignee']})")
            if t.get("blocked_by"):
                parts.append(f"[blocked by {', '.join(f'#{tid}' for tid in t['blocked_by'])}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)


class UpdateTaskTool(TeamTool):
    """Update task content or cancel tasks (Leader only)."""

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.update_task", name="update_task", description=t("update_task"))
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
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

    async def _cancel_member_if_claimed(self, task_id: str) -> None:
        """Cancel the assignee if task is currently claimed."""
        task = await self.task_manager.get(task_id)
        if task and task.status == TaskStatus.CLAIMED.value and task.assignee:
            await self.agent_team.cancel_member(member_name=task.assignee)

    async def _cancel_claimed_members(self) -> None:
        """Cancel all members who have claimed tasks."""
        claimed_tasks = await self.task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        cancelled: set[str] = set()
        for task in claimed_tasks:
            if task.assignee and task.assignee not in cancelled:
                await self.agent_team.cancel_member(member_name=task.assignee)
                cancelled.add(task.assignee)

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
            count = await self.agent_team.cancel_all_tasks()
            return ToolOutput(success=True, data={"cancelled_count": count})

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        # Cancel single task
        if status == "cancelled":
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
            success = await self.task_manager.update_task(task_id, title=title, content=content)
            if not success:
                return ToolOutput(success=False, error="Failed to update task")
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
                await self.agent_team.cancel_member(member_name=task.assignee)
                reset_ok = await self.task_manager.reset(task_id)
                if not reset_ok:
                    return ToolOutput(
                        success=False,
                        error=f"Failed to reset task before reassigning from {task.assignee} to {assignee}",
                    )
            success = await self.task_manager.assign(task_id, assignee)
            if not success:
                return ToolOutput(success=False, error=f"Failed to assign task (member not found or invalid status)")
            updated.append("assignee")

        # Add dependencies (blocked_by edges)
        if add_blocked_by:
            success = await self.task_manager.add_dependencies(task_id, add_blocked_by)
            if not success:
                return ToolOutput(success=False, error="Failed to add dependencies")
            updated.append("blocked_by")

        if not updated:
            return ToolOutput(success=False, error="No update specified — "
                                                   "provide status, title, content, assignee, or add_blocked_by")

        return ToolOutput(success=True, data={
            "task_id": task_id,
            "status": "updated",
            "updated_fields": updated,
        })

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "cancelled_count" in d:
            return f"Cancelled {d['cancelled_count']} tasks"
        return f"Task #{d['task_id']} {d['status']}"


class ClaimTaskTool(TeamTool):
    """Claim or complete a task (Teammate only)."""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(id="team.claim_task", name="claim_task", description=t("claim_task"))
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
            success = await self.task_manager.claim(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to claim task")
            status_change = {"from": task.status, "to": TaskStatus.CLAIMED.value}

        elif status == "completed":
            success = await self.task_manager.complete(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to complete task")
            status_change = {"from": task.status, "to": TaskStatus.COMPLETED.value}

        else:
            return ToolOutput(success=False, error=f"Invalid status: {status}")

        return ToolOutput(success=True, data={
            "task_id": task_id,
            "updated_fields": ["status"],
            "status_change": status_change,
        })

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


# ========== Messaging ==========

class SendMessageTool(TeamTool):
    """Send a message to team members (point-to-point or broadcast)."""

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            ToolCard(id="team.send_message", name="send_message", description=t("send_message"))
        )
        self.message_manager = message_manager
        self._team = team
        self._on_teammate_created = on_teammate_created
        self.card.input_params = {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": t("send_message", "to")},
                "content": {"type": "string", "description": t("send_message", "content")},
                "summary": {"type": "string", "description": t("send_message", "summary")},
            },
            "required": ["to", "content"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        to = inputs.get("to", "").strip()
        content = inputs.get("content", "").strip()
        summary = inputs.get("summary", "").strip()

        if not to:
            return ToolOutput(success=False, error="'to' is required")
        if not content:
            return ToolOutput(success=False, error="'content' is required")

        try:
            if to == "*":
                return await self._broadcast(content, summary)
            return await self._send(to, content, summary)
        except Exception as e:
            team_logger.error(f"send_message failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

    async def _broadcast(self, content: str, summary: str) -> ToolOutput:
        await self._auto_start_members()
        msg_id = await self.message_manager.broadcast_message(content=content)
        if not msg_id:
            return ToolOutput(success=False, error="Failed to broadcast message")
        return ToolOutput(success=True, data={
            "type": "broadcast",
            "from": self.message_manager.member_name,
            "summary": summary or None,
        })

    async def _send(self, to: str, content: str, summary: str) -> ToolOutput:
        # "user" is the pseudo-member representing the human caller; skip
        # roster validation so teammates can reply through the same tool.
        if self._team and to != "user":
            member = await self._team.get_member(to)
            if not member:
                return ToolOutput(success=False, error=f"Member '{to}' not found")
        await self._auto_start_members()
        msg_id = await self.message_manager.send_message(content=content, to_member_name=to)
        if not msg_id:
            return ToolOutput(success=False, error=f"Failed to send message to '{to}'")
        return ToolOutput(success=True, data={
            "type": "message",
            "from": self.message_manager.member_name,
            "to": to,
            "summary": summary or None,
        })

    async def _auto_start_members(self) -> None:
        """Auto-start unstarted members if leader with startup callback."""
        if self._team and self._on_teammate_created and self._team.is_leader:
            started = await self._team.startup(on_created=self._on_teammate_created)
            if started:
                team_logger.info(f"Auto-started members: {started}")

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to send message"
        d = output.data
        if d["type"] == "broadcast":
            return f"Broadcast sent from {d['from']}"
        return f"Message sent from {d['from']} to {d['to']}"


# ========== Tool Factory ==========


def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
    model_config_allocator: Optional[Callable[[], Optional[str]]] = None,
    exclude_tools: Optional[Set[str]] = None,
    lang: str = "cn",
) -> List[Tool]:
    """Create role-appropriate tool instances filtered by permission sets.

    Args:
        role: "leader" or "teammate".
        agent_team: AgentTeam instance providing task/message/db/messager.
        on_teammate_created: Callback invoked when a teammate is created.
        model_config_allocator: Callback that returns the next model config JSON.
        exclude_tools: Tool names to exclude from the allowed set.
        lang: Locale code ("cn" or "en") for tool descriptions.
    """
    from openjiuwen.agent_teams.tools.locales import make_translator

    t = make_translator(lang)
    task_mgr = agent_team.task_manager
    msg_mgr = agent_team.message_manager

    all_tools = {
        # Team management
        "build_team": BuildTeamTool(agent_team, t),
        "clean_team": CleanTeamTool(agent_team, t),
        # Member management
        "spawn_member": SpawnMemberTool(agent_team, t, model_config_allocator=model_config_allocator),
        "shutdown_member": ShutdownMemberTool(agent_team, t),
        "approve_plan": ApprovePlanTool(agent_team, t),
        "approve_tool": ApproveToolCallTool(agent_team, t),
        "list_members": ListMembersTool(agent_team, t),
        # Task management
        "create_task": TaskCreateTool(agent_team, t),
        "update_task": UpdateTaskTool(agent_team, t),
        "view_task": ViewTaskToolV2(task_mgr, t),
        "claim_task": ClaimTaskTool(task_mgr, t),
        # Messaging
        "send_message": SendMessageTool(
            msg_mgr, t, team=agent_team, on_teammate_created=on_teammate_created,
        ),
    }

    allowed = LEADER_TOOLS if role == "leader" else MEMBER_TOOLS
    if exclude_tools:
        allowed = allowed - exclude_tools
    tools = [
        tool for name, tool in all_tools.items()
        if name in allowed
    ]

    for tool in tools:
        _wrap_invoke_with_logging(tool)

    return tools


def _wrap_invoke_with_logging(tool: Tool) -> None:
    """Wrap a tool's invoke method with debug logging and result mapping.

    For TeamTool instances, the wrapper also calls map_result() to produce
    a MappedToolOutput whose __str__ returns model-optimized text.
    """
    original_invoke = tool.invoke
    tool_name = tool.card.name
    is_team_tool = isinstance(tool, TeamTool)

    @wraps(original_invoke)
    async def logged_invoke(inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        team_logger.debug(f"[{tool_name}] invoke start, inputs={inputs}")
        result = await original_invoke(inputs, **kwargs)
        team_logger.debug(f"[{tool_name}] invoke end, output={result}")
        if is_team_tool:
            mapped = tool.map_result(result)  # type: ignore[union-attr]
            return MappedToolOutput.from_output(result, mapped)
        return result

    tool.invoke = logged_invoke
