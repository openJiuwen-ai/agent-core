# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind already-active in-process teams into an organization."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgTaskClaimedEvent,
    OrgTaskCreatedEvent,
    OrgTaskCompletedEvent,
    OrgTaskDelegatedEvent,
    OrgTeamInvitedEvent,
    OrgTeamJoinedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.expert_adapters import (
    ExpertGroupCatalog,
    ExpertTeamLauncher,
)
from openjiuwen.agent_teams.organization.pool import get_process_org_manager, remove_process_org_manager
from openjiuwen.agent_teams.organization.schema import OrgTaskStatus, OrganizationSpec
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.agent_teams.tools.team import TeamBackend


_ORG_OWNER_LIFECYCLE_SECTION = "organization_owner_lifecycle"
_ORG_COLLABORATION_SECTION = "organization_collaboration"
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

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


class OrganizationRuntimeManager:
    """Create organizations and bind active leaders without restarting teams."""

    def __init__(self, team_runtime_manager: "TeamRuntimeManager") -> None:
        self._team_runtime_manager = team_runtime_manager
        self._membership_lock = asyncio.Lock()
        self._subscribed_topics: set[tuple[str, str, str, OrgTopic, int]] = set()
        self._team_organizations: dict[tuple[str, str], str] = {}
        self._leader_turn_queues: dict[tuple[str, str], deque[object]] = {}
        self._leader_turn_workers: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._scheduled_leader_messages: set[tuple[str, str, str]] = set()
        self._leader_turn_runner: Callable[[str, str, object], Awaitable[bool]] | None = None
        self._configured_team_provider: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None
        self._team_activator: Callable[[str, str], Awaitable[str | None]] | None = None
        self._expert_group_catalog: ExpertGroupCatalog | None = None
        self._expert_team_launcher: ExpertTeamLauncher | None = None
        self._expert_adapter_installer: Callable[["OrganizationRuntimeManager"], None] | None = None

    def set_leader_turn_runner(self, runner: Callable[[str, str, object], Awaitable[bool]]) -> None:
        """Set the host-owned path used to run an autonomous leader turn."""

        self._leader_turn_runner = runner

    def set_configured_team_provider(
        self, provider: Callable[[str], Awaitable[list[dict[str, Any]]]]
    ) -> None:
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

    def set_expert_adapter_installer(
        self, installer: Callable[["OrganizationRuntimeManager"], None] | None
    ) -> None:
        """Register a host callback that injects Catalog/Launcher on first use.

        The installer should be idempotent and must not run package scans itself;
        it only constructs and ``set_*`` the adapters. Listing/launch still happen
        when tools call ``list_expert_groups`` / ``create_and_invite_expert_team``.
        """

        self._expert_adapter_installer = installer

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
                    raise ValueError(
                        f"configured team could not be activated: {target_team_id}"
                    ) from exc
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

            member_team_ids = {leader.team_id for leader in organization.leaders}
            member_team_ids.add(owner_team_id)
            for team_id in member_team_ids:
                key = (session_id, team_id)
                worker = self._leader_turn_workers.pop(key, None)
                if worker is not None and not worker.done():
                    worker.cancel()
                self._leader_turn_queues.pop(key, None)
                self._scheduled_leader_messages = {
                    message_key
                    for message_key in self._scheduled_leader_messages
                    if message_key[:2] != key
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
                            await unsubscribe(topic.build(
                                session_id,
                                organization_id,
                                team_id if topic is OrgTopic.TEAM_INBOX else None,
                            ))
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

    async def list_expert_groups(
        self, *, capabilities: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """List host-validated AgentGroup templates; does not create Teams."""

        self._ensure_expert_adapters()
        if self._expert_group_catalog is None:
            return []
        return [
            descriptor.to_dict()
            for descriptor in self._expert_group_catalog.list(capabilities=capabilities)
        ]

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
        """Recover claimed work and discover durable matching open work.

        Topic delivery is intentionally best effort.  The task pool is the
        durable source of truth, so a freshly bound or recovered leader must
        also scan matching OPEN tasks rather than relying only on past events.
        """

        await self._resume_claimed_tasks(manager=manager, team_id=team_id, session_id=session_id)
        for message in await manager.message_service.list_leader_messages(
            team_id=team_id,
            unread_only=True,
        ):
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
            if not isinstance(event, OrgTaskCompletedEvent):
                return

            # A Team can deliberately defer a testing/integration task while
            # its inputs are still being built.  Completion of another task is
            # therefore a second, durable opportunity to claim every matching
            # OPEN task instead of leaving it stranded after the first LLM
            # decision.
            await self._schedule_matching_open_claims(
                manager=manager,
                team_id=backend.team_name,
                session_id=session_id,
                capabilities=capabilities,
                completed_task_id=event.task_id,
            )
            task = await manager.task_pool.get_task(event.task_id)
            if task is None or not task.parent_task_id or task.created_by.team_id != backend.team_name:
                return
            self._schedule_parent_completion_turn(
                team_id=backend.team_name,
                session_id=session_id,
                child_task_id=task.task_id,
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
            f" Task {trigger_task_id} just completed, so re-evaluate this open task now."
            if trigger_task_id
            else ""
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
            "when you are ready, execute it through your team workflow, and complete it with "
            "the resulting output context and output abstract."
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
            "defined scope through your Team workflow. When finished, submit one concrete result or "
            "failure report with org_update_task(action='complete'). If the task is already IN_PROGRESS "
            "or COMPLETED, do not duplicate work."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_parent_completion_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        child_task_id: str,
        parent_task_id: str,
        organization_id: str,
    ) -> None:
        prompt = (
            f"Child organization task {child_task_id} completed in {organization_id}. "
            f"Inspect its result and pending review with org_review_task, then accept or reject it. "
            f"If accepted, use the child output to continue parent task {parent_task_id}. "
            "If rejected, create at most one focused repair task for the team whose capabilities match "
            "the reported defect; include the child report and acceptance criteria in that task. Do not "
            "leave the parent waiting without either accepting/rejecting the child or creating that repair. "
            "When all direct child tasks are accepted, complete the parent task with its integrated result. "
            "For the root task, put the user-facing final delivery in org_update_task output_context.description: "
            "project structure, startup instructions, API contract, executed test results, and known limitations. "
            "Also provide a concise output_abstract."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_leader_turn(
        self,
        *,
        team_id: str,
        session_id: str,
        prompt: str,
        message_key: tuple[str, str, str] | None = None,
    ) -> None:
        key = (session_id, team_id)
        queue = self._leader_turn_queues.setdefault(key, deque())
        queue.append({"query": prompt, "_org_message_key": message_key})
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
                message_key = (
                    inputs.pop("_org_message_key", None)
                    if isinstance(inputs, dict)
                    else None
                )
                try:
                    await self._run_leader_turn(team_id, session_id, inputs)
                finally:
                    if message_key is not None:
                        self._scheduled_leader_messages.discard(message_key)
        finally:
            self._leader_turn_workers.pop(key, None)
            self._leader_turn_queues.pop(key, None)

    def _clear_leader_turn_queue(self, queue: deque[object]) -> None:
        for inputs in queue:
            if isinstance(inputs, dict):
                message_key = inputs.get("_org_message_key")
                if message_key is not None:
                    self._scheduled_leader_messages.discard(message_key)
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
