# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind already-active in-process teams into an organization."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

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
from openjiuwen.agent_teams.organization.pool import get_process_org_manager, remove_process_org_manager
from openjiuwen.agent_teams.organization.schema import (
    ORG_SUMMARY_CAPABILITY,
    ORG_SUMMARY_TASK_TYPE,
    ORG_TASK_REPAIRS_TASK_ID_KEY,
    OrganizationSpec,
    OrgSummaryExecutionStatus,
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
from openjiuwen.agent_teams.tools.database.engine import get_current_time
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

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.organization.summary import SummaryTeamFactory
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


class OrganizationRuntimeManager:
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

    def _ensure_summary_factory(self) -> None:
        """Lazily run the host installer once the SummaryTeamFactory is still missing."""
        if self._summary_team_factory is not None:
            return
        installer = self._summary_team_factory_installer
        if installer is None:
            return
        installer(self)

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

    async def _resume_summary_executions(self, *, manager: Any, session_id: str) -> None:
        """Recover in-flight Summary Teams after a rebind (§8).

        Event delivery is best-effort; the SummaryExecution table is the durable
        source of truth.  For an execution that never bound a team (interrupted
        during provisioning), re-provision it through the factory and converge on
        the same post-launch binding.  For a bound one, re-schedule a ready Summary
        Task to its running dynamic team, and re-evaluate sources for a still-
        WAITING one so a dropped ''sources ready'' notification is rebuilt.
        """
        summary_factory = self._ensure_summary_factory() or self._summary_team_factory
        for execution in await manager.task_pool.list_summary_executions():
            if execution.status in {
                OrgSummaryExecutionStatus.COMPLETED,
                OrgSummaryExecutionStatus.FAILED,
                OrgSummaryExecutionStatus.RELEASED,
            }:
                continue
            summary_task = await manager.task_pool.get_task(execution.summary_task_id)
            if summary_task is None:
                continue
            if summary_task.status is OrgTaskStatus.COMPLETED:
                continue
            summary_team_id = execution.summary_team_id
            if not summary_team_id:
                if summary_factory is None:
                    continue
                root_task_id = str(summary_task.root_task_id or execution.root_task_id)
                from_team_id = summary_task.created_by.team_id or (await self._owner_team_id(manager))
                try:
                    launched = await summary_factory.recover(
                        execution_id=execution.execution_id,
                        organization_id=manager.organization_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        owner_team_id=from_team_id,
                        session_id=session_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    await self._fail_summary_provision(
                        manager=manager,
                        execution=execution,
                        from_team_id=from_team_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        failure_reason=str(exc),
                    )
                    continue
                summary_team_id = launched.team_id
                await self._complete_summary_provision(
                    manager=manager,
                    execution=execution,
                    launched=launched,
                    from_team_id=from_team_id,
                    root_task_id=root_task_id,
                    summary_task_id=execution.summary_task_id,
                )
            evaluation = await manager.task_pool.evaluate_summary_sources(summary_task_id=execution.summary_task_id)
            if not evaluation.get("ready"):
                continue
            self._schedule_summary_turn(
                manager=manager,
                session_id=session_id,
                summary_team_id=summary_team_id,
                summary_task_id=execution.summary_task_id,
                root_task_id=execution.root_task_id,
            )

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
        messager = backend.messager
        if messager is None:
            return

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
                # OPEN tasks. Parent-team review wake is driven by
                # OrgTaskReviewRequestedEvent so it aligns with PENDING review.
                await self._schedule_matching_open_claims(
                    manager=manager,
                    team_id=backend.team_name,
                    session_id=session_id,
                    capabilities=capabilities,
                    completed_task_id=event.task_id,
                )
                return
            if isinstance(event, OrgTaskFailedEvent):
                task = await manager.task_pool.get_task(event.task_id)
                if self._is_unclaimed_expiration(task, event):
                    # The durable expiration inbox request also covers root tasks.
                    return
                if task is None:
                    return
                if task.task_type == ORG_SUMMARY_TASK_TYPE:
                    # A failed/cancelled Summary Task releases its dynamic team.
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

        async def _on_org_event(message: Any) -> None:
            summary_factory = self._ensure_summary_factory() or self._summary_team_factory
            if summary_factory is None:
                return
            event = message.get_payload()
            if isinstance(event, OrgSummaryTaskCreatedEvent):
                await self._handle_summary_task_created(
                    manager=manager,
                    summary_factory=summary_factory,
                    event=event,
                    session_id=session_id,
                )
                return
            if isinstance(event, OrgSummaryProvisionedEvent):
                self._schedule_summary_turn(
                    manager=manager,
                    session_id=session_id,
                    summary_team_id=event.summary_team_id,
                    summary_task_id=event.summary_task_id,
                    root_task_id=event.root_task_id,
                )
                return
            if isinstance(event, OrgSummaryProvisionFailedEvent):
                await self._handle_summary_provision_failed(manager=manager, event=event, session_id=session_id)
                return
            if isinstance(event, OrgSummarySourcesUpdatedEvent):
                await self._handle_summary_sources_updated(
                    manager=manager,
                    event=event,
                    session_id=session_id,
                )
                return
            if isinstance(event, OrgSummarySourcesReadyEvent):
                await self._handle_summary_sources_ready(manager=manager, event=event, session_id=session_id)
                return
            if isinstance(event, OrgSummarySourceFailedEvent):
                await self._handle_summary_source_failed(manager=manager, event=event, session_id=session_id)
                return
            if isinstance(event, OrgSummaryCompletedEvent):
                await self._handle_summary_completed(manager=manager, event=event, session_id=session_id)

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

    async def _handle_summary_task_created(
        self,
        *,
        manager: Any,
        summary_factory: SummaryTeamFactory,
        event: OrgSummaryTaskCreatedEvent,
        session_id: str,
    ) -> None:
        """Provision a dynamic Summary Team and delegate the Summary Task to it (§4.4.1)."""
        task = await manager.task_pool.get_task(event.summary_task_id)
        if task is None:
            return
        root_task_id = str(task.root_task_id or event.summary_task_id)
        from_team_id = task.created_by.team_id or (await self._owner_team_id(manager))
        execution = await manager.task_pool.create_summary_execution(
            root_task_id=root_task_id,
            summary_task_id=event.summary_task_id,
        )
        try:
            launched = await summary_factory.provision(
                organization_id=manager.organization_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                owner_team_id=from_team_id,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._fail_summary_provision(
                manager=manager,
                execution=execution,
                from_team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                failure_reason=str(exc),
            )
            return
        await self._complete_summary_provision(
            manager=manager,
            execution=execution,
            launched=launched,
            from_team_id=from_team_id,
            root_task_id=root_task_id,
            summary_task_id=event.summary_task_id,
        )

    async def _complete_summary_provision(
        self,
        *,
        manager: Any,
        execution: Any,
        launched: Any,
        from_team_id: str,
        root_task_id: str,
        summary_task_id: str,
    ) -> None:
        """Register the team, mark the execution RUNNING, and delegate the task.

        Shared by the fresh-provision event path (§4.4.1) and the §8 recovery scan
        so both converge on the same post-launch binding.
        """
        await manager.register_leader(
            team_id=launched.team_id,
            leader_id=launched.leader_id,
            leader_member_name=launched.leader_id,
            capabilities=[ORG_SUMMARY_CAPABILITY],
        )
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.RUNNING,
            summary_team_id=launched.team_id,
        )
        summary_task = await manager.task_pool.get_task(summary_task_id)
        already_delegated = summary_task is not None and summary_task.assignment.team_id == launched.team_id
        if not already_delegated:
            await manager.task_pool.delegate_task(
                task_id=summary_task_id,
                from_team_id=from_team_id,
                to_team_id=launched.team_id,
            )
        # Land the dynamic team id on the root task's aggregation too, so
        # org_view_tasks on the root shows summary_team_id instead of None (§4.4.1).
        await manager.task_pool.bind_root_summary_team(
            root_task_id=root_task_id,
            summary_team_id=launched.team_id,
        )
        await manager.task_pool.publish_event(
            OrgSummaryProvisionedEvent(
                organization_id=manager.organization_id,
                team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=summary_task_id,
                summary_team_id=launched.team_id,
            )
        )

    async def _fail_summary_provision(
        self,
        *,
        manager: Any,
        execution: Any,
        from_team_id: str,
        root_task_id: str,
        summary_task_id: str,
        failure_reason: str,
    ) -> None:
        """Mark the execution FAILED, fail the Summary Task, wake the root leader (§4.4.3 / §8)."""
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.FAILED,
        )
        # Surface the failure on the Summary Task itself (failure_code), not only
        # the SummaryExecution, so org_view_tasks shows SUMMARY_PROVISION_FAILED.
        await manager.task_pool.fail_summary_task(
            summary_task_id=summary_task_id,
            failure_reason=failure_reason,
        )
        await manager.task_pool.publish_event(
            OrgSummaryProvisionFailedEvent(
                organization_id=manager.organization_id,
                team_id=from_team_id,
                summary_task_id=summary_task_id,
                root_task_id=root_task_id,
                failure_reason=failure_reason,
            )
        )

    async def _handle_summary_provision_failed(
        self,
        *,
        manager: Any,
        event: OrgSummaryProvisionFailedEvent,
        session_id: str,
    ) -> None:
        """Wake the root leader so it can repair, replace, or terminate the summary (§4.4.3)."""
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=event.root_task_id,
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_sources_updated(
        self,
        *,
        manager: Any,
        event: OrgSummarySourcesUpdatedEvent,
        session_id: str,
    ) -> None:
        """Evaluate bound sources after an attach: ready -> SourcesReady, failed -> SourceFailed."""
        evaluation = await manager.task_pool.evaluate_summary_sources(summary_task_id=event.summary_task_id)
        if evaluation.get("ready"):
            await manager.task_pool.publish_event(
                OrgSummarySourcesReadyEvent(
                    organization_id=manager.organization_id,
                    team_id=None,
                    summary_task_id=event.summary_task_id,
                )
            )
            return
        source_failed = evaluation.get("source_failed")
        if source_failed is not None:
            await manager.task_pool.publish_event(
                OrgSummarySourceFailedEvent(
                    organization_id=manager.organization_id,
                    team_id=None,
                    summary_task_id=event.summary_task_id,
                    source_task_id=source_failed,
                    failure_reason=str(evaluation.get("reason") or "source task failed"),
                )
            )

    async def _handle_summary_sources_ready(
        self,
        *,
        manager: Any,
        event: OrgSummarySourcesReadyEvent,
        session_id: str,
    ) -> None:
        """Queue the dynamic Summary Leader turn that aggregates ready sources."""
        summary_task = await manager.task_pool.get_task(event.summary_task_id)
        if summary_task is None:
            return
        root_task_id = str(summary_task.root_task_id or event.summary_task_id)
        execution = await self._running_summary_execution(manager=manager, summary_task_id=event.summary_task_id)
        if execution is None or not execution.summary_team_id:
            return
        self._schedule_summary_turn(
            manager=manager,
            session_id=session_id,
            summary_team_id=execution.summary_team_id,
            summary_task_id=event.summary_task_id,
            root_task_id=root_task_id,
        )

    async def _handle_summary_source_failed(
        self,
        *,
        manager: Any,
        event: OrgSummarySourceFailedEvent,
        session_id: str,
    ) -> None:
        """Wake the root leader to supplement, replace, or terminate the failed source."""
        summary_task = await manager.task_pool.get_task(event.summary_task_id)
        if summary_task is None:
            return
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=str(summary_task.root_task_id or event.summary_task_id),
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_completed(
        self,
        *,
        manager: Any,
        event: OrgSummaryCompletedEvent,
        session_id: str,
    ) -> None:
        """Release the dynamic Summary Team and wake the root leader to inject the result."""
        await self._release_summary_executions(
            manager=manager,
            summary_task_id=event.summary_task_id,
            session_id=session_id,
        )
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=event.root_task_id,
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_task_failed(
        self,
        *,
        manager: Any,
        task_id: str,
        session_id: str,
    ) -> None:
        """Release the dynamic Summary Team and wake the root leader on failure/cancel.

        A failed or cancelled Summary Task otherwise leaves its dynamic team
        running and its execution stuck in RUNNING, since no completion event
        ever arrives to trigger release.
        """
        summary_task = await manager.task_pool.get_task(task_id)
        if summary_task is None:
            return
        await self._release_summary_executions(
            manager=manager,
            summary_task_id=task_id,
            session_id=session_id,
        )
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=str(summary_task.root_task_id or task_id),
            summary_task_id=task_id,
        )

    async def _release_summary_executions(self, *, manager: Any, summary_task_id: str, session_id: str) -> None:
        """Release every non-RELEASED dynamic Summary Team for a Summary Task."""
        executions = await manager.task_pool.list_summary_executions(summary_task_id=summary_task_id)
        for execution in executions:
            if execution.status is OrgSummaryExecutionStatus.RELEASED:
                continue
            await self._release_summary_execution(
                manager=manager,
                execution=execution,
                session_id=session_id,
            )

    async def _release_summary_execution(self, *, manager: Any, execution: Any, session_id: str) -> None:
        summary_factory = self._ensure_summary_factory() or self._summary_team_factory
        if summary_factory is None:
            return
        if execution.summary_team_id:
            try:
                await summary_factory.release(
                    execution_id=execution.execution_id,
                    summary_team_id=execution.summary_team_id,
                    session_id=session_id,
                )
            except Exception:  # noqa: BLE001
                logger.warning("Failed to release summary execution %s", execution.execution_id, exc_info=True)
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.RELEASED,
            released_at=get_current_time(),
        )

    async def _running_summary_execution(self, *, manager: Any, summary_task_id: str) -> Any | None:
        for execution in await manager.task_pool.list_summary_executions(summary_task_id=summary_task_id):
            if execution.summary_team_id and execution.status is not OrgSummaryExecutionStatus.RELEASED:
                return execution
        return None

    async def _owner_team_id(self, manager: Any) -> str:
        organization = await manager.get_organization()
        return organization.owner_team_id if organization is not None else ""

    def _schedule_summary_turn(
        self,
        *,
        manager: Any,
        session_id: str,
        summary_team_id: str,
        summary_task_id: str,
        root_task_id: str,
    ) -> None:
        prompt = (
            f"You are the Summary Team aggregating root task {root_task_id} in {manager.organization_id}. "
            f"Organization summary task {summary_task_id} is delegated to your team. "
            "Read its bound source outputs with org_view_tasks(action='get') and "
            "org_view_child_tasks, integrate them into one final result, then call "
            f"org_update_task(action='start') and org_update_task(action='complete') on "
            f"{summary_task_id} in the same workflow. If material content is missing, create "
            f"a focused supplementary task with org_create_task(parent_task_id='{root_task_id}') "
            "rather than fabricating output."
        )
        self._schedule_leader_turn(team_id=summary_team_id, session_id=session_id, prompt=prompt)

    async def _schedule_summary_root_turn(
        self,
        *,
        manager: Any,
        session_id: str,
        root_task_id: str,
        summary_task_id: str,
    ) -> None:
        team_id = await self._resolve_root_team_id(manager=manager, root_task_id=root_task_id)
        if not team_id:
            return
        prompt = (
            f"Organization summary task {summary_task_id} for root task {root_task_id} in "
            f"{manager.organization_id} needs your attention. Inspect it with "
            "org_view_tasks(action='get'). If summary sources failed or provisioning failed, "
            "supplement them with new source tasks, create a replacement summary task, or "
            "fail/terminate toward the root as appropriate. When the summary completes, "
            "inject its final result into the root's output context."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    async def _resolve_root_team_id(self, *, manager: Any, root_task_id: str) -> str:
        root = await manager.task_pool.get_task(root_task_id)
        if root is not None:
            if root.assignment.team_id:
                return root.assignment.team_id
            if root.created_by.team_id:
                return root.created_by.team_id
        return await self._owner_team_id(manager)

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
            "Inspect it with org_view_tasks(action='get'). If it is still assigned to your team and "
            "its status is CLAIMED, immediately call org_update_task(action='start'). Then execute the "
            "defined scope through your Team workflow. If an independent part requires another organization "
            "team's capabilities, keep this parent task assigned to your team and create a focused OPEN child "
            f"with org_create_task(parent_task_id='{task_id}'). Give each child a clear scope, acceptance "
            "criteria, and only the capabilities it needs; do not set delegated_to_team_id. Track children "
            "with org_view_child_tasks and do not complete the parent until its direct children are completed "
            "and accepted. When the task is actually complete, submit one concrete result with "
            "org_update_task(action='complete'). If the task is already IN_PROGRESS or COMPLETED, do not "
            "duplicate work."
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
            return
        self._scheduled_parent_reviews.add(review_key)
        prompt = (
            f"Child organization task {child_task_id} completed in {organization_id}. "
            f"Inspect its result with org_review_task, then accept or reject it. "
            f"If accepted, use the child output to continue parent task {parent_task_id}. "
            "If rejected, create a repair with org_create_task "
            f"(set repairs_task_id={child_task_id} on the original sibling; never repair-of-repair; "
            "do not org_delegate_task the rejected child). "
            "When all direct children are accepted or superseded by an accepted repair, "
            "complete the parent. For a root task, put the user-facing delivery in "
            "org_update_task output_context.description and provide output_abstract."
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
            return
        self._scheduled_parent_reviews.add(review_key)
        prompt = (
            f"All direct child tasks for parent organization task {parent_task_id} "
            f"in {organization_id} are accepted or superseded by an accepted repair. "
            "Integrate the child outputs and call org_update_task(action='complete') on the "
            "parent with the final output_context and output_abstract. For a root task, put the "
            "user-facing delivery in output_context.description."
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
