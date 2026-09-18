# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind already-active in-process teams into an organization."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.core.common.logging import team_logger
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.messager.inprocess import InProcessMessager
from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgTaskClaimedEvent,
    OrgTaskCompletedEvent,
    OrgTaskCreatedEvent,
    OrgTaskDelegatedEvent,
    OrgTaskFailedEvent,
    OrgTaskReviewedEvent,
    OrgTaskReviewRequestedEvent,
    OrgTeamInvitedEvent,
    OrgTeamJoinedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.expert_adapters import (
    ExpertGroupCatalog,
    ExpertTeamLauncher,
)
from openjiuwen.agent_teams.organization.summary_team import SummaryTeamLauncher
from openjiuwen.agent_teams.organization.pool import get_process_org_manager, remove_process_org_manager
from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_REPAIRS_TASK_ID_KEY,
    OrganizationSpec,
    OrgTaskFailureCode,
    OrgTaskReviewStatus,
    OrgSummaryExecutionStatus,
    OrgSummaryTeamStatus,
    OrgTaskStatus,
    OrgUnclaimedTaskPolicy,
)
from openjiuwen.agent_teams.organization.task_pool import (
    OrgTaskManager,
    _is_supersedable_task,
)
from openjiuwen.agent_teams.organization.unclaimed import OrgUnclaimedTaskService
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.agent_teams.tools.team import TeamBackend

_ORG_OWNER_LIFECYCLE_SECTION = "organization_owner_lifecycle"
_ORG_COLLABORATION_SECTION = "organization_collaboration"
_ORG_SUMMARY_TEAM_SECTION = "organization_summary_team"
_ORG_OWNER_LIFECYCLE_PROMPT = {
    "cn": (
        "## Team Organization 生命周期约束\n"
        "你是当前 Team Organization 的 owner。organization 存在期间，禁止调用 "
        "clean_team，也不要关闭或解散本 Team。需要结束 organization 时，必须先调用 "
        "org_dissolve_organization 清空 organization 的成员和任务池；仅在该调用成功后，"
        "才能执行 shutdown_member 或 clean_team。"
    ),
    "en": (
        "## Team Organization lifecycle constraint\n"
        "You own the current Team Organization. While it exists, do not call clean_team "
        "and do not shut down or disband this Team. To end the organization, first call "
        "org_dissolve_organization to clear its members and task pool. Only after that call "
        "succeeds may you use shutdown_member or clean_team."
    ),
}

_ORG_COLLABORATION_PROMPT = {
    "cn": (
        "## Team Organization 协作记录\n"
        "当跨 Team 依赖需要确认 API 契约、输入输出、验收结论或明确阻塞项时，使用 "
        "org_send_leader_message 向相关 leader 发送简短、可执行的消息。不要用它发送例行状态，"
        "也不要用它替代 task pool 的认领、完成和评审操作。\n"
        "对于已委派给其他 Team 的直接子任务，DELEGATED、CLAIMED 和 IN_PROGRESS 均表示对方 "
        "正在负责处理；暂未出现 output_context 或 output_abstract 不代表失败。不得重新委派、"
        "认领、启动、完成该任务，也不得为绕过等待创建内容重复的替代子任务。结束当前回合并等待 "
        "完成事件；只有 FAILED、REJECTED 或 NEEDS_REVISION 时，才按既有修复流程创建修复子任务。\n"
        "认领根任务后，先按职责选择汇总模式：本 Team 能独立作最终判断、其他 Team 仅提供佐证时选 "
        "HIERARCHICAL；财务、法律、技术、市场等独立领域需要跨域形成最终判断，或本 Team 只负责其中 "
        "一部分时选 SUMMARY_TEAM。选择并启动根任务后，所有拆分工作都必须以该根任务为直接父任务，"
        "不能创建并列根任务。SUMMARY_TEAM 下，本 Team 自己的贡献也必须写成 "
        "根任务的直接子任务并完成验收，才能作为最终汇总来源；不要只留在 Team 内部任务中。"
    ),
    "en": (
        "## Team Organization collaboration record\n"
        "When a cross-team dependency needs an API-contract, input/output, acceptance, or concrete "
        "blocker confirmation, send a short actionable org_send_leader_message to the relevant leader. "
        "Do not use it for routine status updates or instead of task-pool claim, completion, and review operations.\n"
        "For a direct child delegated to another Team, DELEGATED, CLAIMED, and IN_PROGRESS mean that Team "
        "owns its execution; missing output does not mean failure. Do not re-delegate, claim, start, or complete "
        "it, and do not create a duplicate replacement merely to bypass waiting. End the current turn and wait "
        "for its completion event. Create a repair child only after FAILED, REJECTED, or NEEDS_REVISION.\n"
        "After claiming a root, choose its aggregation mode before decomposition: use HIERARCHICAL only when "
        "your Team can independently make the final judgment and other Teams supply supporting evidence. "
        "Use SUMMARY_TEAM for independent specialist domains requiring a cross-domain final judgment, or when "
        "your Team handles only one part. After choosing and starting the root, create every work item as "
        "its direct child, never as a parallel root. In SUMMARY_TEAM mode, record your own contribution as a direct "
        "root child and have it accepted; an internal Team task alone is not a summary source."
    ),
}

_ORG_SUMMARY_TEAM_PROMPT = {
    "cn": (
        "## Summary Team 固定职责\n"
        "你只负责组织根任务的最终汇总。收到 `organization.summary` 任务时，必须先调用 "
        "`org_summary_get_inputs` 读取该任务绑定的、已验收来源及其输出；仅基于这些来源形成最终结论。"
        "不得创建 Organization 子任务、重新认领任务、审核或修改来源任务。将可交付给用户的最终内容写入 "
        "`org_summary_complete` 的 output_context.description，并提供 output_abstract。"
    ),
    "en": (
        "## Summary Team fixed responsibility\n"
        "You only produce the final aggregation for an organization root task. For an "
        "`organization.summary` task, first call `org_summary_get_inputs` to read its bound, "
        "accepted sources and their outputs, then derive the final result only from those sources. "
        "Do not create Organization child tasks, re-claim tasks, or review or modify source tasks. Complete the "
        "Summary Task via `org_summary_complete`, putting the user-facing deliverable "
        "in output_context.description and a concise output_abstract."
    ),
}

