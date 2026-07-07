# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-only tools for organization-level collaboration."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.organization.schema import (
    OrgTaskCreator,
    OrgTaskOutputContext,
    OrgTaskOutputSpec,
)
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class _OrgLeaderTool(TeamTool):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        manager: OrgTaskManager,
        team_id: str,
        leader_id: str,
    ) -> None:
        super().__init__(ToolCard(id=f"team_org.{name}", name=name, description=description))
        self.manager = manager
        self.team_id = team_id
        self.leader_id = leader_id

    async def _ensure_registered(self) -> None:
        await self.manager.register_leader(
            team_id=self.team_id,
            leader_id=self.leader_id,
            leader_member_name=self.leader_id,
        )


class OrgViewTasksTool(_OrgLeaderTool):
    """View organization-level tasks for LLM claim/delegate decisions."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_view_tasks",
            description="View organization-level tasks and leader messages.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "open", "assigned", "get", "messages"],
                    "description": "What to view.",
                },
                "task_id": {"type": "string", "description": "Required for action=get."},
                "status": {"type": "string", "description": "Optional task status filter for action=list."},
                "limit": {"type": "integer", "description": "Maximum number of rows to return."},
            },
            "required": ["action"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        action = inputs.get("action")
        limit = int(inputs.get("limit") or 50)
        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="'task_id' is required for action=get")
            task = await self.manager.get_task(task_id)
            if task is None:
                return ToolOutput(success=False, error=f"org task not found: {task_id}")
            return ToolOutput(success=True, data=task.model_dump())
        if action == "open":
            tasks = await self.manager.list_open_tasks(limit=limit)
            return ToolOutput(success=True, data={"tasks": [task.brief() for task in tasks]})
        if action == "assigned":
            tasks = await self.manager.list_tasks_for_team(self.team_id, include_open=False, limit=limit)
            return ToolOutput(success=True, data={"tasks": [task.brief() for task in tasks]})
        if action == "messages":
            messages = await self.manager.list_leader_messages(team_id=self.team_id, limit=limit)
            return ToolOutput(success=True, data={"messages": messages})
        if action == "list":
            tasks = await self.manager.list_tasks(status=inputs.get("status"), limit=limit)
            return ToolOutput(success=True, data={"tasks": [task.brief() for task in tasks]})
        return ToolOutput(success=False, error=f"unsupported action: {action}")


class OrgCreateTaskTool(_OrgLeaderTool):
    """Create a root org task or a child task in the same pool."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_create_task",
            description="Create an organization task. Use parent_task_id/root_task_id for child tasks.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "parent_task_id": {"type": "string"},
                "root_task_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "task_type": {"type": "string"},
                "required_capabilities": {"type": "array", "items": {"type": "string"}},
                "output_spec": {"type": "object"},
                "metadata": {"type": "object"},
                "delegated_to_team_id": {"type": "string"},
                "delegated_to_leader_id": {"type": "string"},
            },
            "required": ["title", "description"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        if not inputs.get("title") or not inputs.get("description"):
            return ToolOutput(success=False, error="'title' and 'description' are required")
        result = await self.manager.create_task(
            task_id=inputs.get("task_id"),
            parent_task_id=inputs.get("parent_task_id"),
            root_task_id=inputs.get("root_task_id"),
            title=inputs["title"],
            description=inputs["description"],
            task_type=inputs.get("task_type"),
            required_capabilities=inputs.get("required_capabilities") or [],
            output_spec=OrgTaskOutputSpec.model_validate(inputs["output_spec"]) if inputs.get("output_spec") else None,
            metadata=inputs.get("metadata") or {},
            created_by=OrgTaskCreator(
                creator_type="team_leader",
                creator_id=self.leader_id,
                organization_id=self.manager.organization_id,
                team_id=self.team_id,
            ),
            delegated_to_team_id=inputs.get("delegated_to_team_id"),
            delegated_to_leader_id=inputs.get("delegated_to_leader_id"),
        )
        if not result.ok or result.task is None:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.task.brief())


