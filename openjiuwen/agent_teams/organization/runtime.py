# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind already-active in-process teams into an organization."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgTaskCreatedEvent,
    OrgTaskDelegatedEvent,
    OrgTeamInvitedEvent,
    OrgTeamJoinedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.pool import get_process_org_manager
from openjiuwen.agent_teams.organization.schema import OrganizationSpec
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.agent_teams.tools.team import TeamBackend

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


class OrganizationRuntimeManager:
    """Create organizations and bind active leaders without restarting teams."""

    def __init__(self, team_runtime_manager: "TeamRuntimeManager") -> None:
        self._team_runtime_manager = team_runtime_manager
        self._membership_lock = asyncio.Lock()
        self._subscribed_topics: set[tuple[str, str, str, OrgTopic]] = set()
        self._claim_turns: set[asyncio.Task[Any]] = set()
        self._leader_turn_runner: Callable[[str, str, object], Awaitable[bool]] | None = None

    def set_leader_turn_runner(self, runner: Callable[[str, str, object], Awaitable[bool]]) -> None:
        """Set the host-owned path used to run an autonomous leader turn."""

        self._leader_turn_runner = runner

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
        """Invite an active team and immediately bind it in the first version."""

        async with self._membership_lock:
            inviter_agent, inviter_backend = await self._resolve_leader(inviter_team_id, session_id)
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

    async def _bind_team(self, *, agent: "TeamAgent", backend: TeamBackend, manager: Any, session_id: str) -> None:
        backend.org_task_manager = manager.task_pool
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
                team_id=backend.team_name,
                leader_id=leader_id,
            ):
                add_tool(tool)
        await self._subscribe_team_events(backend, manager, session_id)

    async def _subscribe_team_events(self, backend: TeamBackend, manager: Any, session_id: str) -> None:
        messager = backend.messager
        if messager is None:
            return

        async def _on_task_event(message: Any) -> None:
            if getattr(message, "event_type", None) != OrgEvent.TASK_CREATED:
                return
            event = message.get_payload()
            if not isinstance(event, OrgTaskCreatedEvent):
                return
            if event.team_id == backend.team_name:
                return
            self._schedule_claim_turn(
                team_id=backend.team_name,
                session_id=session_id,
                task_id=event.task_id,
                organization_id=manager.organization_id,
            )

        async def _on_inbox_event(message: Any) -> None:
            if getattr(message, "event_type", None) != OrgEvent.TASK_DELEGATED:
                return
            event = message.get_payload()
            if not isinstance(event, OrgTaskDelegatedEvent):
                return
            self._schedule_delegated_turn(
                team_id=backend.team_name,
                session_id=session_id,
                task_id=event.task_id,
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
        key = (organization_id, session_id, team_id, topic)
        if key in self._subscribed_topics:
            return
        topic_id = topic.build(session_id, organization_id, team_id if topic is OrgTopic.TEAM_INBOX else None)
        await messager.subscribe(topic_id, handler)
        self._subscribed_topics.add(key)

    def _schedule_claim_turn(self, *, team_id: str, session_id: str, task_id: str, organization_id: str) -> None:
        prompt = (
            f"Organization task {task_id} is available in {organization_id}. "
            "Use org_view_tasks(action='get') to inspect it and org_view_tasks(action='open') "
            "to check the pool. Claim it with org_claim_task only when your team can satisfy "
            "the required capabilities and has capacity. If it is unsuitable or already claimed, do nothing."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_delegated_turn(self, *, team_id: str, session_id: str, task_id: str, organization_id: str) -> None:
        prompt = (
            f"Organization task {task_id} in {organization_id} was delegated to your team. "
            "Inspect it with org_view_tasks(action='get'), then use org_update_task(action='start') "
            "when you are ready and execute it through your team workflow."
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    def _schedule_leader_turn(self, *, team_id: str, session_id: str, prompt: str) -> None:
        task = asyncio.create_task(self._run_leader_turn(team_id, session_id, {"query": prompt}))
        self._claim_turns.add(task)
        task.add_done_callback(self._claim_turns.discard)

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
