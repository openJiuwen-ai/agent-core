# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member table data access object."""

from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from openjiuwen.agent_teams.schema.status import (
    EXECUTION_TRANSITIONS,
    MEMBER_TRANSITIONS,
    ExecutionStatus,
    MemberMode,
    MemberStatus,
    is_valid_transition,
)
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import TeamMember
from openjiuwen.core.common.logging import team_logger


class MemberDao:
    """Data access object for the team_member table."""

    def __init__(self, session_local: async_sessionmaker) -> None:
        """Initialize member DAO with the shared session factory."""
        self._session_local = session_local

    async def create_member(
        self,
        member_name: str,
        team_name: str,
        display_name: str,
        agent_card: str,
        status: str,
        *,
        desc: Optional[str] = None,
        execution_status: Optional[str] = None,
        mode: str = MemberMode.BUILD_MODE.value,
        prompt: Optional[str] = None,
        model_ref_json: Optional[str] = None,
    ) -> bool:
        """Create a new team member."""
        async with self._session_local() as session:
            try:
                member = TeamMember(
                    member_name=member_name,
                    team_name=team_name,
                    display_name=display_name,
                    agent_card=agent_card,
                    status=status,
                    desc=desc,
                    execution_status=execution_status,
                    mode=mode,
                    prompt=prompt,
                    model_ref_json=model_ref_json,
                    updated_at=get_current_time(),
                )
                session.add(member)
                await session.commit()
                team_logger.info("Member %s created", member_name)
                return True
            except IntegrityError:
                await session.rollback()
                team_logger.error("Member %s already exists", member_name)
                return False

    async def get_member(self, member_name: str, team_name: str) -> Optional[TeamMember]:
        """Get member information by ID."""
        async with self._session_local() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                )
            )
            return result.scalar_one_or_none()

    async def get_team_members(self, team_name: str, status: str | None = None) -> List[TeamMember]:
        """Get members for a team, optionally filtered by status.

        Args:
            team_name: Team identifier.
            status: If provided, only return members with this status.
        """
        async with self._session_local() as session:
            stmt = select(TeamMember).where(TeamMember.team_name == team_name)
            if status is not None:
                stmt = stmt.where(TeamMember.status == status)
            return (await session.execute(stmt)).scalars().all()

    async def get_members_max_updated_at(self, team_name: str) -> int:
        """Probe MAX(``team_member.updated_at``) for the team.

        Args:
            team_name: Team identifier.

        Returns:
            Largest member update timestamp (ms), or ``0`` when no
            members exist or all rows have null ``updated_at``.
        """
        async with self._session_local() as session:
            result = await session.execute(
                select(func.max(TeamMember.updated_at)).where(TeamMember.team_name == team_name)
            )
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0

    async def update_member_status(
        self,
        member_name: str,
        team_name: str,
        status: str,
    ) -> bool:
        """Update member status."""
        async with self._session_local() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                )
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error("Member %s not found in team %s", member_name, team_name)
                return False

            if not is_valid_transition(
                MemberStatus(member.status),
                MemberStatus(status),
                MEMBER_TRANSITIONS,
            ):
                team_logger.error(
                    "Invalid state transition for member %s: %s -> %s",
                    member_name,
                    member.status,
                    status,
                )
                return False

            member.status = status
            await session.commit()
            team_logger.debug("Member %s status updated to %s", member_name, status)
            return True

    async def update_member_execution_status(
        self,
        member_name: str,
        team_name: str,
        execution_status: str,
    ) -> bool:
        """Update member execution status."""
        async with self._session_local() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                )
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error("Member %s not found in team %s", member_name, team_name)
                return False

            if not is_valid_transition(
                ExecutionStatus(member.execution_status),
                ExecutionStatus(execution_status),
                EXECUTION_TRANSITIONS,
            ):
                team_logger.error(
                    "Invalid state transition for member %s: %s -> %s",
                    member_name,
                    member.execution_status,
                    execution_status,
                )
                return False

            member.execution_status = execution_status
            await session.commit()
            team_logger.debug(
                "Member %s execution status updated to %s",
                member_name,
                execution_status,
            )
            return True
