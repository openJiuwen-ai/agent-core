# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Tools Module

This module provides tool wrappers for agent team functionality,
exposing team management, member management, task management,
and messaging capabilities as tools for agents to use.
"""
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


class TeamTool(Tool, ABC):
    """Base class for team tools that provides a default no-op stream implementation."""

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError("TeamTool does not support streaming")


# ========== Tool Permission Sets ==========

# Tools that only the leader can use
LEADER_ONLY_TOOLS: Set[str] = {
    "build_team",              # Create a new team
    "clean_team",              # Clean up a team
    "spawn_member",            # Create a new team member
    "shutdown_member",         # Shutdown a team member
    "approve_plan",            # Approve or reject a member's plan
    "approve_tool",            # Approve or reject a teammate tool call
    "task_manager",            # Manager task (unified - supports:
                               # add single/batch/priority/top task or cancel/cancel_all/update task)
}

# Tools that only members can use
MEMBER_ONLY_TOOLS: Set[str] = {
    "claim_task",              # Claim a task for a member
    "complete_task",           # Complete a task
}

# Tools that both leader and members can use
SHARED_TOOLS: Set[str] = {
    # Query tools
    # "get_team_info",           # Get team information
    # "get_member",              # Get member information
    "list_members",            # List all members

    "view_task",              # View tasks (unified - supports get/list/claimable)
    # Messaging tools
    "send_message",            # Send a message (point-to-point or broadcast)
    # Worktree tools
    "enter_worktree",          # Enter an isolated git worktree
    "exit_worktree",           # Exit the current worktree session
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
                "team_name": {"type": "string", "description": t("build_team", "team_name")},
                "team_desc": {"type": "string", "description": t("build_team", "team_desc")},
                "leader_name": {"type": "string", "description": t("build_team", "leader_name")},
                "leader_desc": {"type": "string", "description": t("build_team", "leader_desc")},
            },
            "required": ["team_name", "team_desc", "leader_name", "leader_desc"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        team_name = inputs.get("team_name")
        await self.team.build_team(
            name=team_name,
            desc=inputs.get("team_desc"),
            leader_name=inputs["leader_name"],
            leader_desc=inputs["leader_desc"],
        )
        return ToolOutput(success=True)


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
            team_id = self.team.team_id
            success = await self.team.clean_team()
            if not success:
                return ToolOutput(
                    success=False,
                    error="Active members remain. Use shutdown_member to close all members first.",
                )
            return ToolOutput(success=True, data={"team_id": team_id})
        except Exception as e:
            team_logger.error(f"clean_team failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")


# ========== Member Management ==========

class SpawnMemberTool(TeamTool):
    """Create a new team member"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.spawn_member", name="spawn_member", description=t("spawn_member"))
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": t("spawn_member", "member_id")},
                "name": {"type": "string", "description": t("spawn_member", "name")},
                "desc": {"type": "string", "description": t("spawn_member", "desc")},
                "prompt": {"type": "string", "description": t("spawn_member", "prompt")},
            },
            "required": ["member_id", "name", "desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.agent_teams.schema.status import MemberMode

        member_id = inputs.get("member_id")
        name = inputs.get("name")
        desc = inputs.get("desc", "")
        mode_str = self.team.teammate_mode.value
        mode = MemberMode(mode_str)

        agent_card = AgentCard(id=member_id, name=name, description=desc)
        success = await self.team.spawn_member(
            member_id=member_id,
            name=name,
            agent_card=agent_card,
            desc=desc,
            prompt=inputs.get("prompt"),
            mode=mode,
        )
        return ToolOutput(
            success=success,
            error=None if success else "Failed to spawn member"
        )


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
                "member_id": {"type": "string", "description": t("shutdown_member", "member_id")},
                "force": {"type": "boolean", "description": t("shutdown_member", "force")},
            },
            "required": ["member_id"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.team.shutdown_member(
            member_id=inputs.get("member_id"),
            force=inputs.get("force", False)
        )
        return ToolOutput(
            success=success,
            error=None if success else "Failed to shutdown member"
        )


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
                "member_id": {"type": "string", "description": t("approve_plan", "member_id")},
                "approved": {"type": "boolean", "description": t("approve_plan", "approved")},
                "feedback": {"type": "string", "description": t("approve_plan", "feedback")},
            },
            "required": ["member_id", "approved"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.team.approve_plan(
            member_id=inputs.get("member_id"),
            approved=inputs.get("approved"),
            feedback=inputs.get("feedback")
        )
        return ToolOutput(
            success=success,
            error=None if success else "Failed to approve/reject plan"
        )


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
                "member_id": {"type": "string", "description": t("approve_tool", "member_id")},
                "tool_call_id": {"type": "string", "description": t("approve_tool", "tool_call_id")},
                "approved": {"type": "boolean", "description": t("approve_tool", "approved")},
                "feedback": {"type": "string", "description": t("approve_tool", "feedback")},
                "auto_confirm": {"type": "boolean", "description": t("approve_tool", "auto_confirm")},
            },
            "required": ["member_id", "tool_call_id", "approved"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.team.approve_tool(
            member_id=inputs.get("member_id"),
            tool_call_id=inputs.get("tool_call_id"),
            approved=inputs.get("approved"),
            feedback=inputs.get("feedback"),
            auto_confirm=inputs.get("auto_confirm", False),
        )
        return ToolOutput(
            success=success,
            error=None if success else "Failed to approve/reject tool call",
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


# ========== Task Management ==========

class TaskManagerToolV2(TeamTool):
    """Unified task management tool (V2).

    Inspired by TeamTasksTool — flat action enum, tasks as structured array,
    priority per-task instead of top-level, no mode param.
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(id="team.task_manager", name="task_manager", description=t("task_manager"))
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager

        _task_schema: dict = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("task_manager", "task.task_id")},
                "title": {"type": "string", "description": t("task_manager", "task.title")},
                "content": {"type": "string", "description": t("task_manager", "task.content")},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("task_manager", "task.depends_on"),
                },
            },
            "required": ["title", "content"],
        }

        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "insert", "update", "cancel", "cancel_all"],
                    "description": t("task_manager", "action"),
                },
                # add
                "tasks": {
                    "type": "array",
                    "items": _task_schema,
                    "description": t("task_manager", "tasks"),
                },
                # insert / update / cancel
                "task_id": {"type": "string", "description": t("task_manager", "task_id")},
                "title": {"type": "string", "description": t("task_manager", "title")},
                "content": {"type": "string", "description": t("task_manager", "content")},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("task_manager", "depends_on"),
                },
                "depended_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("task_manager", "depended_by"),
                },
            },
            "required": [],
        }

    async def _cancel_member_if_claimed(self, task_id: str) -> None:
        """Cancel the assignee if task is currently claimed."""
        task = await self.task_manager.get(task_id)
        if task and task.status == TaskStatus.CLAIMED.value and task.assignee:
            await self.agent_team.cancel_member(member_id=task.assignee)

    async def _cancel_claimed_members(self) -> None:
        """Cancel all members who have claimed tasks."""
        claimed_tasks = await self.task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        cancelled = set()
        for task in claimed_tasks:
            if task.assignee and task.assignee not in cancelled:
                await self.agent_team.cancel_member(member_id=task.assignee)
                cancelled.add(task.assignee)

    async def _add_single(self, task_spec: dict) -> ToolOutput:
        """Add one task."""
        task = await self.task_manager.add(
            title=task_spec.get("title"),
            content=task_spec.get("content"),
            task_id=task_spec.get("task_id"),
            dependencies=task_spec.get("depends_on"),
        )
        if task:
            return ToolOutput(success=True, data=task.brief())
        return ToolOutput(success=False, error="Failed to add task")

    @staticmethod
    def _normalize_task_spec(spec: dict) -> dict:
        """Map V2 schema keys to internal task_manager keys for add_batch."""
        return {
            "title": spec.get("title"),
            "content": spec.get("content"),
            "task_id": spec.get("task_id"),
            "dependencies": spec.get("depends_on"),
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action", "add")

        if action == "cancel_all":
            await self._cancel_claimed_members()
            count = await self.agent_team.cancel_all_tasks()
            return ToolOutput(success=True, data={"cancelled_count": count})

        if action == "cancel":
            task_id = inputs.get("task_id")
            await self._cancel_member_if_claimed(task_id)
            success = await self.agent_team.cancel_task(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to cancel task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

        if action == "update":
            task_id = inputs.get("task_id")
            await self._cancel_member_if_claimed(task_id)
            success = await self.task_manager.update_task(
                task_id=task_id, title=inputs.get("title"), content=inputs.get("content"),
            )
            if not success:
                return ToolOutput(success=False, error="Failed to update task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "updated"})

        if action == "insert":
            task = await self.task_manager.add_with_priority(
                title=inputs.get("title"),
                content=inputs.get("content"),
                task_id=inputs.get("task_id"),
                dependencies=inputs.get("depends_on"),
                dependent_task_ids=inputs.get("depended_by"),
            )
            if task:
                return ToolOutput(success=True, data=task.brief())
            return ToolOutput(success=False, error="Failed to insert task (possibly circular dependency)")

        # action == "add"
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="No tasks provided")

        if len(tasks) == 1:
            return await self._add_single(tasks[0])

        # Batch: normalize keys then delegate
        normalized = [self._normalize_task_spec(t) for t in tasks]
        created = await self.task_manager.add_batch(normalized)
        return ToolOutput(
            success=True,
            data={
                "tasks": [t.brief() for t in created],
                "count": len(created),
                "skipped": len(tasks) - len(created),
            },
        )


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
        action = inputs.get("action", "claimable")

        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="task_id required for get action")
            result = await self.task_manager.get(task_id=task_id)
            if result:
                return ToolOutput(success=True, data=result.model_dump())
            return ToolOutput(success=False, error="Task not found")

        if action == "list":
            result = await self.task_manager.list_tasks(status=inputs.get("status"))
        else:
            result = await self.task_manager.get_claimable_tasks()

        return ToolOutput(
            success=True,
            data={"tasks": [t.model_dump() for t in result], "count": len(result)},
        )


class ClaimTaskTool(TeamTool):
    """Claim a task for a member"""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(id="team.claim_task", name="claim_task", description=t("claim_task"))
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("claim_task", "task_id")},
            },
            "required": ["task_id"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.task_manager.claim(task_id=inputs.get("task_id"))
        return ToolOutput(
            success=success,
            error=None if success else "Failed to claim task"
        )


class CompleteTaskTool(TeamTool):
    """Complete a task"""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(id="team.complete_task", name="complete_task", description=t("complete_task"))
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("complete_task", "task_id")},
            },
            "required": ["task_id"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.task_manager.complete(task_id=inputs.get("task_id"))
        return ToolOutput(
            success=success,
            error=None if success else "Failed to complete task"
        )


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
            "from": self.message_manager.member_id,
            "summary": summary or None,
        })

    async def _send(self, to: str, content: str, summary: str) -> ToolOutput:
        if self._team:
            member = await self._team.get_member(to)
            if not member:
                return ToolOutput(success=False, error=f"Member '{to}' not found")
        await self._auto_start_members()
        msg_id = await self.message_manager.send_message(content=content, to_member=to)
        if not msg_id:
            return ToolOutput(success=False, error=f"Failed to send message to '{to}'")
        return ToolOutput(success=True, data={
            "type": "message",
            "from": self.message_manager.member_id,
            "to": to,
            "summary": summary or None,
        })

    async def _auto_start_members(self) -> None:
        """Auto-start unstarted members if leader with startup callback."""
        if self._team and self._on_teammate_created and self._team.is_leader:
            started = await self._team.startup(on_created=self._on_teammate_created)
            if started:
                team_logger.info(f"Auto-started members: {started}")


# ========== Tool Factory ==========


def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
    exclude_tools: Optional[Set[str]] = None,
    lang: str = "cn",
) -> List[Tool]:
    """Create role-appropriate tool instances filtered by permission sets.

    Args:
        role: "leader" or "teammate".
        agent_team: AgentTeam instance providing task/message/db/messager.
        on_teammate_created: Callback invoked when a teammate is created.
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
        "spawn_member": SpawnMemberTool(agent_team, t),
        "shutdown_member": ShutdownMemberTool(agent_team, t),
        "approve_plan": ApprovePlanTool(agent_team, t),
        "approve_tool": ApproveToolCallTool(agent_team, t),
        "list_members": ListMembersTool(agent_team, t),
        # Task management
        "task_manager": TaskManagerToolV2(agent_team, t),
        "view_task": ViewTaskToolV2(task_mgr, t),
        "claim_task": ClaimTaskTool(task_mgr, t),
        "complete_task": CompleteTaskTool(task_mgr, t),
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
    """Wrap a tool's invoke method with debug logging for inputs and outputs."""
    original_invoke = tool.invoke
    tool_name = tool.card.name

    @wraps(original_invoke)
    async def logged_invoke(inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        team_logger.debug(f"[{tool_name}] invoke start, inputs={inputs}")
        result = await original_invoke(inputs, **kwargs)
        team_logger.debug(f"[{tool_name}] invoke end, output={result}")
        return result

    tool.invoke = logged_invoke
