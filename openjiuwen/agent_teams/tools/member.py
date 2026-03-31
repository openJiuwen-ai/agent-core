# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Member Module

This module implements TeamMember state management.
"""

from typing import Optional

from openjiuwen.core.common.logging import team_logger
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.status import (
    MemberStatus,
    ExecutionStatus
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.team_events import (
    EventMessage,
    MemberExecutionChangedEvent,
    MemberStatusChangedEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.tools.context import get_session_id
from openjiuwen.core.single_agent import AgentCard


class TeamMember:
    """Team Member

    This class manages a TeamMember's state.

    Attributes:
        member_id: Unique member identifier
        team_id: Team identifier
        name: Member name
        agent_card: Card of agent this member uses
        db: Team database instance
        messager: Messager instance for event publishing
    """

    def __init__(
        self,
        member_id: str,
        team_id: str,
        name: str,
        agent_card: AgentCard,
        db: TeamDatabase,
        messager: Messager,
        prompt: Optional[str] = None,
        desc: Optional[str] = None,
    ):
        """Initialize team member

        Args:
            member_id: Unique member identifier
            team_id: Team identifier
            name: Member name
            agent_card: Type of agent
            db: Team database instance
            messager: Messager instance for event publishing
        """
        self.member_id = member_id
        self.team_id = team_id
        self.name = name
        self.agent_card = agent_card
        self.db = db
        self.messager = messager
        self.prompt = prompt
        self.desc = desc

    async def status(self) -> MemberStatus:
        """Get current member status"""
        member_data = await self.db.get_member(self.member_id)
        return MemberStatus(member_data.status) if member_data else None

    async def execution_status(self) -> ExecutionStatus:
        """Get current execution status"""
        member_data = await self.db.get_member(self.member_id)
        return ExecutionStatus(member_data.execution_status) if member_data else None

    async def update_status(self, new_status: MemberStatus) -> bool:
        """Update member status with validation

        Args:
            new_status: New status

        Returns:
            True if transition is valid and successful
        """

        old_status = await self.status()
        success = await self.db.update_member_status(self.member_id, new_status.value)

        if not success:
            team_logger.error(f"Failed to update member status for {self.member_id}: {new_status.value}")
            return False

        team_logger.info(
            f"Member {self.member_id} status: {old_status.value} -> {new_status.value}"
        )

        # Publish member status changed event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(MemberStatusChangedEvent(
                    team_id=self.team_id,
                    member_id=self.member_id,
                    old_status=old_status.value,
                    new_status=new_status.value
                )),
            )
            team_logger.debug(f"Member status changed event published: {self.member_id}, {old_status.value} -> {new_status.value}")
        except Exception as e:
            team_logger.error(f"Failed to publish member status changed event for {self.member_id}: {e}")

        return True

    async def update_execution_status(self, new_status: ExecutionStatus) -> bool:
        """Update execution status with validation

        Args:
            new_status: New execution status

        Returns:
            True if transition is valid and successful
        """

        old_status = await self.execution_status()
        success = await self.db.update_member_execution_status(
            self.member_id,
            new_status.value
        )

        if not success:
            team_logger.error(f"Failed to update member execution status for {self.member_id}: {new_status.value}")
            return False

        team_logger.info(
            f"Member {self.member_id} execution status: "
            f"{old_status.value} -> {new_status.value}"
        )

        # Publish member execution status changed event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(MemberExecutionChangedEvent(
                    team_id=self.team_id,
                    member_id=self.member_id,
                    old_status=old_status.value,
                    new_status=new_status.value
                )),
            )
            team_logger.debug(f"Member execution status changed event published: "
                         f"{self.member_id}, {old_status.value} -> {new_status.value}")
        except Exception as e:
            team_logger.error(f"Failed to publish member execution status changed event for {self.member_id}: {e}")

        return True