class OrgClaimTaskTool(_OrgLeaderTool):
    """Claim one OPEN org task for the current leader's team."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_claim_task",
            description="Claim an open organization task for this team.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        result = await self.manager.claim_task(
            task_id=inputs.get("task_id", ""),
            team_id=self.team_id,
            leader_id=self.leader_id,
        )
        if not result.ok or result.task is None:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.task.brief())


class OrgDelegateTaskTool(_OrgLeaderTool):
    """Delegate an org task assigned to this team to another team."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_delegate_task",
            description="Delegate an organization task to another team leader.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "to_team_id": {"type": "string"},
                "to_leader_id": {"type": "string"},
            },
            "required": ["task_id", "to_team_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        result = await self.manager.delegate_task(
            task_id=inputs.get("task_id", ""),
            from_team_id=self.team_id,
            to_team_id=inputs.get("to_team_id", ""),
            to_leader_id=inputs.get("to_leader_id"),
        )
        if not result.ok or result.task is None:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.task.brief())


class OrgUpdateTaskTool(_OrgLeaderTool):
    """Start or complete an org task assigned to this team."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_update_task",
            description="Start or complete an organization task assigned to this team.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "complete"]},
                "task_id": {"type": "string"},
                "output_context": {"type": "object"},
                "output_abstract": {"type": "string"},
            },
            "required": ["action", "task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        action = inputs.get("action")
        task_id = inputs.get("task_id", "")
        if action == "start":
            result = await self.manager.start_task(task_id=task_id, team_id=self.team_id)
        elif action == "complete":
            result = await self.manager.complete_task(
                task_id=task_id,
                team_id=self.team_id,
                output_context=OrgTaskOutputContext.model_validate(inputs["output_context"])
                if inputs.get("output_context")
                else None,
                output_abstract=inputs.get("output_abstract"),
            )
        else:
            return ToolOutput(success=False, error=f"unsupported action: {action}")
        if not result.ok or result.task is None:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.task.brief())


class OrgSendLeaderMessageTool(_OrgLeaderTool):
    """Persist and announce a leader-to-leader message."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_send_leader_message",
            description="Send a DB-backed message to another team leader or all leaders.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "to_team_id": {"type": "string"},
                "to_leader_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["content"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        content = inputs.get("content")
        if not content:
            return ToolOutput(success=False, error="'content' is required")
        result = await self.manager.send_leader_message(
            from_team_id=self.team_id,
            from_leader_id=self.leader_id,
            content=content,
            to_team_id=inputs.get("to_team_id"),
            to_leader_id=inputs.get("to_leader_id"),
            metadata=inputs.get("metadata") or {},
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.data)


def create_org_leader_tools(
    *,
    manager: OrgTaskManager,
    team_id: str,
    leader_id: str,
) -> list[TeamTool]:
    return [
        OrgViewTasksTool(manager, team_id, leader_id),
        OrgCreateTaskTool(manager, team_id, leader_id),
        OrgClaimTaskTool(manager, team_id, leader_id),
        OrgDelegateTaskTool(manager, team_id, leader_id),
        OrgUpdateTaskTool(manager, team_id, leader_id),
        OrgSendLeaderMessageTool(manager, team_id, leader_id),
    ]


ORG_LEADER_TOOL_NAMES = {
    "org_view_tasks",
    "org_create_task",
    "org_claim_task",
    "org_delegate_task",
    "org_update_task",
    "org_send_leader_message",
}


__all__ = [
    "ORG_LEADER_TOOL_NAMES",
    "OrgClaimTaskTool",
    "OrgCreateTaskTool",
    "OrgDelegateTaskTool",
    "OrgSendLeaderMessageTool",
    "OrgUpdateTaskTool",
    "OrgViewTasksTool",
    "create_org_leader_tools",
]
