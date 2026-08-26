# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-only tools for organization-level collaboration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.organization.schema import (
    OrgTaskCreator,
    OrgTaskOutputContext,
    OrgTaskOutputSpec,
    OrgTaskReviewStatus,
)
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
    from openjiuwen.agent_teams.organization.transport_api import TransportAPI


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


class _OrgControlTool(TeamTool):
    """Leader tool for creating an organization and admitting active teams."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        runtime_manager: "OrganizationRuntimeManager",
        team_id: str,
        session_id: str,
    ) -> None:
        super().__init__(ToolCard(id=f"team_org_control.{name}", name=name, description=description))
        self.runtime_manager = runtime_manager
        self.team_id = team_id
        self.session_id = session_id


class OrgCreateOrganizationTool(_OrgControlTool):
    """Create an organization from the leader's already-active team."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_create_organization",
            description="Create a team organization owned by this active team.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["organization_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            organization = await self.runtime_manager.create_organization(
                organization_id=inputs.get("organization_id", ""),
                owner_team_id=self.team_id,
                session_id=self.session_id,
                display_name=inputs.get("display_name"),
                description=inputs.get("description"),
            )
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))
        data = organization.model_dump()
        data["next_action"] = (
            "Organization task-pool tools are now available to this leader. "
            "Use org_create_task, org_view_tasks, org_view_child_tasks, and org_review_task; "
            "do not replace member teams with local teammates."
        )
        return ToolOutput(success=True, data=data)


class OrgInviteTeamTool(_OrgControlTool):
    """Invite another active team; invitation acceptance is automatic in v1."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_invite_team",
            description="Invite an active team in this session to the organization. The invitation is accepted automatically.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string"},
                "team_id": {"type": "string", "description": "Active team to add."},
            },
            "required": ["organization_id", "team_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            organization = await self.runtime_manager.invite_team(
                organization_id=inputs.get("organization_id", ""),
                inviter_team_id=self.team_id,
                target_team_id=inputs.get("team_id", ""),
                session_id=self.session_id,
            )
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))
        data = organization.model_dump()
        data["next_action"] = (
            "Organization task-pool tools are now available to this leader. "
            "Use org_create_task, org_view_tasks, org_view_child_tasks, and org_review_task; "
            "do not replace member teams with local teammates."
        )
        return ToolOutput(success=True, data=data)


class OrgDissolveOrganizationTool(_OrgControlTool):
    """Dissolve an owner-controlled organization and erase its persisted state."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_dissolve_organization",
            description="Dissolve an organization owned by this team, remove its members, and delete its task-pool data.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {"organization_id": {"type": "string"}},
            "required": ["organization_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            result = await self.runtime_manager.dissolve_organization(
                organization_id=inputs.get("organization_id", ""),
                owner_team_id=self.team_id,
                session_id=self.session_id,
            )
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))
        return ToolOutput(success=True, data=result)


class OrgListAvailableTeamsTool(_OrgControlTool):
    """List active teams in the current process/session for organization setup."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_list_available_teams",
            description="List active same-session teams that can be invited into an organization.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {"type": "object", "properties": {}}

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        teams = await self.runtime_manager.list_available_teams(session_id=self.session_id)
        return ToolOutput(success=True, data={"teams": teams})


class OrgListConfiguredTeamsTool(_OrgControlTool):
    """List dormant host-configured teams that may be activated and invited."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_list_configured_teams",
            description="List configured same-process teams that can be activated and invited.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {"type": "object", "properties": {}}

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        teams = await self.runtime_manager.list_configured_teams(session_id=self.session_id)
        return ToolOutput(success=True, data={"teams": teams})


