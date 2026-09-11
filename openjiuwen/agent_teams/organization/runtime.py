# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind already-active in-process teams into an organization."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.core.common.logging import team_logger

from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgSummaryCompletedEvent,
    OrgSummaryProvisionedEvent,
    OrgSummaryProvisionFailedEvent,
    OrgSummarySourceFailedEvent,
    OrgSummarySourcesReadyEvent,
    OrgSummarySourcesUpdatedEvent,
    OrgSummaryTaskCreatedEvent,
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
from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.pool import get_process_org_manager, remove_process_org_manager
from openjiuwen.agent_teams.organization import runtime_prompts as prompts
from openjiuwen.agent_teams.organization.runtime_summary import OrganizationSummaryMixin
from openjiuwen.agent_teams.organization.schema import (
    ORG_SUMMARY_TASK_TYPE,
    ORG_TASK_REPAIRS_TASK_ID_KEY,
    OrganizationSpec,
    OrgTaskFailureCode,
    OrgTaskReviewStatus,
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
# Sentinel team id used to use one org-scoped ORG topic subscription per organization.
_ORG_SUBSCRIBER_TEAM = "__org__"
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
        "也不要用它替代 task pool 的认领、完成和评审操作。"
    ),
    "en": (
        "## Team Organization collaboration record\n"
        "When a cross-team dependency needs an API-contract, input/output, acceptance, or concrete "
        "blocker confirmation, send a short actionable org_send_leader_message to the relevant leader. "
        "Do not use it for routine status updates or instead of task-pool claim, completion, and review operations."
    ),
}

_LEADER_TURN_PAUSE_POLL_INTERVAL_SECONDS = 0.1
_PARENT_RESUME_TERMINAL_STATUSES = frozenset(
    {
        OrgTaskStatus.COMPLETED,
        OrgTaskStatus.FAILED,
    }
)

logger = team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.organization.summary import SummaryTeamFactory
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


