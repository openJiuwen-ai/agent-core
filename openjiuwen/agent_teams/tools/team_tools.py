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

from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.status import TaskStatus
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
    "send_message",            # Send a point-to-point message
    "broadcast_message",       # Send a broadcast message
}

# All tools available to leader
LEADER_TOOLS: Set[str] = LEADER_ONLY_TOOLS | SHARED_TOOLS

# All tools available to members
MEMBER_TOOLS: Set[str] = MEMBER_ONLY_TOOLS | SHARED_TOOLS


# ========== Team Management ==========

class BuildTeamTool(TeamTool):
    """Create a new team"""

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="BuildTeamTool", name="build_team", description="组建团队，设置团队名称和协作目标。"
                                                                        "这是启动协作的第一步，必须在 spawn_member 和"
                                                                        "add_task 之前调用")
        )
        self.team = team
        self.db = team.db
        self.messager = team.messager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "团队名称，体现团队职能方向"},
                "desc": {"type": "string", "description": "团队协作目标和任务范围的描述"},
                "prompt": {"type": "string", "description": "团队级别的全局协作提示词，所有成员可见"},
                "leader_name": {"type": "string", "description": "Leader 的显示名称"},
                "leader_desc": {"type": "string", "description": "Leader 的人设描述（专业背景、领域专长）"},
            },
            "required": ["name", "desc", "leader_name", "leader_desc"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        team_name = inputs.get("name")
        await self.team.build_team(
            name=team_name,
            desc=inputs.get("desc"),
            prompt=inputs.get("prompt"),
            leader_name=inputs["leader_name"],
            leader_desc=inputs["leader_desc"],
        )
        return ToolOutput(success=True)


class CleanTeamTool(TeamTool):
    """Clean up a team when all members are shutdown"""

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="CleanTeamTool", name="clean_team", description="解散团队并清理所有资源。前置条件："
                                                                        "所有成员已通过 shutdown_member 关闭。"
                                                                        "在所有任务完成、结果汇总后调用")
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        success = await self.team.clean_team()
        return ToolOutput(
            success=success,
            error=None if success else "Failed to clean team"
        )


# ========== Member Management ==========

