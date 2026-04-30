# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Member Module

This module implements TeamMember state management.
"""

from typing import Optional

from openjiuwen.core.common.logging import team_logger
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.schema.status import (
    MemberStatus,
    ExecutionStatus
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberExecutionChangedEvent,
    MemberStatusChangedEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.core.single_agent import AgentCard


class TeamMember:
    """Team Member

    This class manages a TeamMember's state.

    Attributes:
        member_name: Unique member identifier (semantic slug)
        team_name: Team identifier
        display_name: Human-readable member display label
        agent_card: Card of agent this member uses
        db: Team database instance
        messager: Messager instance for event publishing
    """

    def __init__(
        self,
        member_name: str,
        team_name: str,
        agent_card: AgentCard,
        db: TeamDatabase,
        messager: Messager,
        display_name: Optional[str] = None,
        prompt: Optional[str] = None,
        desc: Optional[str] = None,
    ):
        """Initialize team member.

        Args:
            member_name: Unique member identifier (semantic slug).
            team_name: Team identifier.
            agent_card: Type of agent.
            db: Team database instance.
            messager: Messager instance for event publishing.
            display_name: Optional human-readable display label.
                Defaults to ``member_name``.
            prompt: Optional startup prompt.
            desc: Optional persona description.
        """
        self.member_name = member_name
        self.team_name = team_name
        self.display_name = display_name or member_name
        self.agent_card = agent_card
        self.db = db
        self.messager = messager
        self.prompt = prompt
        self.desc = desc

    async def status(self) -> MemberStatus:
        """Get current member status"""
        member_data = await self.db.member.get_member(self.member_name, self.team_name)
        return MemberStatus(member_data.status) if member_data else None

    async def execution_status(self) -> ExecutionStatus:
        """Get current execution status"""
        member_data = await self.db.member.get_member(self.member_name, self.team_name)
        return ExecutionStatus(member_data.execution_status) if member_data else None

    async def update_status(self, new_status: MemberStatus) -> bool:
        """Update member status with validation.

        No-op short-circuit when the new status equals the current one:
        skip the DB write and the status-changed event, so callers can
        idempotently re-assert a status (e.g. recovering from ERROR by
        always pulling back to READY at round entry) without polluting
        the event stream.

        Args:
            new_status: New status

        Returns:
            True if transition is valid and successful
        """

        old_status = await self.status()
        if old_status == new_status:
            return True
        success = await self.db.member.update_member_status(self.member_name, self.team_name, new_status.value)

        if not success:
            team_logger.error(f"Failed to update member status for {self.member_name}: {new_status.value}")
            return False

        team_logger.debug(
            f"Member {self.member_name} status: {old_status.value} -> {new_status.value}"
        )

        # Publish member status changed event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MemberStatusChangedEvent(
                    team_name=self.team_name,
                    member_name=self.member_name,
                    old_status=old_status.value,
                    new_status=new_status.value
                )),
            )
            team_logger.debug(f"Member status changed event published: {self.member_name}, "
                              f"{old_status.value} -> {new_status.value}")
        except Exception as e:
            team_logger.error(f"Failed to publish member status changed event for {self.member_name}: {e}")

        return True

    async def update_execution_status(self, new_status: ExecutionStatus) -> bool:
        """Update execution status with validation

        Args:
            new_status: New execution status

        Returns:
            True if transition is valid and successful
        """

        old_status = await self.execution_status()
        success = await self.db.member.update_member_execution_status(
            self.member_name,
            self.team_name,
            new_status.value
        )

        if not success:
            team_logger.error(f"Failed to update member execution status for {self.member_name}: {new_status.value}")
            return False

        team_logger.debug(
            f"Member {self.member_name} execution status: "
            f"{old_status.value} -> {new_status.value}"
        )

        # Publish member execution status changed event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MemberExecutionChangedEvent(
                    team_name=self.team_name,
                    member_name=self.member_name,
                    old_status=old_status.value,
                    new_status=new_status.value
                )),
            )
            team_logger.debug(f"Member execution status changed event published: "
                         f"{self.member_name}, {old_status.value} -> {new_status.value}")
        except Exception as e:
            team_logger.error(f"Failed to publish member execution status changed event for {self.member_name}: {e}")

        return True