class OrganizationRuntimeManager(OrganizationSummaryMixin):
    """Create organizations and bind active leaders without restarting teams."""

    def __init__(self, team_runtime_manager: "TeamRuntimeManager") -> None:
        self._team_runtime_manager = team_runtime_manager
        self._membership_lock = asyncio.Lock()
        self._unclaimed_services: dict[tuple[str, str], OrgUnclaimedTaskService] = {}
        self._subscribed_topics: set[tuple[str, str, str, OrgTopic, int]] = set()
        self._team_organizations: dict[tuple[str, str], str] = {}
        self._leader_turn_queues: dict[tuple[str, str], deque[object]] = {}
        self._leader_turn_workers: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._scheduled_leader_messages: set[tuple[str, str, str]] = set()
        self._scheduled_parent_reviews: set[tuple[str, str, str]] = set()
        self._leader_turn_runner: Callable[[str, str, object], Awaitable[bool]] | None = None
        self._configured_team_provider: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None
        self._team_activator: Callable[[str, str], Awaitable[str | None]] | None = None
        self._expert_group_catalog: ExpertGroupCatalog | None = None
        self._expert_team_launcher: ExpertTeamLauncher | None = None
        self._expert_adapter_installer: Callable[["OrganizationRuntimeManager"], None] | None = None
        self._summary_team_factory: SummaryTeamFactory | None = None
        self._summary_team_factory_installer: Callable[["OrganizationRuntimeManager"], None] | None = None

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

    def set_summary_team_factory(self, factory: SummaryTeamFactory) -> None:
        """Set the host adapter that provisions and releases on-demand Summary Teams."""

        self._summary_team_factory = factory

    def set_summary_team_factory_installer(
        self, installer: Callable[["OrganizationRuntimeManager"], None] | None
    ) -> None:
        """Register a host callback that injects the SummaryTeamFactory on first use.

        Mirrors :meth:`set_expert_adapter_installer`.  The installer should be
        idempotent and must not provision anything itself; it only constructs and
        ``set_summary_team_factory`` when a summary event first needs it.
        """
        self._summary_team_factory_installer = installer

    def _ensure_summary_factory(self) -> SummaryTeamFactory | None:
        """Lazily run the host installer once the SummaryTeamFactory is still missing."""

        if self._summary_team_factory is not None:
            return self._summary_team_factory
        installer = self._summary_team_factory_installer
        if installer is None:
            return None
        installer(self)
        return self._summary_team_factory

    def _ensure_expert_adapters(self) -> None:
        """Lazily run the host installer once Catalog or Launcher is still missing."""

        if self._expert_group_catalog is not None and self._expert_team_launcher is not None:
            return
        installer = self._expert_adapter_installer
        if installer is None:
            return
        installer(self)

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
                entry = await self._team_runtime_manager.pool.get(team_id)
                if entry is None or entry.current_session_id != session_id:
                    continue
                backend = getattr(entry.agent, "team_backend", None)
                if backend is None:
                    continue
                unsubscribe = getattr(backend.messager, "unsubscribe", None)
                for subscribed in tuple(self._subscribed_topics):
                    subscribed_org, subscribed_session, subscribed_team, topic, messager_id = subscribed
                    if (
                        subscribed_org == organization_id
                        and subscribed_session == session_id
                        and subscribed_team == team_id
                    ):
                        if callable(unsubscribe) and messager_id == id(backend.messager):
                            await unsubscribe(
                                topic.build(
                                    session_id,
                                    organization_id,
                                    team_id if topic is OrgTopic.TEAM_INBOX else None,
                                )
                            )
                        self._subscribed_topics.discard(subscribed)
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

    async def _bind_team(self, *, agent: "TeamAgent", backend: TeamBackend, manager: TeamOrganizationManager, session_id: str) -> None:
        backend.org_task_manager = manager.task_pool
        backend.org_message_service = manager.message_service
        self._team_organizations[(session_id, backend.team_name)] = manager.organization_id
        organization = await manager.get_organization()
        self._set_owner_lifecycle_prompt(
            agent,
            is_owner=organization is not None and organization.owner_team_id == backend.team_name,
        )
        self._set_collaboration_prompt(agent)
        leader_id = self._leader_id(agent, backend)
        await manager.register_leader(
            team_id=backend.team_name,
            leader_id=leader_id,
            leader_member_name=backend.leader_member_name or leader_id,
            capabilities=self._capabilities(agent),
        )
        await self.ensure_control_tools(agent, session_id=session_id)

        harness = agent.harness
        add_tool = getattr(harness, "add_tool", None)
        if callable(add_tool):
            from openjiuwen.agent_teams.organization.tools import create_org_leader_tools

            for tool in create_org_leader_tools(
                manager=manager.task_pool,
                message_service=manager.message_service,
                team_id=backend.team_name,
                leader_id=leader_id,
            ):
                add_tool(tool)
        await self._subscribe_team_events(
            backend,
            manager,
            session_id,
            capabilities=set(self._capabilities(agent)),
        )
        await self._ensure_unclaimed_service(manager, session_id)

    async def _ensure_unclaimed_service(self, manager: TeamOrganizationManager, session_id: str) -> None:
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
        """Recover claimed work, matching open work, and durable parent follow-ups.

        Topic delivery is intentionally best effort.  The task pool is the
        durable source of truth, so a freshly bound or recovered leader must
        also scan matching OPEN tasks and §7.3 parent follow-ups rather than
        relying only on past events.
        """

        await self._resume_claimed_tasks(manager=manager, team_id=team_id, session_id=session_id)
        await self._resume_summary_executions(manager=manager, session_id=session_id)
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

    async def _resume_claimed_tasks(self, *, manager: TeamOrganizationManager, team_id: str, session_id: str) -> None:
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
        manager: TeamOrganizationManager,
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
        manager: TeamOrganizationManager,
        session_id: str,
        *,
        capabilities: set[str],
    ) -> None:
        messager = backend.messager
        if messager is None:
            return

        async def _on_task_created(event: OrgTaskCreatedEvent) -> None:
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

        async def _on_task_claimed(event: OrgTaskClaimedEvent) -> None:
            if event.claimed_by_team_id != backend.team_name:
                return
            self._schedule_claimed_task_execution_turn(
                team_id=backend.team_name,
                session_id=session_id,
                task_id=event.task_id,
                organization_id=manager.organization_id,
            )

        async def _on_task_completed(event: OrgTaskCompletedEvent) -> None:
            # Completion is a second durable opportunity to claim matching OPEN
            # tasks. Parent-team review wake is driven by ReviewRequested.
            await self._schedule_matching_open_claims(
                manager=manager,
                team_id=backend.team_name,
                session_id=session_id,
                capabilities=capabilities,
                completed_task_id=event.task_id,
            )

        async def _on_task_failed(event: OrgTaskFailedEvent) -> None:
            task = await manager.task_pool.get_task(event.task_id)
            if self._is_unclaimed_expiration(task, event):
                return
            if task is None:
                return
            if task.task_type == ORG_SUMMARY_TASK_TYPE:
                await self._handle_summary_task_failed(
                    manager=manager,
                    task_id=event.task_id,
                    session_id=session_id,
                )
                return
            if not task.parent_task_id:
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

        async def _on_task_review_requested(event: OrgTaskReviewRequestedEvent) -> None:
            if event.reviewer_team_id != backend.team_name:
                return
            self._schedule_parent_review_turn(
                team_id=backend.team_name,
                session_id=session_id,
                child_task_id=event.task_id,
                parent_task_id=event.parent_task_id,
                organization_id=manager.organization_id,
            )

        async def _on_task_reviewed(event: OrgTaskReviewedEvent) -> None:
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

        task_handlers = {
            OrgTaskCreatedEvent: _on_task_created,
            OrgTaskClaimedEvent: _on_task_claimed,
            OrgTaskCompletedEvent: _on_task_completed,
            OrgTaskFailedEvent: _on_task_failed,
            OrgTaskReviewRequestedEvent: _on_task_review_requested,
            OrgTaskReviewedEvent: _on_task_reviewed,
        }

        async def _on_task_event(message: Any) -> None:
            event = message.get_payload()
            handler = task_handlers.get(type(event))
            if handler is not None:
                await handler(event)

        async def _on_inbox_event(message: Any) -> None:
            event_type = getattr(message, "event_type", None)
            if event_type == OrgEvent.TASK_DELEGATED:
                event = message.get_payload()
                if not isinstance(event, OrgTaskDelegatedEvent):
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

        async def _on_summary_task_created(event: OrgSummaryTaskCreatedEvent) -> None:
            await self._handle_summary_task_created(
                manager=manager,
                summary_factory=summary_factory,
                event=event,
                session_id=session_id,
            )

        async def _on_summary_provisioned(event: OrgSummaryProvisionedEvent) -> None:
            await self._handle_summary_provisioned(
                manager=manager, event=event, session_id=session_id
            )

        async def _on_summary_provision_failed(event: OrgSummaryProvisionFailedEvent) -> None:
            await self._handle_summary_provision_failed(
                manager=manager, event=event, session_id=session_id
            )

        async def _on_summary_sources_updated(event: OrgSummarySourcesUpdatedEvent) -> None:
            await self._handle_summary_sources_updated(
                manager=manager, event=event, session_id=session_id
            )

        async def _on_summary_sources_ready(event: OrgSummarySourcesReadyEvent) -> None:
            await self._handle_summary_sources_ready(
                manager=manager, event=event, session_id=session_id
            )

        async def _on_summary_source_failed(event: OrgSummarySourceFailedEvent) -> None:
            await self._handle_summary_source_failed(
                manager=manager, event=event, session_id=session_id
            )

        async def _on_summary_completed(event: OrgSummaryCompletedEvent) -> None:
            await self._handle_summary_completed(
                manager=manager, event=event, session_id=session_id
            )

        org_handlers = {
            OrgSummaryTaskCreatedEvent: _on_summary_task_created,
            OrgSummaryProvisionedEvent: _on_summary_provisioned,
            OrgSummaryProvisionFailedEvent: _on_summary_provision_failed,
            OrgSummarySourcesUpdatedEvent: _on_summary_sources_updated,
            OrgSummarySourcesReadyEvent: _on_summary_sources_ready,
            OrgSummarySourceFailedEvent: _on_summary_source_failed,
            OrgSummaryCompletedEvent: _on_summary_completed,
        }

        async def _on_org_event(message: Any) -> None:
            nonlocal summary_factory
            summary_factory = self._ensure_summary_factory()
            if summary_factory is None:
                # Summary lifecycle events need a host factory; without one they
                # are dropped (the Summary Task row itself still exists).
                logger.debug(
                    "org event %s dropped: no summary team factory installed",
                    getattr(message, "event_type", None),
                )
                return
            event = message.get_payload()
            handler = org_handlers.get(type(event))
            if handler is not None:
                await handler(event)

        summary_factory = None

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
        # Summary lifecycle events are published only to the org-scoped ORG topic.
        # Deduplicate on a sentinel team_id so the subscription exists once per
        # organization regardless of how many teams share this runtime.
        await self._subscribe_once(
            messager=messager,
            topic=OrgTopic.ORG,
            session_id=session_id,
            organization_id=manager.organization_id,
            team_id=_ORG_SUBSCRIBER_TEAM,
            handler=_on_org_event,
        )

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
        manager: TeamOrganizationManager,
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
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.claim_turn(
                task_id=task_id,
                organization_id=organization_id,
                trigger_task_id=trigger_task_id,
            ),
        )

    def _schedule_delegated_turn(self, *, team_id: str, session_id: str, task_id: str, organization_id: str) -> None:
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.delegated_turn(task_id=task_id, organization_id=organization_id),
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
            return
        self._scheduled_leader_messages.add(message_key)
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.leader_message_turn(
                message_id=message_id,
                from_team_id=from_team_id,
                organization_id=organization_id,
            ),
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
        """Continue an automatic claim with a separate execution turn."""
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.claimed_task_execution_turn(
                task_id=task_id,
                organization_id=organization_id,
            ),
        )

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
            return
        self._scheduled_parent_reviews.add(review_key)
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.parent_review_turn(
                child_task_id=child_task_id,
                parent_task_id=parent_task_id,
                organization_id=organization_id,
            ),
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
            return
        self._scheduled_parent_reviews.add(review_key)
        target_id = repairs_task_id or child_task_id
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.parent_repair_turn(
                child_task_id=child_task_id,
                parent_task_id=parent_task_id,
                organization_id=organization_id,
                review_status=review_status,
                repair_instructions=prompts.repair_create_instructions(
                    target_id=target_id,
                    report_phrase="defect report",
                    terminal_label="rejected/completed",
                ),
            ),
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
            return
        self._scheduled_parent_reviews.add(review_key)
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.parent_ready_turn(
                parent_task_id=parent_task_id,
                organization_id=organization_id,
            ),
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
            return
        self._scheduled_parent_reviews.add(review_key)
        target_id = repairs_task_id or child_task_id
        self._schedule_leader_turn(
            team_id=team_id,
            session_id=session_id,
            prompt=prompts.parent_child_failed_turn(
                child_task_id=child_task_id,
                parent_task_id=parent_task_id,
                organization_id=organization_id,
                failure_code=failure_code,
                failure_reason=failure_reason,
                repair_instructions=prompts.repair_create_instructions(
                    target_id=target_id,
                    report_phrase="the failure report",
                    terminal_label="failed",
                ),
            ),
            review_key=review_key,
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
        unclaimed_notification: tuple[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        key = (session_id, team_id)
        queue = self._leader_turn_queues.setdefault(key, deque())
        queue.append(
            {
                "query": prompt,
                "_org_message_key": message_key,
                "_org_review_key": review_key,
                "_org_unclaimed_notification": unclaimed_notification,
            }
        )
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
                message_key = None
                review_key = None
                if isinstance(inputs, dict):
                    message_key = inputs.pop("_org_message_key", None)
                    review_key = inputs.pop("_org_review_key", None)
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
                    await self._run_leader_turn(team_id, session_id, inputs)
                finally:
                    if message_key is not None:
                        self._scheduled_leader_messages.discard(message_key)
                    if review_key is not None:
                        self._scheduled_parent_reviews.discard(review_key)
        finally:
            self._leader_turn_workers.pop(key, None)
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