class SpawnMemberTool(TeamTool):
    """Create a new team member"""

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="SpawnMemberTool", name="spawn_member", description="按领域专长创建新的团队成员。"
                                                                            "每个成员应有明确的人设和专业方向，"
                                                                            "用于领取并执行匹配领域的任务。"
                                                                            "创建后成员处于未启动状态，"
                                                                            "需调用 startup_members 统一拉起")
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "成员唯一标识符，建议使用有语义的 ID（如 backend-dev-1）"},
                "name": {"type": "string", "description": "成员名称，体现其角色定位（如「后端开发专家」）"},
                "desc": {"type": "string", "description": "成员的人设描述，包括专业背景、领域专长、行为风格和工作方式，用于任务匹配和角色定位"},
                "prompt": {"type": "string", "description": "成员的启动指令。应引导成员通过 view_task 工具查看任务列表，认领任务"},
                # "mode": {"type": "string", "enum": ["plan_mode", "build_mode"], "description": "成员模式。plan_mode:
                # 需要leader审批任务才能完成（默认）；build_mode: 可以直接完成任务"},
            },
            "required": ["member_id", "name", "desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.agent_teams.tools.status import MemberMode

        member_id = inputs.get("member_id")
        name = inputs.get("name")
        desc = inputs.get("desc", "")
        # mode_str = inputs.get("mode", self.team.teammate_mode.value)
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

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="ShutdownMemberTool", name="shutdown_member", description="关闭团队成员并释放资源。"
                                                                                  "在成员完成所有任务后调用；"
                                                                                  "若成员持续无法交付，可强制关闭")
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "要关闭的成员ID"},
                "force": {"type": "boolean", "description": "是否强制关闭（忽略未完成任务），默认 false"},
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

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="ApprovePlanTool", name="approve_plan", description="审批或拒绝成员提交的执行计划。"
                                                                            "审核计划是否符合目标要求，给出反馈指导成员调整")
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "提交计划的成员ID"},
                "approved": {"type": "boolean", "description": "true 批准计划，false 拒绝并要求修改"},
                "feedback": {"type": "string", "description": "审批反馈，拒绝时应说明原因和修改方向"},
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

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(
                id="ApproveToolCallTool",
                name="approve_tool",
                description=(
                    "审批或拒绝 teammate 被 rail 中断的工具调用。"
                    "收到工具审批请求后，Leader 应调用此工具反馈 approved、feedback 和 auto_confirm。"
                ),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "发起工具审批请求的成员 ID"},
                "tool_call_id": {"type": "string", "description": "待恢复的中断 tool_call_id"},
                "approved": {"type": "boolean", "description": "是否批准这次工具调用"},
                "feedback": {"type": "string", "description": "可选审批反馈"},
                "auto_confirm": {"type": "boolean", "description": "后续同名工具是否自动批准"},
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

    def __init__(self, team: TeamBackend):
        super().__init__(
            ToolCard(id="ListMembersTool", name="list_members", description="列出所有团队成员及其状态，"
                                                                            "用于评估团队人员构成和是否需要创建新成员")
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

    def __init__(self, agent_team: TeamBackend):
        super().__init__(
            ToolCard(
                id="TaskManagerToolV2",
                name="task_manager",
                description=(
                    "团队任务管理工具（仅Leader可用）。"
                    "action: add（添加）、insert（插入已有DAG）、update（更新）、cancel（取消）、cancel_all（全部取消）。"
                    "add 时通过 tasks 数组传入，支持单个或批量；"
                    "insert 用于将任务插入已有 DAG 中间，支持 depended_by 反向依赖"
                ),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager

        _task_schema: dict = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "自定义任务ID，便于依赖引用"},
                "title": {"type": "string", "description": "任务标题，简明描述任务目标"},
                "content": {"type": "string", "description": "任务详细内容，包含执行说明和验收标准"},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "前置依赖的任务ID列表",
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
                    "description": "操作类型，默认 add",
                },
                # add
                "tasks": {
                    "type": "array",
                    "items": _task_schema,
                    "description": "add 时传入的任务列表（单个任务也用数组包裹）",
                },
                # insert / update / cancel
                "task_id": {"type": "string", "description": "任务ID（insert 时为自定义ID，update/cancel 时为目标任务ID）"},
                "title": {"type": "string", "description": "任务标题（insert/update 使用）"},
                "content": {"type": "string", "description": "任务内容（insert/update 使用）"},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "insert 时的前置依赖任务ID列表",
                },
                "depended_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "insert 时需要等待本任务完成的现有任务ID列表（反向依赖）",
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

    def __init__(self, task_manager: TeamTaskManager):
        super().__init__(
            ToolCard(
                id="ViewTaskToolV2",
                name="view_task",
                description=(
                    "查看任务信息。"
                    "action: get（单个任务详情）、list（按状态列出任务）、claimable（默认，可认领任务）"
                ),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "claimable"],
                    "description": "查看模式，默认 claimable",
                },
                "task_id": {"type": "string", "description": "get 时的任务ID"},
                "status": {
                    "type": "string",
                    "description": "list 时按状态过滤：pending/claimed/plan_approved/completed/cancelled/blocked",
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

    def __init__(self, task_manager: TeamTaskManager):
        super().__init__(
            ToolCard(id="ClaimTaskTool", name="claim_task", description="领取一个就绪任务。"
                                                                        "只能领取 pending 状态且无人认领的任务，"
                                                                        "应选择匹配自己领域专长的任务")
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要领取的任务ID，需为 pending 状态"},
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

    def __init__(self, task_manager: TeamTaskManager):
        super().__init__(
            ToolCard(id="CompleteTaskTool", name="complete_task", description="标记任务完成。"
                                                                              "完成后会自动解锁依赖本任务的下游任务，"
                                                                              "使其变为 pending 可领取状态。"
                                                                              "调用后应通过 send_message 向 Leader 汇报"
                                                                              "结果摘要")
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要标记完成的任务ID，需为自己已领取（claimed）的任务"},
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
    """Send a point-to-point message"""

    def __init__(self, message_manager: TeamMessageManager):
        super().__init__(
            ToolCard(id="SendMessageTool", name="send_message", description="向指定成员发送点对点消息。用于通知成员领取任务、"
                                                                            "回复进度汇报、升级阻塞问题或协调成员间依赖")
        )
        self.message_manager = message_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "消息内容，应包含明确的行动指引或信息"},
                "to_member": {"type": "string", "description": "接收者的成员ID"},
            },
            "required": ["content", "to_member"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        message_id = await self.message_manager.send_message(
            content=inputs.get("content"),
            to_member=inputs.get("to_member"),
        )
        if message_id:
            return ToolOutput(success=True)
        return ToolOutput(success=False, error="Failed to send message")


class BroadcastMessageTool(TeamTool):
    """Send a broadcast message"""

    def __init__(
        self,
        message_manager: TeamMessageManager,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            ToolCard(id="BroadcastMessageTool", name="broadcast_message", description="向所有团队成员广播消息。"
                                                                                      "用于宣布全局决策、"
                                                                                      "约束变更或需要所有人知晓的信息")
        )
        self.message_manager = message_manager
        self._team = team
        self._on_teammate_created = on_teammate_created
        self.card.input_params = {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "广播内容，所有成员都会收到"},
            },
            "required": ["content"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        # Leader broadcast: start all unstarted members first
        if self._team and self._on_teammate_created and self._team.is_leader:
            started = await self._team.startup(on_created=self._on_teammate_created)
            if started:
                team_logger.info(f"Auto-started members before broadcast: {started}")

        message_id = await self.message_manager.broadcast_message(
            content=inputs.get("content"),
        )
        if message_id:
            return ToolOutput(success=True)
        return ToolOutput(success=False, error="Failed to broadcast message")


# ========== Tool Factory ==========


def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
    exclude_tools: Optional[Set[str]] = None,
) -> List[Tool]:
    """Create role-appropriate tool instances filtered by permission sets.

    Args:
        role: "leader" or "teammate".
        agent_team: AgentTeam instance providing task/message/db/messager.
        exclude_tools: Tool names to exclude from the allowed set.
    """
    task_mgr = agent_team.task_manager
    msg_mgr = agent_team.message_manager

    all_tools = {
        # Team management
        "build_team": BuildTeamTool(agent_team),
        "clean_team": CleanTeamTool(agent_team),
        # Member management
        "spawn_member": SpawnMemberTool(agent_team),
        "shutdown_member": ShutdownMemberTool(agent_team),
        "approve_plan": ApprovePlanTool(agent_team),
        "approve_tool": ApproveToolCallTool(agent_team),
        "list_members": ListMembersTool(agent_team),
        # Task management
        "task_manager": TaskManagerToolV2(agent_team),
        "view_task": ViewTaskToolV2(task_mgr),
        "claim_task": ClaimTaskTool(task_mgr),
        "complete_task": CompleteTaskTool(task_mgr),
        # Messaging
        "send_message": SendMessageTool(msg_mgr),
        "broadcast_message": BroadcastMessageTool(msg_mgr, team=agent_team, on_teammate_created=on_teammate_created),
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