_LEADER_TURN_PAUSE_POLL_INTERVAL_SECONDS = 0.1
_PARENT_RESUME_TERMINAL_STATUSES = frozenset(
    {
        OrgTaskStatus.COMPLETED,
        OrgTaskStatus.FAILED,
    }
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


class OrganizationRuntimeManager:
    """Create organizations and bind active leaders without restarting teams."""

    def __init__(self, team_runtime_manager: "TeamRuntimeManager") -> None:
        self._team_runtime_manager = team_runtime_manager
        self._membership_lock = asyncio.Lock()
        self._unclaimed_services: dict[tuple[str, str], OrgUnclaimedTaskService] = {}
        self._subscribed_topics: set[tuple[str, str, str, OrgTopic, int]] = set()
        self._org_subscribers: dict[tuple[str, str, str], Any] = {}
        self._org_subscriber_sources: dict[tuple[str, str, str], int] = {}
        self._team_organizations: dict[tuple[str, str], str] = {}
        self._leader_turn_queues: dict[tuple[str, str], deque[object]] = {}
        self._leader_turn_workers: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._scheduled_leader_messages: set[tuple[str, str, str]] = set()
        self._scheduled_parent_reviews: set[tuple[str, str, str]] = set()
        self._scheduled_summary_executions: set[tuple[str, str, str]] = set()
        self._leader_turn_runner: Callable[[str, str, object], Awaitable[bool]] | None = None
        self._configured_team_provider: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None
        self._team_activator: Callable[[str, str], Awaitable[str | None]] | None = None
        self._expert_group_catalog: ExpertGroupCatalog | None = None
        self._expert_team_launcher: ExpertTeamLauncher | None = None
        self._expert_adapter_installer: Callable[["OrganizationRuntimeManager"], None] | None = None
        self._summary_team_launcher: SummaryTeamLauncher | None = None
        self._summary_adapter_installer: Callable[["OrganizationRuntimeManager"], None] | None = None
        self._summary_team_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def set_leader_turn_runner(self, runner: Callable[[str, str, object], Awaitable[bool]]) -> None:
        """Set the host-owned path used to run an autonomous leader turn."""

        self._leader_turn_runner = runner

    def set_configured_team_provider(self, provider: Callable[[str], Awaitable[list[dict[str, Any]]]]) -> None:
        """Set the host callback exposing dormant same-process team templates."""

        self._configured_team_provider = provider

    def set_team_activator(self, activator: Callable[[str, str], Awaitable[str | None]]) -> None:
        """Set the host callback that activates one configured team on invitation."""

        self._team_activator = activator

    def set_expert_group_catalog(self, catalog: ExpertGroupCatalog) -> None:
        """Set the host adapter that lists validated AgentGroup packages."""

        self._expert_group_catalog = catalog

    def set_expert_team_launcher(self, launcher: ExpertTeamLauncher) -> None:
        """Set the host adapter that launches expert Teams for organization invite."""

        self._expert_team_launcher = launcher

    def set_expert_adapter_installer(self, installer: Callable[["OrganizationRuntimeManager"], None] | None) -> None:
        """Register a host callback that injects Catalog/Launcher on first use.

        The installer should be idempotent and must not run package scans itself;
        it only constructs and ``set_*`` the adapters. Listing/launch still happen
        when tools call ``list_expert_groups`` / ``create_and_invite_expert_team``.
        """

        self._expert_adapter_installer = installer

    def set_summary_team_launcher(self, launcher: SummaryTeamLauncher) -> None:
        """Set the host launcher for the organization-wide reusable Summary Team."""

        self._summary_team_launcher = launcher

    def set_summary_adapter_installer(self, installer: Callable[["OrganizationRuntimeManager"], None] | None) -> None:
        """Register the host's lazy Summary Team adapter installer."""

        self._summary_adapter_installer = installer

    def _ensure_summary_adapter(self) -> None:
        """Install the host Summary Team launcher on first summary execution request."""

        if self._summary_team_launcher is not None or self._summary_adapter_installer is None:
            return
        self._summary_adapter_installer(self)

    def _ensure_expert_adapters(self) -> None:
        """Lazily run the host installer once Catalog or Launcher is still missing."""

        if self._expert_group_catalog is not None and self._expert_team_launcher is not None:
            return
        installer = self._expert_adapter_installer
        if installer is None:
            return
        installer(self)

    async def ensure_summary_team(
        self,
        *,
        organization_id: str,
        root_team_id: str,
        session_id: str,
    ) -> tuple[str, str]:
        """Lazily launch, invite, and persist the singleton Summary Team for one organization."""

        self._ensure_summary_adapter()
        if self._summary_team_launcher is None:
            raise ValueError("Summary Team launcher is not configured by the host")
        _, root_backend = await self._resolve_leader(root_team_id, session_id)
        manager = get_process_org_manager(
            organization_id=organization_id,
            db=root_backend.db,
            messager=root_backend.messager,
            session_id=session_id,
        )
        lock = self._summary_team_locks.setdefault((session_id, organization_id), asyncio.Lock())
        async with lock:
            existing = await manager.task_pool.get_summary_team()
            if existing is not None and existing.status == OrgSummaryTeamStatus.READY:
                if existing.summary_team_id and existing.leader_id:
                    entry = await self._team_runtime_manager.pool.get(existing.summary_team_id)
                    if (
                        entry is not None
                        and entry.current_session_id == session_id
                        and self._team_organizations.get((session_id, existing.summary_team_id)) == organization_id
                    ):
                        return existing.summary_team_id, existing.leader_id
            await manager.task_pool.reserve_summary_team()
            organization = await manager.get_organization()
            if organization is None or not organization.owner_team_id:
                raise ValueError("organization owner is required to invite the Summary Team")
            launched = await self._summary_team_launcher.launch(
                organization_id=organization_id,
                session_id=session_id,
                share_db_from_team_id=root_team_id,
            )
            try:
                await self.invite_team(
                    organization_id=organization_id,
                    inviter_team_id=organization.owner_team_id,
                    target_team_id=launched.team_id,
                    session_id=session_id,
                )
            except Exception:
                await self._summary_team_launcher.stop(team_id=launched.team_id, session_id=session_id)
                raise
            await manager.task_pool.mark_summary_team_ready(
                team_id=launched.team_id,
                leader_id=launched.leader_id,
            )
            return launched.team_id, launched.leader_id

    async def notify_summary_provision_failure(
        self,
        *,
        organization_id: str,
        root_team_id: str,
        root_leader_id: str,
        summary_task_id: str,
        reason: str,
        session_id: str,
    ) -> None:
        """Persist and deliver a provisioning-failure notice to the Root Leader."""

        _, backend = await self._resolve_leader(root_team_id, session_id)
        manager = get_process_org_manager(
            organization_id=organization_id,
            db=backend.db,
            messager=backend.messager,
            session_id=session_id,
        )
        await manager.message_service.send_leader_message(
            from_team_id="__organization__",
            from_leader_id="__organization__",
            to_team_id=root_team_id,
            to_leader_id=root_leader_id,
            content=(
                f"Summary Team provisioning failed for Summary Task {summary_task_id}. "
                f"The Summary Task is FAILED(SUMMARY_PROVISION_FAILED). Reason: {reason}"
            ),
            metadata={
                "kind": "summary_provision_failed",
                "task_id": summary_task_id,
                "failure_code": OrgTaskFailureCode.SUMMARY_PROVISION_FAILED.value,
            },
        )

    async def ensure_control_tools(self, agent: "TeamAgent", *, session_id: str) -> None:
        """Mount organization bootstrap tools on a running team leader."""

        backend = getattr(agent, "team_backend", None)
        if backend is None or not backend.is_leader:
            return
        harness = agent.harness
        add_tool = getattr(harness, "add_tool", None)
        if not callable(add_tool):
            return
        from openjiuwen.agent_teams.organization.tools import create_org_control_tools

        for tool in create_org_control_tools(
            runtime_manager=self,
            team_id=backend.team_name,
            session_id=session_id,
        ):
            add_tool(tool)

    async def create_organization(
        self,
        *,
        organization_id: str,
        owner_team_id: str,
        session_id: str,
        display_name: str | None = None,
        description: str | None = None,
        unclaimed_task_policy: OrgUnclaimedTaskPolicy | None = None,
    ) -> OrganizationSpec:
        """Create an organization owned by an active team and bind its leader."""

        if not organization_id:
            raise ValueError("organization_id is required")
        async with self._membership_lock:
            owner_agent, owner_backend = await self._resolve_leader(owner_team_id, session_id)
            existing_manager = getattr(owner_backend, "org_task_manager", None)
            if existing_manager is not None and existing_manager.organization_id != organization_id:
                raise ValueError(f"team already belongs to organization: {existing_manager.organization_id}")

            manager = get_process_org_manager(
                organization_id=organization_id,
                db=owner_backend.db,
                messager=owner_backend.messager,
                session_id=session_id,
            )
            existing = await manager.get_organization()
            if existing is not None and existing.owner_team_id not in (None, owner_team_id):
                raise ValueError(f"organization already belongs to team: {existing.owner_team_id}")

            owner_leader_id = self._leader_id(owner_agent, owner_backend)
            spec = await manager.initialize(
                unclaimed_task_policy=unclaimed_task_policy,
                display_name=display_name,
                description=description,
                metadata={
                    **(existing.metadata if existing is not None else {}),
                    "owner_team_id": owner_team_id,
                    "owner_leader_id": owner_leader_id,
                    "session_id": session_id,
                },
            )
            await self._bind_team(
                agent=owner_agent,
                backend=owner_backend,
                manager=manager,
                session_id=session_id,
            )
            return (await manager.get_organization()) or spec

    async def invite_team(
        self,
        *,
        organization_id: str,
        inviter_team_id: str,
        target_team_id: str,
        session_id: str,
    ) -> OrganizationSpec:
        """Invite an active team, activating a configured dormant team when needed."""

        async with self._membership_lock:
            inviter_agent, inviter_backend = await self._resolve_leader(inviter_team_id, session_id)
            try:
                target_agent, target_backend = await self._resolve_leader(target_team_id, session_id)
            except ValueError as exc:
                if self._team_activator is None:
                    raise
                activated_team_id = await self._team_activator(target_team_id, session_id)
                if not activated_team_id:
                    raise ValueError(f"configured team could not be activated: {target_team_id}") from exc
                target_team_id = activated_team_id
                target_agent, target_backend = await self._resolve_leader(target_team_id, session_id)
            manager = get_process_org_manager(
                organization_id=organization_id,
                db=inviter_backend.db,
                messager=inviter_backend.messager,
                session_id=session_id,
            )
            organization = await manager.get_organization()
            if organization is None:
                raise ValueError(f"organization not found: {organization_id}")
            if organization.owner_team_id != inviter_team_id:
                raise ValueError("only the organization owner team can invite members")
            if target_backend.db is not inviter_backend.db:
                raise ValueError("invited team must use the owner's shared TeamDatabase instance")

            current_manager = getattr(target_backend, "org_task_manager", None)
            if current_manager is not None and current_manager.organization_id != organization_id:
                raise ValueError(f"team already belongs to organization: {current_manager.organization_id}")

            await manager.publish_event(
                OrgTeamInvitedEvent(
                    organization_id=organization_id,
                    team_id=inviter_team_id,
                    leader_id=self._leader_id(inviter_agent, inviter_backend),
                    inviter_team_id=inviter_team_id,
                    invited_team_id=target_team_id,
                ),
                team_inbox_id=target_team_id,
            )
            await self._bind_team(
                agent=target_agent,
                backend=target_backend,
                manager=manager,
                session_id=session_id,
            )
            await self._resume_assignable_tasks(
                manager=manager,
                team_id=target_team_id,
                session_id=session_id,
                capabilities=set(self._capabilities(target_agent)),
            )
            target_leader_id = self._leader_id(target_agent, target_backend)
            await manager.publish_event(
                OrgTeamJoinedEvent(
                    organization_id=organization_id,
                    team_id=target_team_id,
                    leader_id=target_leader_id,
                    joined_team_id=target_team_id,
                    joined_leader_id=target_leader_id,
                )
            )
            return (await manager.get_organization()) or organization

    async def dissolve_organization(
        self,
        *,
        organization_id: str,
        owner_team_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Dissolve an organization, unbind its teams, and erase its DB rows."""

        async with self._membership_lock:
            _, owner_backend = await self._resolve_leader(owner_team_id, session_id)
            manager = get_process_org_manager(
                organization_id=organization_id,
                db=owner_backend.db,
                messager=owner_backend.messager,
                session_id=session_id,
            )
            organization = await manager.get_organization()
            if organization is None:
                raise ValueError(f"organization not found: {organization_id}")
            if organization.owner_team_id != owner_team_id:
                raise ValueError("only the organization owner team can dissolve an organization")
            summary_team = await manager.task_pool.get_summary_team()

            service = self._unclaimed_services.pop((session_id, organization_id), None)
            if service is not None:
                await service.stop()

            member_team_ids = {leader.team_id for leader in organization.leaders}
            member_team_ids.add(owner_team_id)
            for team_id in member_team_ids:
                key = (session_id, team_id)
                worker = self._leader_turn_workers.pop(key, None)
                if worker is not None and not worker.done():
                    worker.cancel()
                self._leader_turn_queues.pop(key, None)
                self._scheduled_leader_messages = {
                    message_key for message_key in self._scheduled_leader_messages if message_key[:2] != key
                }
                self._scheduled_parent_reviews = {
                    review_key for review_key in self._scheduled_parent_reviews if review_key[:2] != key
                }
                self._scheduled_summary_executions = {
                    summary_key for summary_key in self._scheduled_summary_executions if summary_key[:2] != key
                }
                await self._release_org_subscriber(organization_id, session_id, team_id)
                entry = await self._team_runtime_manager.pool.get(team_id)
                if entry is None or entry.current_session_id != session_id:
                    continue
                backend = getattr(entry.agent, "team_backend", None)
                if backend is None:
                    continue
                backend.org_task_manager = None
                backend.org_message_service = None
                self._set_owner_lifecycle_prompt(entry.agent, is_owner=False)
                harness = getattr(entry.agent, "harness", None)
                remove_tool = getattr(harness, "remove_tool", None)
                if callable(remove_tool):
                    from openjiuwen.agent_teams.organization.tools import ORG_LEADER_TOOL_NAMES

                    for tool_name in ORG_LEADER_TOOL_NAMES:
                        remove_tool(tool_name)
                self._team_organizations.pop(key, None)

            deleted = await manager.dissolve_organization()
            if self._summary_team_launcher is not None and summary_team is not None and summary_team.summary_team_id:
                await self._summary_team_launcher.stop(
                    team_id=summary_team.summary_team_id,
                    session_id=session_id,
                )
            self._summary_team_locks.pop((session_id, organization_id), None)
            remove_process_org_manager(
                organization_id=organization_id,
                db=owner_backend.db,
                session_id=session_id,
            )
            return {
                "organization_id": organization_id,
                "dissolved_team_ids": sorted(member_team_ids),
                "deleted": deleted,
            }

    async def get_organization(self, *, organization_id: str, team_id: str, session_id: str) -> OrganizationSpec | None:
        """Read organization state through an active member's shared database."""

        _, backend = await self._resolve_leader(team_id, session_id)
        manager = get_process_org_manager(
            organization_id=organization_id,
            db=backend.db,
            messager=backend.messager,
            session_id=session_id,
        )
        organization = await manager.get_organization()
        if organization is None:
            return None
        if team_id not in {leader.team_id for leader in organization.leaders}:
            raise ValueError("team is not a member of this organization")
        return organization

    async def list_available_teams(self, *, session_id: str) -> list[dict[str, Any]]:
        """Return active same-session teams that can be invited by an owner leader."""

        teams = await self._team_runtime_manager.pool.teams_for_session(session_id)
        available: list[dict[str, Any]] = []
        for entry in teams:
            backend = getattr(entry.agent, "team_backend", None)
            if backend is None or not backend.is_leader:
                continue
            available.append(
                {
                    "team_id": backend.team_name,
                    "leader_id": self._leader_id(entry.agent, backend),
                    "state": entry.state.value,
                    "capabilities": self._capabilities(entry.agent),
                    "organization_id": getattr(getattr(backend, "org_task_manager", None), "organization_id", None),
                }
            )
        return available

    async def list_configured_teams(self, *, session_id: str) -> list[dict[str, Any]]:
        """Return host-registered dormant team templates for this session."""

        if self._configured_team_provider is None:
            return []
        return await self._configured_team_provider(session_id)

    async def list_expert_groups(self, *, capabilities: set[str] | None = None) -> list[dict[str, Any]]:
        """List host-validated AgentGroup templates; does not create Teams."""

        self._ensure_expert_adapters()
        if self._expert_group_catalog is None:
            return []
        return [descriptor.to_dict() for descriptor in self._expert_group_catalog.list(capabilities=capabilities)]

    async def create_and_invite_expert_team(
        self,
        *,
        organization_id: str,
        owner_team_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Launch an expert Team from an AgentGroup package and invite it."""

        self._ensure_expert_adapters()
        if self._expert_team_launcher is None:
            raise ValueError("expert team launcher is not configured")
        group_name = str(agent_group_name or "").strip()
        if not group_name:
            raise ValueError("agent_group_name is required")

        _, owner_backend = await self._resolve_leader(owner_team_id, session_id)
        manager = get_process_org_manager(
            organization_id=organization_id,
            db=owner_backend.db,
            messager=owner_backend.messager,
            session_id=session_id,
        )
        organization = await manager.get_organization()
        if organization is None:
            raise ValueError(f"organization not found: {organization_id}")
        if organization.owner_team_id != owner_team_id:
            raise ValueError("only the organization owner team can create expert teams")

        launched = await self._expert_team_launcher.launch(
            organization_id=organization_id,
            agent_group_name=group_name,
            session_id=session_id,
            display_name=display_name,
            share_db_from_team_id=owner_team_id,
        )
        try:
            organization = await self.invite_team(
                organization_id=organization_id,
                inviter_team_id=owner_team_id,
                target_team_id=launched.team_id,
                session_id=session_id,
            )
        except Exception:
            await self._expert_team_launcher.stop(
                team_id=launched.team_id,
                session_id=session_id,
            )
            raise

        return {
            "organization": organization.model_dump(),
            **launched.to_dict(),
            "agent_group_name": launched.agent_group_name or group_name,
        }

    async def _bind_team(self, *, agent: "TeamAgent", backend: TeamBackend, manager: Any, session_id: str) -> None:
        backend.org_task_manager = manager.task_pool
        backend.org_message_service = manager.message_service
        self._team_organizations[(session_id, backend.team_name)] = manager.organization_id
        organization = await manager.get_organization()
        self._set_owner_lifecycle_prompt(
            agent,
            is_owner=organization is not None and organization.owner_team_id == backend.team_name,
        )
        self._set_collaboration_prompt(agent)
        is_summary_team = self._is_summary_team(agent)
        self._set_summary_team_prompt(agent, is_summary_team=is_summary_team)
        leader_id = self._leader_id(agent, backend)
        await manager.register_leader(
            team_id=backend.team_name,
            leader_id=leader_id,
            leader_member_name=backend.leader_member_name or leader_id,
            capabilities=self._capabilities(agent),
        )
        if not is_summary_team:
            await self.ensure_control_tools(agent, session_id=session_id)

        harness = agent.harness
        add_tool = getattr(harness, "add_tool", None)
        if callable(add_tool):
            from openjiuwen.agent_teams.organization.tools import (
                create_org_leader_tools,
                create_summary_leader_tools,
            )

            tools = (
                create_summary_leader_tools(
                    manager=manager.task_pool,
                    team_id=backend.team_name,
                    leader_id=leader_id,
                )
                if is_summary_team
                else create_org_leader_tools(
                    manager=manager.task_pool,
                    message_service=manager.message_service,
                    team_id=backend.team_name,
                    leader_id=leader_id,
                    runtime_manager=self,
                    session_id=session_id,
                )
            )
            for tool in tools:
                add_tool(tool)
        await self._subscribe_team_events(
            backend,
            manager,
            session_id,
            capabilities=set(self._capabilities(agent)),
        )
        await self._ensure_unclaimed_service(manager, session_id)

    async def _ensure_unclaimed_service(self, manager: Any, session_id: str) -> None:
        key = (session_id, manager.organization_id)
        service = self._unclaimed_services.get(key)
        if service is None:
            organization = await manager.get_organization()

            async def notify(message: dict[str, Any]) -> None:
                team_id = message["to_team_id"]
                if self._team_organizations.get((session_id, team_id)) != manager.organization_id:
                    return
                entry = await self._team_runtime_manager.pool.get(team_id)
                if entry is None or entry.current_session_id != session_id:
                    return
                message_key = (session_id, team_id, message["message_id"])
                if message_key in self._scheduled_leader_messages:
                    self._ensure_leader_turn_worker(team_id, session_id)
                    return
                self._scheduled_leader_messages.add(message_key)
                self._schedule_leader_turn(
                    team_id=team_id,
                    session_id=session_id,
                    prompt="",
                    message_key=message_key,
                    unclaimed_notification=(key, message),
                )

            service = OrgUnclaimedTaskService(
                manager,
                notify,
                organization.unclaimed_task_policy.scan_interval_seconds,
            )
            self._unclaimed_services[key] = service
        service.start()

    async def release_team(self, *, team_id: str, session_id: str) -> None:
        """Release background work before the last bound team or its database stops."""
        key = (session_id, team_id)
        organization_id = self._team_organizations.pop(key, None)
        worker = self._leader_turn_workers.pop(key, None)
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        queue = self._leader_turn_queues.pop(key, deque())
        self._clear_leader_turn_queue(queue)
        if organization_id:
            await self._release_org_subscriber(organization_id, session_id, team_id)
        if organization_id and not any(
            session == session_id and org == organization_id for (session, _), org in self._team_organizations.items()
        ):
            service = self._unclaimed_services.pop((session_id, organization_id), None)
            if service is not None:
                await service.stop()

    async def close(self) -> None:
        """Stop organization-owned tasks before host teardown."""
        services = list(self._unclaimed_services.values())
        self._unclaimed_services.clear()
        for service in services:
            await service.stop()
        workers = [worker for worker in self._leader_turn_workers.values() if worker is not asyncio.current_task()]
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._leader_turn_workers.clear()
        self._leader_turn_queues.clear()
        self._scheduled_leader_messages.clear()
        self._scheduled_parent_reviews.clear()
        self._scheduled_summary_executions.clear()
        for session_id, organization_id, team_id in tuple(self._org_subscribers):
            await self._release_org_subscriber(organization_id, session_id, team_id)

    @staticmethod
    def _set_owner_lifecycle_prompt(agent: "TeamAgent", *, is_owner: bool) -> None:
        """Keep the owner-only organization lifecycle rule in the live system prompt."""

        harness = getattr(agent, "harness", None)
        prompt_builder = getattr(harness, "system_prompt_builder", None)
        if prompt_builder is None:
            return
        prompt_builder.remove_section(_ORG_OWNER_LIFECYCLE_SECTION)
        if not is_owner:
            return
        from openjiuwen.harness.prompts.builder import PromptSection

        prompt_builder.add_section(
            PromptSection(
                name=_ORG_OWNER_LIFECYCLE_SECTION,
                content=_ORG_OWNER_LIFECYCLE_PROMPT,
                priority=75,
            )
        )

    @staticmethod
    def _set_collaboration_prompt(agent: "TeamAgent") -> None:
        """Add the small shared rule for auditable leader communication."""

        harness = getattr(agent, "harness", None)
        prompt_builder = getattr(harness, "system_prompt_builder", None)
        if prompt_builder is None:
            return
        prompt_builder.remove_section(_ORG_COLLABORATION_SECTION)
        from openjiuwen.harness.prompts.builder import PromptSection

        prompt_builder.add_section(
            PromptSection(
                name=_ORG_COLLABORATION_SECTION,
                content=_ORG_COLLABORATION_PROMPT,
                priority=70,
            )
        )

    @staticmethod
    def _is_summary_team(agent: "TeamAgent") -> bool:
        """Return whether the host marked this agent as the organization Summary Team."""

        metadata = getattr(getattr(agent, "spec", None), "metadata", None)
        return isinstance(metadata, dict) and metadata.get("summary_team") is True

    @staticmethod
    def _set_summary_team_prompt(agent: "TeamAgent", *, is_summary_team: bool) -> None:
        """Add Summary Team-only instructions and remove them from all ordinary Teams."""

        harness = getattr(agent, "harness", None)
        prompt_builder = getattr(harness, "system_prompt_builder", None)
        if prompt_builder is None:
            return
        prompt_builder.remove_section(_ORG_SUMMARY_TEAM_SECTION)
        if not is_summary_team:
            return
        from openjiuwen.harness.prompts.builder import PromptSection

        prompt_builder.add_section(
            PromptSection(
                name=_ORG_SUMMARY_TEAM_SECTION,
                content=_ORG_SUMMARY_TEAM_PROMPT,
                priority=85,
            )
        )

    async def ensure_team_binding(
        self,
        *,
        team_id: str,
        session_id: str,
        agent: "TeamAgent" | None = None,
    ) -> bool:
        """Restore organization bindings after a host recreates a team harness.

        Configured dormant teams are intentionally created before their first LLM
        turn.  The normal Team runner may replace that provisional agent on the
        first actual invocation, so bindings must be mounted on the live agent.
        """

        if agent is None:
            agent, backend = await self._resolve_leader(team_id, session_id)
        else:
            backend = getattr(agent, "team_backend", None)
            if backend is None or not backend.is_leader:
                return False

        organization_id = self._team_organizations.get((session_id, team_id))
        if not organization_id:
            # ``_team_organizations`` disappears on a host restart while the
            # organization tables remain durable.  Rebuild the binding from
            # the leader membership record instead of silently falling back to
            # ordinary team-only tools.
            organization_ids = await OrgTaskManager.find_organization_ids_for_team(backend.db, team_id)
            if len(organization_ids) != 1:
                return False
            organization_id = organization_ids[0]

        manager = get_process_org_manager(
            organization_id=organization_id,
            db=backend.db,
            messager=backend.messager,
            session_id=session_id,
        )
        if await manager.get_organization() is None:
            return False
        await self._bind_team(agent=agent, backend=backend, manager=manager, session_id=session_id)
        await self._resume_assignable_tasks(
            manager=manager,
            team_id=team_id,
            session_id=session_id,
            capabilities=set(self._capabilities(agent)),
        )
        return True

    async def _resume_assignable_tasks(
        self,
        *,
        manager: Any,
        team_id: str,
        session_id: str,
        capabilities: set[str],
    ) -> None:
        """Recover assignable work and durable organization follow-ups.

        Topic delivery is intentionally best effort.  The task pool is the
        durable source of truth, so a freshly bound or recovered leader must
        also scan matching OPEN tasks and §7.3 parent follow-ups rather than
        relying only on past events.
        """

        await self._resume_claimed_tasks(manager=manager, team_id=team_id, session_id=session_id)
        await self._resume_summary_executions(manager=manager, team_id=team_id, session_id=session_id)
        for message in await manager.message_service.list_leader_messages(
            team_id=team_id,
            unread_only=True,
        ):
            if message["from_team_id"] == "__organization__":
                # The deadline scanner replays these with phase-aware prompts.
                continue
            self._schedule_leader_message_turn(
                team_id=team_id,
                session_id=session_id,
                message_id=message["message_id"],
                from_team_id=message["from_team_id"],
                organization_id=manager.organization_id,
            )
        await self._schedule_matching_open_claims(
            manager=manager,
            team_id=team_id,
            session_id=session_id,
            capabilities=capabilities,
            completed_task_id=None,
        )
        await self._resume_parent_followups(
            manager=manager,
            team_id=team_id,
            session_id=session_id,
        )

    async def _resume_summary_executions(self, *, manager: Any, team_id: str, session_id: str) -> None:
        """Recover unfinished Summary Team work from durable execution state on a team rebind."""

        executions = await manager.task_pool.list_incomplete_summary_executions()
        for execution in executions:
            root = await manager.task_pool.get_task(execution.root_task_id)
            if root is None:
                continue
            if execution.status == OrgSummaryExecutionStatus.PROVISIONING.value:
                if root.assignment.team_id != team_id or root.aggregation is None:
                    continue
                controller_leader_id = root.aggregation.controller_leader_id
                if not controller_leader_id:
                    continue
                try:
                    summary_team_id, _ = await self.ensure_summary_team(
                        organization_id=manager.organization_id,
                        root_team_id=team_id,
                        session_id=session_id,
                    )
                    bound = await manager.task_pool.bind_summary_execution(
                        summary_task_id=execution.summary_task_id,
                        summary_team_id=summary_team_id,
                    )
                    if not bound.ok:
                        raise RuntimeError(bound.reason or "summary execution binding failed")
                except Exception as exc:
                    reason = f"summary team provisioning failed during recovery: {exc}"
                    await manager.task_pool.fail_summary_execution(
                        summary_task_id=execution.summary_task_id,
                        failure_reason=reason,
                    )
                    await manager.task_pool.mark_summary_team_failed()
                    await self.notify_summary_provision_failure(
                        organization_id=manager.organization_id,
                        root_team_id=team_id,
                        root_leader_id=controller_leader_id,
                        summary_task_id=execution.summary_task_id,
                        reason=reason,
                        session_id=session_id,
                    )
                continue
            if execution.summary_team_id != team_id:
                continue
            summary_task = await manager.task_pool.get_task(execution.summary_task_id)
            if (
                execution.status == OrgSummaryExecutionStatus.RUNNING.value
                and summary_task is not None
                and summary_task.status in {OrgTaskStatus.DELEGATED, OrgTaskStatus.IN_PROGRESS}
            ):
                self.schedule_summary_execution(
                    team_id=team_id,
                    session_id=session_id,
                    task_id=summary_task.task_id,
                    organization_id=manager.organization_id,
                    execution_id=execution.execution_id,
                    root_task_id=execution.root_task_id,
                )

        # This covers a crash after sources became accepted but before their
        # TASK_DELEGATED event reached the Summary Team.
        await manager.task_pool.activate_ready_summary_tasks()

    async def _resume_claimed_tasks(self, *, manager: Any, team_id: str, session_id: str) -> None:
        """Resume work claimed before a process or harness recovery."""

        for task in await manager.task_pool.list_tasks_for_team(team_id, include_open=False):
            if task.status is OrgTaskStatus.CLAIMED:
                self._schedule_claimed_task_execution_turn(
                    team_id=team_id,
                    session_id=session_id,
                    task_id=task.task_id,
                    organization_id=manager.organization_id,
                )

    async def _resume_parent_followups(
        self,
        *,
        manager: Any,
        team_id: str,
        session_id: str,
    ) -> None:
        """Rebuild §7.3 leader turns from durable task/review state after rebind.

        Event delivery is best-effort; PENDING reviews, unrepaired FAILED/REJECTED
        children, and completeable parents must be rediscovered from the task pool.
        """
        organization_id = manager.organization_id
        parent_ids: set[str] = set()

        for item in await manager.task_pool.list_pending_reviews(team_id=team_id):
            task_brief = item.get("task") or {}
            child_task_id = task_brief.get("task_id")
            parent_task_id = task_brief.get("parent_task_id")
            if not child_task_id or not parent_task_id:
                continue
            parent_ids.add(parent_task_id)
            self._schedule_parent_review_turn(
                team_id=team_id,
                session_id=session_id,
                child_task_id=child_task_id,
                parent_task_id=parent_task_id,
                organization_id=organization_id,
            )

        for task in await manager.task_pool.list_tasks_created_by_team(team_id=team_id):
            if (
                task.unclaimed is not None
                and task.failure_code is OrgTaskFailureCode.EXPIRED
                and task.unclaimed.closed_reason in {"description_update_timeout", "post_update_claim_timeout"}
            ):
                continue
            if not task.parent_task_id:
                if task.status not in _PARENT_RESUME_TERMINAL_STATUSES:
                    parent_ids.add(task.task_id)
                continue
            parent_ids.add(task.parent_task_id)
            review = await manager.task_pool.get_task_review(task.task_id)
            if not _is_supersedable_task(task.status.value, review):
                continue
            if await manager.task_pool.has_accepted_or_active_repair(
                parent_task_id=task.parent_task_id,
                repairs_target=task.task_id,
            ):
                continue
            if task.status is OrgTaskStatus.FAILED:
                self._schedule_parent_child_failed_turn(
                    team_id=team_id,
                    session_id=session_id,
                    child_task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    organization_id=organization_id,
                    failure_code=(
                        task.failure_code.value
                        if task.failure_code is not None
                        else OrgTaskFailureCode.EXECUTION_FAILED.value
                    ),
                    failure_reason=task.failure_reason or "recovered failed child",
                    repairs_task_id=self._original_repairs_target(task),
                )
                continue
            review_status = review.review_status.value if review is not None else None
            if review_status in {
                OrgTaskReviewStatus.REJECTED.value,
                OrgTaskReviewStatus.NEEDS_REVISION.value,
            }:
                self._schedule_parent_repair_turn(
                    team_id=team_id,
                    session_id=session_id,
                    child_task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    organization_id=organization_id,
                    review_status=review_status,
                    repairs_task_id=self._original_repairs_target(task),
                )

        for parent_task_id in parent_ids:
            parent = await manager.task_pool.get_task(parent_task_id)
            if parent is None or parent.status in _PARENT_RESUME_TERMINAL_STATUSES:
                continue
            if not await manager.task_pool.can_complete_parent_task(
                parent_task_id=parent_task_id,
                team_id=team_id,
            ):
                continue
            self._schedule_parent_ready_turn(
                team_id=team_id,
                session_id=session_id,
                parent_task_id=parent_task_id,
                organization_id=organization_id,
            )

    async def _subscribe_team_events(
        self,
        backend: TeamBackend,
        manager: Any,
        session_id: str,
        *,
        capabilities: set[str],
    ) -> None:
        backend_messager = backend.messager
        if backend_messager is None:
            return
        subscriber_key = (session_id, manager.organization_id, backend.team_name)
        if self._org_subscriber_sources.get(subscriber_key) not in (None, id(backend_messager)):
            await self._release_org_subscriber(manager.organization_id, session_id, backend.team_name)
        messager = self._org_subscribers.get(subscriber_key)
        if messager is None:
            # InProcessMessager keys topic subscribers by node_id. Team leaders
            # commonly share the same member name, so organization listeners
            # need their own organization/team-scoped subscriber identity.
            messager = (
                InProcessMessager(
                    config=MessagerTransportConfig(
                        node_id=f"org:{session_id}:{manager.organization_id}:{backend.team_name}"
                    )
                )
                if isinstance(backend_messager, InProcessMessager)
                else backend_messager
            )
            self._org_subscribers[subscriber_key] = messager
            self._org_subscriber_sources[subscriber_key] = id(backend_messager)

        async def _on_task_event(message: Any) -> None:
            event = message.get_payload()
            if isinstance(event, OrgTaskCreatedEvent):
                if event.team_id == backend.team_name:
                    return
                task = await manager.task_pool.get_task(event.task_id)
                required = set(task.required_capabilities) if task is not None else set()
                if not required or not required.issubset(capabilities):
                    return
                self._schedule_claim_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    task_id=event.task_id,
                    organization_id=manager.organization_id,
                )
                return
            if isinstance(event, OrgTaskClaimedEvent):
                if event.claimed_by_team_id != backend.team_name:
                    return
                self._schedule_claimed_task_execution_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    task_id=event.task_id,
                    organization_id=manager.organization_id,
                )
                return
            if isinstance(event, OrgTaskCompletedEvent):
                # Completion is a second durable opportunity to claim matching
                # OPEN tasks and wake a parent review if its separate review
                # event was not delivered.
                await self._schedule_matching_open_claims(
                    manager=manager,
                    team_id=backend.team_name,
                    session_id=session_id,
                    capabilities=capabilities,
                    completed_task_id=event.task_id,
                )
                task = await manager.task_pool.get_task(event.task_id)
                if task is None or not task.parent_task_id:
                    return
                if task.created_by.team_id != backend.team_name:
                    return
                review = await manager.task_pool.get_task_review(event.task_id)
                if review is None or review.review_status is not OrgTaskReviewStatus.PENDING:
                    return
                self._schedule_parent_review_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    child_task_id=event.task_id,
                    parent_task_id=task.parent_task_id,
                    organization_id=manager.organization_id,
                )
                return
            if isinstance(event, OrgTaskFailedEvent):
                task = await manager.task_pool.get_task(event.task_id)
                if self._is_unclaimed_expiration(task, event):
                    # The durable expiration inbox request also covers root tasks.
                    return
                if task is None or not task.parent_task_id:
                    return
                if task.created_by.team_id != backend.team_name:
                    return
                self._schedule_parent_child_failed_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    child_task_id=event.task_id,
                    parent_task_id=task.parent_task_id,
                    organization_id=manager.organization_id,
                    failure_code=event.failure_code,
                    failure_reason=event.failure_reason,
                    repairs_task_id=self._original_repairs_target(task),
                )
                return
            if isinstance(event, OrgTaskReviewRequestedEvent):
                if event.reviewer_team_id != backend.team_name:
                    return
                self._schedule_parent_review_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    child_task_id=event.task_id,
                    parent_task_id=event.parent_task_id,
                    organization_id=manager.organization_id,
                )
                return
            if not isinstance(event, OrgTaskReviewedEvent):
                return
            if event.team_id != backend.team_name:
                return
            task = await manager.task_pool.get_task(event.task_id)
            if task is None or not task.parent_task_id:
                return
            if event.review_status in {
                OrgTaskReviewStatus.REJECTED.value,
                OrgTaskReviewStatus.NEEDS_REVISION.value,
            }:
                self._schedule_parent_repair_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    child_task_id=event.task_id,
                    parent_task_id=task.parent_task_id,
                    organization_id=manager.organization_id,
                    review_status=event.review_status,
                    repairs_task_id=self._original_repairs_target(task),
                )
                return
            if event.review_status != OrgTaskReviewStatus.ACCEPTED.value:
                return
            if not await manager.task_pool.can_complete_parent_task(
                parent_task_id=task.parent_task_id,
                team_id=backend.team_name,
            ):
                return
            self._schedule_parent_ready_turn(
                team_id=backend.team_name,
                session_id=session_id,
                parent_task_id=task.parent_task_id,
                organization_id=manager.organization_id,
            )

        async def _on_inbox_event(message: Any) -> None:
            event_type = getattr(message, "event_type", None)
            if event_type == OrgEvent.TASK_DELEGATED:
                event = message.get_payload()
                if not isinstance(event, OrgTaskDelegatedEvent):
                    return
                task = await manager.task_pool.get_task(event.task_id)
                if task is not None and task.task_type == "organization.summary":
                    execution = await manager.task_pool.get_summary_execution(summary_task_id=event.task_id)
                    if execution is None:
                        return
                    self.schedule_summary_execution(
                        team_id=backend.team_name,
                        session_id=session_id,
                        task_id=event.task_id,
                        organization_id=manager.organization_id,
                        execution_id=execution.execution_id,
                        root_task_id=execution.root_task_id,
                    )
                    return
                self._schedule_delegated_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    task_id=event.task_id,
                    organization_id=manager.organization_id,
                )
                return
            if event_type == OrgEvent.LEADER_MESSAGE:
                payload = getattr(message, "payload", None) or {}
                message_id = payload.get("message_id")
                if not message_id:
                    return
                if (session_id, backend.team_name, message_id) in self._scheduled_leader_messages:
                    self._ensure_leader_turn_worker(backend.team_name, session_id)
                    return
                persisted = await manager.message_service.get_leader_message(
                    message_id=message_id,
                    team_id=backend.team_name,
                )
                if persisted is None or persisted["handled_at"] is not None:
                    return
                self._schedule_leader_message_turn(
                    team_id=backend.team_name,
                    session_id=session_id,
                    message_id=message_id,
                    from_team_id=str(payload.get("from_team_id") or message.sender_id or ""),
                    organization_id=manager.organization_id,
                )

        await self._subscribe_once(
            messager=messager,
            topic=OrgTopic.TASK,
            session_id=session_id,
            organization_id=manager.organization_id,
            team_id=backend.team_name,
            handler=_on_task_event,
        )
        await self._subscribe_once(
            messager=messager,
            topic=OrgTopic.TEAM_INBOX,
            session_id=session_id,
            organization_id=manager.organization_id,
            team_id=backend.team_name,
            handler=_on_inbox_event,
        )

    async def _release_org_subscriber(self, organization_id: str, session_id: str, team_id: str) -> None:
        """Unsubscribe one team's organization-only listener without touching its Team transport."""
        messager = self._org_subscribers.pop((session_id, organization_id, team_id), None)
        self._org_subscriber_sources.pop((session_id, organization_id, team_id), None)
        for subscribed in tuple(self._subscribed_topics):
            subscribed_org, subscribed_session, subscribed_team, topic, messager_id = subscribed
            if (subscribed_org, subscribed_session, subscribed_team) != (organization_id, session_id, team_id):
                continue
            unsubscribe = getattr(messager, "unsubscribe", None)
            if callable(unsubscribe) and messager_id == id(messager):
                await unsubscribe(
                    topic.build(session_id, organization_id, team_id if topic is OrgTopic.TEAM_INBOX else None)
                )
            self._subscribed_topics.discard(subscribed)

    @staticmethod
    def _is_unclaimed_expiration(task: Any, event: OrgTaskFailedEvent) -> bool:
        """Unclaimed expiry has its own durable creator notification path."""

        if task is None or task.unclaimed is None or event.failure_code != "EXPIRED":
            return False
        return task.unclaimed.closed_reason in {
            "description_update_timeout",
            "post_update_claim_timeout",
        }

    async def _subscribe_once(
        self,
        *,
        messager: Any,
        topic: OrgTopic,
        session_id: str,
        organization_id: str,
        team_id: str,
        handler: Any,
    ) -> None:
        key = (organization_id, session_id, team_id, topic, id(messager))
        if key in self._subscribed_topics:
            return
        topic_id = topic.build(session_id, organization_id, team_id if topic is OrgTopic.TEAM_INBOX else None)
        await messager.subscribe(topic_id, handler)
        self._subscribed_topics.add(key)

    async def _schedule_matching_open_claims(
        self,
        *,
        manager: Any,
        team_id: str,
        session_id: str,
        capabilities: set[str],
        completed_task_id: str | None,
    ) -> None:
        for task in await manager.task_pool.list_open_tasks():
            if completed_task_id is not None and task.task_id == completed_task_id:
                continue
            required = set(task.required_capabilities)
            if required and required.issubset(capabilities):
                self._schedule_claim_turn(
                    team_id=team_id,
                    session_id=session_id,
                    task_id=task.task_id,
                    organization_id=manager.organization_id,
                    trigger_task_id=completed_task_id,
                )

    def _schedule_claim_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        task_id: str,
        organization_id: str,
        trigger_task_id: str | None = None,
    ) -> None:
        trigger_context = (
            f" Task {trigger_task_id} just completed, so re-evaluate this open task now." if trigger_task_id else ""
        )
        prompt = (
            f"Organization task {task_id} is available in {organization_id}.{trigger_context} "
            "Inspect it with org_view_tasks(action='get'). If every required capability is present "
            "in your team, you MUST call org_claim_task for this task in this turn. Do not leave a "
            "capability-matched task OPEN merely because another team's artifact is not ready: claim "
            "it first, prepare any independent work, and use org_view_tasks to wait for dependencies "
            "before starting dependent validation. When the defined scope has been executed, produce "
            "one final result or report and call org_update_task(action='complete') in the same "
            "workflow, including failures and blockers in its output. Do not wait for another team to "
            "fix a reported issue, and do not create an open-ended sequence of extra verification tasks "
            "unless the parent task explicitly requests it. Only skip the claim when a required capability "
            "is actually absent or the claim fails because another team already claimed it."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_delegated_turn(self, *, team_id: str, session_id: str, task_id: str, organization_id: str) -> None:
        prompt = (
            f"Organization task {task_id} in {organization_id} was delegated to your team. "
            "Inspect it with org_view_tasks(action='get'), then use org_update_task(action='start') "
            "when you are ready. If an independent part requires another organization team's "
            "capabilities, keep this parent task assigned to your team and create a focused OPEN child "
            f"with org_create_task(parent_task_id='{task_id}'). Give each child a clear scope, "
            "acceptance criteria, and only the capabilities it needs; do not set delegated_to_team_id. "
            "Track children with org_view_child_tasks and do not complete the parent until its direct "
            "children are completed and accepted. Otherwise execute the task through your team workflow "
            "and complete it with the resulting output context and output abstract."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def schedule_summary_execution(
        self,
        *,
        team_id: str,
        session_id: str,
        task_id: str,
        organization_id: str,
        execution_id: str,
        root_task_id: str,
    ) -> None:
        """Queue one final-aggregation turn and suppress duplicate event deliveries."""

        summary_key = (session_id, team_id, task_id)
        if summary_key in self._scheduled_summary_executions:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_summary_executions.add(summary_key)

        prompt = (
            f"Summary Task {task_id} (execution_id={execution_id}, root_task_id={root_task_id}) in "
            f"organization {organization_id} is ready for final aggregation. "
            f"Call org_summary_get_inputs(summary_task_id='{task_id}') before doing any synthesis. "
            "Use only those bound, accepted source outputs. Do NOT create child tasks, delegate, "
            "claim, review, or modify any source task. Delegate only the two internal analysis/drafting "
            f"tasks to the fixed Summary Team teammates and prefix their internal task titles with {execution_id}, "
            "then produce the final user-facing result. After those two internal tasks report, immediately call "
            "org_summary_complete in the same leader turn. Internal task completion, Team idle, or Team pause is "
            "not Summary Task completion: do not start another internal task cycle, wait, or poll instead. "
            "A Root Leader message may add delivery requirements, but it does not change this Team's two-tool "
            "protocol or authorize access to other organization tasks. "
            "Complete this Summary Task with org_summary_complete, placing the deliverable in "
            "output_context.description "
            "and a concise summary in output_abstract; completing it also completes the root task."
        )
        # MVP permits one active root task only. The execution id is carried in
        # the prompt so a future concurrent implementation must introduce a
        # separate harness session/workspace rather than reuse this context.
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            summary_key=summary_key,
        )

    def _schedule_leader_message_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        message_id: str,
        from_team_id: str,
        organization_id: str,
    ) -> None:
        message_key = (session_id, team_id, message_id)
        if message_key in self._scheduled_leader_messages:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_leader_messages.add(message_key)
        prompt = (
            f"Leader message {message_id} arrived in organization {organization_id} "
            f"from team {from_team_id}. Read it with org_get_leader_message, perform any required "
            "cross-team coordination or task-pool updates, then call org_ack_leader_message only "
            "after the message has been handled."
        )
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            message_key=message_key,
        )

    def _schedule_claimed_task_execution_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        task_id: str,
        organization_id: str,
    ) -> None:
        """Continue an automatic claim with a separate execution turn.

        A claim is persisted during a leader's tool call, but that LLM turn can
        legitimately finish immediately afterwards.  Queue a second turn so a
        successfully auto-claimed task never remains stranded in ``CLAIMED``.
        """

        prompt = (
            f"Your team claimed organization task {task_id} in {organization_id}. "
            "Inspect it with org_view_tasks(action='get'). If it is a root task and still CLAIMED, first "
            "choose its aggregation mode before starting it. Choose HIERARCHICAL only when your Team can "
            "independently make the final decision and other Teams provide supporting evidence or dependent "
            "work. Choose SUMMARY_TEAM when the root needs independent, orthogonal conclusions from two or "
            "more specialist domains, or when your Team can complete only one part and cannot reasonably "
            "represent the final cross-domain judgment. Do not choose HIERARCHICAL merely because it is the "
            "default, shorter, or because your Team coordinates the work; prefer SUMMARY_TEAM for independent "
            "multi-domain due diligence unless your Team truly owns the final integration. Call "
            "org_update_task(action='set_aggregation_mode') with that choice. "
            "Then call org_update_task(action='start') and execute the "
            "defined scope through your Team workflow. If an independent part requires another organization "
            "team's capabilities, keep this parent task assigned to your team and create a focused OPEN child "
            f"with org_create_task(parent_task_id='{task_id}'). Give each child a clear scope, acceptance "
            "criteria, and only the capabilities it needs; do not set delegated_to_team_id. Track children "
            "with org_view_child_tasks and wait until every direct child is completed and accepted. If the "
            "child is delegated to another Team and is DELEGATED, CLAIMED, or IN_PROGRESS, it is still being "
            "executed: do not re-delegate it or create a duplicate replacement; end this turn and wait for "
            "its completion event. "
            "If the root uses HIERARCHICAL, integrate those accepted outputs and complete the root yourself with "
            "org_update_task(action='complete'). If the root uses SUMMARY_TEAM, include every contribution "
            "(including work your own Team performs) as a direct child, then call "
            "org_create_summary_execution with all direct child ids after they are accepted. Do not directly "
            "complete a SUMMARY_TEAM root; its Summary Task completes it. If the task is already IN_PROGRESS "
            "or COMPLETED, do not duplicate work."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_parent_review_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        child_task_id: str,
        parent_task_id: str,
        organization_id: str,
    ) -> None:
        review_key = (session_id, team_id, child_task_id)
        if review_key in self._scheduled_parent_reviews:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_parent_reviews.add(review_key)
        prompt = (
            f"Child organization task {child_task_id} completed in {organization_id}. "
            f"Inspect its result with org_review_task, then accept or reject it. "
            f"If accepted, use the child output to continue parent task {parent_task_id}. "
            "If rejected, create a repair with org_create_task "
            f"(set repairs_task_id={child_task_id} on the original sibling; never repair-of-repair; "
            "do not org_delegate_task the rejected child). "
            "When all direct children are accepted or superseded by an accepted repair, inspect the parent "
            "task's aggregation mode. Complete it only for HIERARCHICAL (or a non-root parent). For a "
            "SUMMARY_TEAM root, create its Summary Execution instead; do not call complete on that root. "
            "For a root completion, put the user-facing delivery in org_update_task output_context.description "
            "and provide output_abstract."
        )
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            review_key=review_key,
        )

    def _schedule_parent_repair_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        child_task_id: str,
        parent_task_id: str,
        organization_id: str,
        review_status: str,
        repairs_task_id: str | None = None,
    ) -> None:
        review_key = (session_id, team_id, f"repair:{child_task_id}")
        if review_key in self._scheduled_parent_reviews:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_parent_reviews.add(review_key)
        target_id = repairs_task_id or child_task_id
        prompt = (
            f"Child organization task {child_task_id} was reviewed as {review_status} "
            f"in {organization_id}. Parent task {parent_task_id} cannot advance on that child. "
            "Read the child result and review verdict/required_changes. "
            + self._repair_create_instructions(
                target_id=target_id,
                report_phrase="defect report",
                terminal_label="rejected/completed",
            )
        )
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            review_key=review_key,
        )

    def _schedule_parent_ready_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        parent_task_id: str,
        organization_id: str,
    ) -> None:
        review_key = (session_id, team_id, f"complete:{parent_task_id}")
        if review_key in self._scheduled_parent_reviews:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_parent_reviews.add(review_key)
        prompt = (
            f"All direct child tasks for parent organization task {parent_task_id} "
            f"in {organization_id} are accepted or superseded by an accepted repair. "
            "Inspect the parent's aggregation mode. For HIERARCHICAL (or a non-root parent), integrate the "
            "child outputs and call org_update_task(action='complete') on the parent with the final "
            "output_context and output_abstract. For a SUMMARY_TEAM root, call "
            "org_create_summary_execution with the accepted direct child ids instead; do not complete the "
            "root yourself. For a root completion, put the user-facing delivery in output_context.description."
        )
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            review_key=review_key,
        )

    def _schedule_parent_child_failed_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        child_task_id: str,
        parent_task_id: str,
        organization_id: str,
        failure_code: str,
        failure_reason: str,
        repairs_task_id: str | None = None,
    ) -> None:
        review_key = (session_id, team_id, f"failed:{child_task_id}")
        if review_key in self._scheduled_parent_reviews:
            self._ensure_leader_turn_worker(team_id, session_id)
            return
        self._scheduled_parent_reviews.add(review_key)
        target_id = repairs_task_id or child_task_id
        prompt = (
            f"Child organization task {child_task_id} failed in {organization_id} "
            f"(failure_code={failure_code}, failure_reason={failure_reason}). "
            f"Parent task {parent_task_id} cannot advance on that child. "
            "This is not a pending review — do not call org_review_task on the failed child. "
            + self._repair_create_instructions(
                target_id=target_id,
                report_phrase="the failure report",
                terminal_label="failed",
            )
        )
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompt,
            review_key=review_key,
        )

    @staticmethod
    def _repair_create_instructions(
        *,
        target_id: str,
        report_phrase: str,
        terminal_label: str,
    ) -> str:
        """Shared wake guidance for creating a repair sibling of a terminal child."""
        return (
            "Create a focused repair task with org_create_task "
            f"(set repairs_task_id={target_id} pointing at the original sibling, never another "
            f"repair; include {report_phrase} and acceptance criteria; prefer capabilities that "
            "match the defect; if the original has retry_limit, do not exceed it). "
            "If org_create_task fails because retry_limit is reached, do not retry create in a "
            "loop: call org_update_task(action='failed') on the parent with failure_reason "
            "explaining that the repair budget is exhausted, so the owning/parent team can "
            "decide the next step or fail/terminate toward the root. Same team may "
            "execute the repair; switching teams is optional—only if switching teams, set "
            "delegated_to_team_id on that new repair (or org_delegate_task the new OPEN repair "
            "only). Do not call org_delegate_task on the "
            f"{terminal_label} child, which is terminal. "
            "Do not leave the parent waiting without creating that repair, and do not silently "
            f"reopen the {terminal_label} child task."
        )

    @staticmethod
    def _original_repairs_target(task: Any) -> str:
        """Return the original sibling id a new repair must target (never a repair-of-repair)."""
        meta = getattr(task, "metadata", None) or {}
        nested = meta.get(ORG_TASK_REPAIRS_TASK_ID_KEY) if isinstance(meta, dict) else None
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        return str(task.task_id)

    def _schedule_leader_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        prompt: str,
        message_key: tuple[str, str, str] | None = None,
        review_key: tuple[str, str, str] | None = None,
        summary_key: tuple[str, str, str] | None = None,
        unclaimed_notification: tuple[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        key = (session_id, team_id)
        queue = self._leader_turn_queues.setdefault(key, deque())
        queue.append(
            {
                "query": prompt,
                "_org_message_key": message_key,
                "_org_review_key": review_key,
                "_org_summary_key": summary_key,
                "_org_unclaimed_notification": unclaimed_notification,
            }
        )
        self._ensure_leader_turn_worker(team_id, session_id)

    def _ensure_leader_turn_worker(self, team_id: str, session_id: str) -> None:
        """Restart a retained organization turn when another event reaches this team."""
        key = (session_id, team_id)
        if not self._leader_turn_queues.get(key):
            return
        worker = self._leader_turn_workers.get(key)
        if worker is None or worker.done():
            worker = asyncio.create_task(self._drain_leader_turns(team_id, session_id))
            self._leader_turn_workers[key] = worker

    async def _drain_leader_turns(self, team_id: str, session_id: str) -> None:
        """Run one background leader turn at a time and retain events while busy."""

        key = (session_id, team_id)
        try:
            queue = self._leader_turn_queues.setdefault(key, deque())
            while queue:
                entry = await self._team_runtime_manager.pool.get(team_id)
                if entry is None or entry.current_session_id != session_id:
                    self._clear_leader_turn_queue(queue)
                    return
                if entry.state is not RuntimeState.PAUSED:
                    await asyncio.sleep(_LEADER_TURN_PAUSE_POLL_INTERVAL_SECONDS)
                    continue
                inputs = queue.popleft()
                original_inputs = dict(inputs) if isinstance(inputs, dict) else inputs
                message_key = None
                review_key = None
                summary_key = None
                turn_failed = False
                if isinstance(inputs, dict):
                    message_key = inputs.pop("_org_message_key", None)
                    review_key = inputs.pop("_org_review_key", None)
                    summary_key = inputs.pop("_org_summary_key", None)
                try:
                    notification = inputs.pop("_org_unclaimed_notification", None) if isinstance(inputs, dict) else None
                    if notification is not None:
                        service_key, message = notification
                        service = self._unclaimed_services.get(service_key)
                        if service is None:
                            continue
                        persisted = await service.manager.message_service.get_leader_message(
                            message_id=message["message_id"],
                            team_id=team_id,
                        )
                        if (
                            persisted is None
                            or persisted["handled_at"] is not None
                            or not await service.is_actionable(persisted)
                        ):
                            continue
                        from openjiuwen.agent_teams.prompts.loader import load_template

                        language = getattr(entry.agent.spec, "language", None) or "cn"
                        inputs["query"] = (
                            load_template(
                                f"org_unclaimed_{message['metadata']['unclaimed_kind']}",
                                language,
                            )
                            .format(
                                {
                                    **message["metadata"],
                                    "message_id": message["message_id"],
                                    "organization_id": service.manager.organization_id,
                                }
                            )
                            .content
                        )
                    if not await self._run_leader_turn(team_id, session_id, inputs):
                        turn_failed = True
                        queue.appendleft(original_inputs)
                        team_logger.warning("Organization leader turn was not run for team {} session {}", team_id, session_id)
                        return
                except Exception:
                    turn_failed = True
                    queue.appendleft(original_inputs)
                    team_logger.warning(
                        "Organization leader turn failed for team {} session {}", team_id, session_id, exc_info=True
                    )
                    return
                finally:
                    if not turn_failed and message_key is not None:
                        self._scheduled_leader_messages.discard(message_key)
                    if not turn_failed and review_key is not None:
                        self._scheduled_parent_reviews.discard(review_key)
                    if not turn_failed and summary_key is not None:
                        self._scheduled_summary_executions.discard(summary_key)
        finally:
            self._leader_turn_workers.pop(key, None)
            if not self._leader_turn_queues.get(key):
                self._leader_turn_queues.pop(key, None)

    def _clear_leader_turn_queue(self, queue: deque[object]) -> None:
        for inputs in queue:
            if isinstance(inputs, dict):
                message_key = inputs.get("_org_message_key")
                if message_key is not None:
                    self._scheduled_leader_messages.discard(message_key)
                review_key = inputs.get("_org_review_key")
                if review_key is not None:
                    self._scheduled_parent_reviews.discard(review_key)
                summary_key = inputs.get("_org_summary_key")
                if summary_key is not None:
                    self._scheduled_summary_executions.discard(summary_key)
        queue.clear()

    async def _run_leader_turn(self, team_id: str, session_id: str, inputs: object) -> bool:
        entry = await self._team_runtime_manager.pool.get(team_id)
        if entry is None or entry.current_session_id != session_id or entry.state is not RuntimeState.PAUSED:
            return False
        if self._leader_turn_runner is not None:
            return await self._leader_turn_runner(team_id, session_id, inputs)
        return await self._team_runtime_manager.run_organization_turn(
            team_name=team_id,
            session_id=session_id,
            inputs=inputs,
        )

    async def _resolve_leader(self, team_id: str, session_id: str) -> tuple["TeamAgent", TeamBackend]:
        entry = await self._team_runtime_manager.pool.get(team_id)
        if entry is None or entry.current_session_id != session_id:
            raise ValueError(f"team is not active in session: {team_id}")
        backend = entry.agent.team_backend
        if backend is None or not backend.is_leader:
            raise ValueError(f"active team has no leader backend: {team_id}")
        return entry.agent, backend

    @staticmethod
    def _leader_id(agent: "TeamAgent", backend: TeamBackend) -> str:
        return backend.leader_member_name or agent.member_name or backend.member_name

    @staticmethod
    def _capabilities(agent: "TeamAgent") -> list[str]:
        metadata = getattr(agent.spec, "metadata", None) or {}
        capabilities = metadata.get("capabilities", [])
        return [str(capability) for capability in capabilities] if isinstance(capabilities, list) else []


__all__ = ["OrganizationRuntimeManager"]