class OrgActivateAndInviteTeamTool(OrgInviteTeamTool):
    """Activate a configured team when necessary, then invite it into the organization."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        _OrgControlTool.__init__(
            self,
            name="org_activate_and_invite_team",
            description="Activate a configured team if dormant, then invite it into the organization.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string"},
                "team_id": {"type": "string", "description": "Configured profile or active team to add."},
            },
            "required": ["organization_id", "team_id"],
        }


class OrgViewOrganizationTool(_OrgControlTool):
    """Read organization ownership and members from the shared database."""

    def __init__(self, runtime_manager: "OrganizationRuntimeManager", team_id: str, session_id: str) -> None:
        super().__init__(
            name="org_view_organization",
            description="View an organization and its registered team leaders.",
            runtime_manager=runtime_manager,
            team_id=team_id,
            session_id=session_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {"organization_id": {"type": "string"}},
            "required": ["organization_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            organization = await self.runtime_manager.get_organization(
                organization_id=inputs.get("organization_id", ""),
                team_id=self.team_id,
                session_id=self.session_id,
            )
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))
        if organization is None:
            return ToolOutput(success=False, error="organization not found")
        data = organization.model_dump()
        data["next_action"] = (
            "The invited team's leader now has organization task-pool tools and can claim matching tasks."
        )
        return ToolOutput(success=True, data=data)


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
            "required": ["title", "description", "required_capabilities"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        if not inputs.get("title") or not inputs.get("description"):
            return ToolOutput(success=False, error="'title' and 'description' are required")
        capabilities = inputs.get("required_capabilities")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(not isinstance(capability, str) or not capability.strip() for capability in capabilities)
        ):
            return ToolOutput(
                success=False,
                error="'required_capabilities' must contain at least one non-empty capability",
            )
        await self._ensure_registered()
        result = await self.manager.create_task(
            task_id=inputs.get("task_id"),
            parent_task_id=inputs.get("parent_task_id"),
            root_task_id=inputs.get("root_task_id"),
            title=inputs["title"],
            description=inputs["description"],
            task_type=inputs.get("task_type"),
            required_capabilities=capabilities,
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
    """Persist and announce a leader-to-leader message via TransportAPI."""

    def __init__(
        self,
        manager: OrgTaskManager,
        team_id: str,
        leader_id: str,
        transport: "TransportAPI | None" = None,
    ) -> None:
        super().__init__(
            name="org_send_leader_message",
            description="Send a DB-backed message to another team leader or all leaders.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.transport = transport
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
        if self.transport is None:
            return ToolOutput(success=False, error="organization transport is not bound")

        to_team_id = inputs.get("to_team_id")
        result = await self.manager.send_leader_message(
            from_team_id=self.team_id,
            from_leader_id=self.leader_id,
            content=content,
            to_team_id=to_team_id,
            to_leader_id=inputs.get("to_leader_id"),
            metadata=inputs.get("metadata") or {},
        )
        if not result.ok or not result.data:
            return ToolOutput(success=False, error=result.reason or "failed to persist leader message")

        message_id = result.data["message_id"]
        targets = await self._resolve_delivery_targets(to_team_id)
        if not targets:
            return ToolOutput(success=False, error="no delivery targets for leader message")

        delivered: list[str] = []
        for target in targets:
            transport_result = await self.transport.deliver(
                content,
                target,
                message_id=message_id,
            )
            if not transport_result.success:
                return ToolOutput(
                    success=False,
                    error=transport_result.reason or f"failed to deliver to {target}",
                    data={**result.data, "delivered_to": delivered},
                )
            delivered.append(target)

        return ToolOutput(success=True, data={**result.data, "delivered_to": delivered})

    async def _resolve_delivery_targets(self, to_team_id: str | None) -> list[str]:
        if to_team_id:
            return [to_team_id]
        organization = await self.manager.get_organization()
        if organization is None:
            return []
        return sorted(
            {
                leader.team_id
                for leader in organization.leaders
                if leader.team_id and leader.team_id != self.team_id
            }
        )


class OrgViewChildTasksTool(_OrgLeaderTool):
    """View direct child tasks created for a parent task."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_view_child_tasks",
            description="View direct child tasks for a parent organization task.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "parent_task_id": {"type": "string"},
                "only_mine": {"type": "boolean"},
            },
            "required": ["parent_task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        parent_task_id = inputs.get("parent_task_id")
        if not parent_task_id:
            return ToolOutput(success=False, error="'parent_task_id' is required")
        tasks = await self.manager.list_child_tasks(
            parent_task_id=parent_task_id,
            creator_team_id=self.team_id if inputs.get("only_mine", True) else None,
        )
        return ToolOutput(success=True, data={"tasks": [task.brief() for task in tasks]})


class OrgViewPendingReviewsTool(_OrgLeaderTool):
    """View child task results waiting for this team to review."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_view_pending_reviews",
            description="View completed child tasks waiting for this team to review.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        reviews = await self.manager.list_pending_reviews(team_id=self.team_id, limit=int(inputs.get("limit") or 50))
        return ToolOutput(success=True, data={"pending_reviews": reviews})


class OrgReviewTaskTool(_OrgLeaderTool):
    """Accept or reject a completed child task result."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_review_task",
            description="Review a completed child task created by this team.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "review_status": {
                    "type": "string",
                    "enum": ["ACCEPTED", "REJECTED", "NEEDS_REVISION"],
                },
                "verdict": {"type": "string"},
                "required_changes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id", "review_status"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        result = await self.manager.review_task(
            task_id=inputs.get("task_id", ""),
            reviewer_team_id=self.team_id,
            review_status=OrgTaskReviewStatus(inputs.get("review_status", "")),
            verdict=inputs.get("verdict"),
            required_changes=inputs.get("required_changes") or [],
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.data)


