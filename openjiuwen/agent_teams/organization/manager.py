# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""High-level process-local team organization manager."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.organization.events import OrgTopic
from openjiuwen.agent_teams.organization.schema import OrgLeaderHandle, OrganizationSpec
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.tools.database import TeamDatabase


class TeamOrganizationManager:
    """Owns one organization's DB-backed task pool in the current process."""

    def __init__(
        self,
        *,
        organization_id: str,
        db: TeamDatabase,
        messager: Messager | None = None,
        session_id: str | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.messager = messager
        self.session_id = session_id
        self.task_pool = OrgTaskManager(
            db=db,
            organization_id=organization_id,
            messager=messager,
            session_id=session_id,
        )

    async def initialize(
        self,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> OrganizationSpec:
        return await self.task_pool.ensure_organization(
            display_name=display_name,
            description=description,
        )

    async def register_leader(
        self,
        *,
        team_id: str,
        leader_id: str,
        leader_member_name: str | None = None,
        capabilities: list[str] | None = None,
    ) -> OrgLeaderHandle:
        return await self.task_pool.register_leader(
            team_id=team_id,
            leader_id=leader_id,
            leader_member_name=leader_member_name,
            capabilities=capabilities,
        )

    async def subscribe(
        self,
        *,
        topic: OrgTopic,
        handler: Callable[[Any], Awaitable[None]],
        team_id: str | None = None,
    ) -> str:
        """Subscribe to an organization topic and return the concrete topic id."""

        if self.messager is None:
            raise RuntimeError("Organization manager has no messager")
        if not self.session_id:
            raise RuntimeError("Organization manager has no session_id")
        topic_id = topic.build(self.session_id, self.organization_id, team_id)
        await self.messager.subscribe(topic_id, handler)
        return topic_id

    async def unsubscribe(self, *, topic: OrgTopic, team_id: str | None = None) -> str:
        """Unsubscribe from an organization topic and return the concrete topic id."""

        if self.messager is None:
            raise RuntimeError("Organization manager has no messager")
        if not self.session_id:
            raise RuntimeError("Organization manager has no session_id")
        topic_id = topic.build(self.session_id, self.organization_id, team_id)
        await self.messager.unsubscribe(topic_id)
        return topic_id


__all__ = ["TeamOrganizationManager"]