class OrgCreateSummaryTaskTool(_OrgLeaderTool):
    """Create a third-party summary task."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_create_summary_task",
            description="Create an organization summary task backed by source tasks.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "source_task_ids": {"type": "array", "items": {"type": "string"}},
                "output_spec": {"type": "object"},
                "metadata": {"type": "object"},
            },
            "required": ["title", "description"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        if not inputs.get("title") or not inputs.get("description"):
            return ToolOutput(success=False, error="'title' and 'description' are required")
        result = await self.manager.create_summary_task(
            task_id=inputs.get("task_id"),
            title=inputs["title"],
            description=inputs["description"],
            source_task_ids=inputs.get("source_task_ids") or [],
            output_spec=OrgTaskOutputSpec.model_validate(inputs["output_spec"]) if inputs.get("output_spec") else None,
            metadata=inputs.get("metadata") or {},
            created_by=OrgTaskCreator(
                creator_type="team_leader",
                creator_id=self.leader_id,
                organization_id=self.manager.organization_id,
                team_id=self.team_id,
            ),
        )
        if not result.ok or result.task is None:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.task.brief())


class OrgAttachSummarySourcesTool(_OrgLeaderTool):
    """Attach completed source tasks to a summary task."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_attach_summary_sources",
            description="Attach completed source tasks to an organization summary task.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {
                "summary_task_id": {"type": "string"},
                "source_task_ids": {"type": "array", "items": {"type": "string"}},
                "source_role": {"type": "string"},
                "required": {"type": "boolean"},
            },
            "required": ["summary_task_id", "source_task_ids"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        result = await self.manager.attach_summary_sources(
            summary_task_id=inputs.get("summary_task_id", ""),
            source_task_ids=inputs.get("source_task_ids") or [],
            source_role=inputs.get("source_role"),
            required=inputs.get("required", True),
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)
        return ToolOutput(success=True, data=result.data)


class OrgViewSummarySourcesTool(_OrgLeaderTool):
    """View source tasks and outputs for a summary task."""

    def __init__(self, manager: OrgTaskManager, team_id: str, leader_id: str) -> None:
        super().__init__(
            name="org_view_summary_sources",
            description="View source task outputs attached to an organization summary task.",
            manager=manager,
            team_id=team_id,
            leader_id=leader_id,
        )
        self.card.input_params = {
            "type": "object",
            "properties": {"summary_task_id": {"type": "string"}},
            "required": ["summary_task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._ensure_registered()
        summary_task_id = inputs.get("summary_task_id")
        if not summary_task_id:
            return ToolOutput(success=False, error="'summary_task_id' is required")
        data = await self.manager.get_summary_inputs(summary_task_id=summary_task_id)
        if data is None:
            return ToolOutput(success=False, error=f"summary task not found: {summary_task_id}")
        return ToolOutput(success=True, data=data)


def create_org_leader_tools(
    *,
    manager: OrgTaskManager,
    team_id: str,
    leader_id: str,
    transport: "TransportAPI | None" = None,
) -> list[TeamTool]:
    return [
        OrgViewTasksTool(manager, team_id, leader_id),
        OrgCreateTaskTool(manager, team_id, leader_id),
        OrgClaimTaskTool(manager, team_id, leader_id),
        OrgDelegateTaskTool(manager, team_id, leader_id),
        OrgUpdateTaskTool(manager, team_id, leader_id),
        OrgSendLeaderMessageTool(manager, team_id, leader_id, transport=transport),
        OrgViewChildTasksTool(manager, team_id, leader_id),
        OrgViewPendingReviewsTool(manager, team_id, leader_id),
        OrgReviewTaskTool(manager, team_id, leader_id),
        OrgCreateSummaryTaskTool(manager, team_id, leader_id),
        OrgAttachSummarySourcesTool(manager, team_id, leader_id),
        OrgViewSummarySourcesTool(manager, team_id, leader_id),
    ]


def create_org_control_tools(
    *,
    runtime_manager: "OrganizationRuntimeManager",
    team_id: str,
    session_id: str,
) -> list[TeamTool]:
    return [
        OrgCreateOrganizationTool(runtime_manager, team_id, session_id),
        OrgInviteTeamTool(runtime_manager, team_id, session_id),
        OrgDissolveOrganizationTool(runtime_manager, team_id, session_id),
        OrgListAvailableTeamsTool(runtime_manager, team_id, session_id),
        OrgListConfiguredTeamsTool(runtime_manager, team_id, session_id),
        OrgActivateAndInviteTeamTool(runtime_manager, team_id, session_id),
        OrgViewOrganizationTool(runtime_manager, team_id, session_id),
    ]


ORG_LEADER_TOOL_NAMES = {
    "org_view_tasks",
    "org_create_task",
    "org_claim_task",
    "org_delegate_task",
    "org_update_task",
    "org_send_leader_message",
    "org_view_child_tasks",
    "org_view_pending_reviews",
    "org_review_task",
    "org_create_summary_task",
    "org_attach_summary_sources",
    "org_view_summary_sources",
}


__all__ = [
    "ORG_LEADER_TOOL_NAMES",
    "OrgCreateOrganizationTool",
    "OrgDissolveOrganizationTool",
    "OrgInviteTeamTool",
    "OrgListAvailableTeamsTool",
    "OrgListConfiguredTeamsTool",
    "OrgActivateAndInviteTeamTool",
    "OrgViewOrganizationTool",
    "OrgClaimTaskTool",
    "OrgCreateTaskTool",
    "OrgDelegateTaskTool",
    "OrgAttachSummarySourcesTool",
    "OrgCreateSummaryTaskTool",
    "OrgReviewTaskTool",
    "OrgSendLeaderMessageTool",
    "OrgUpdateTaskTool",
    "OrgViewChildTasksTool",
    "OrgViewPendingReviewsTool",
    "OrgViewSummarySourcesTool",
    "OrgViewTasksTool",
    "create_org_leader_tools",
    "create_org_control_tools",
]
