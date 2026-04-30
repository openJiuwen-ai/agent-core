# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team table data access object."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import Team
from openjiuwen.core.common.logging import team_logger


class TeamDao:
    """Data access object for the team_info table."""

    def __init__(self, session_local: async_sessionmaker) -> None:
        """Initialize team DAO with the shared session factory."""
        self._session_local = session_local

    async def create_team(
        self,
        team_name: str,
        display_name: str,
        leader_member_name: str,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> bool:
        """Create a new team."""
        async with self._session_local() as session:
            try:
                ts = get_current_time()
                team = Team(
                    team_name=team_name,
                    display_name=display_name,
                    leader_member_name=leader_member_name,
                    desc=desc,
                    prompt=prompt,
                    created=ts,
                    updated_at=ts,
                )
                session.add(team)
                await session.commit()
                team_logger.info("Team %s created", team_name)
                return True
            except IntegrityError as e:
                await session.rollback()
                team_logger.error("Team %s already exists: %s", team_name, e)
                return False

    async def get_team(self, team_name: str) -> Optional[Team]:
        """Get team information by ID."""
        async with self._session_local() as session:
            result = await session.execute(select(Team).where(Team.team_name == team_name))
            return result.scalar_one_or_none()

    async def delete_team(self, team_name: str) -> bool:
        """Delete a team (cascade delete will remove related records).

        Returns:
            ``True`` if a row was deleted, ``False`` when the team did
            not exist. Callers that treat "missing" as success can map
            ``False`` themselves.
        """
        async with self._session_local() as session:
            result = await session.execute(select(Team).where(Team.team_name == team_name))
            team = result.scalar_one_or_none()
            if not team:
                team_logger.debug("Team %s not found for deletion", team_name)
                return False

            await session.delete(team)
            await session.commit()
            team_logger.info("Team %s deleted", team_name)
            return True

    async def get_team_updated_at(self, team_name: str) -> int:
        """Probe ``team_info.updated_at`` for change detection.

        Args:
            team_name: Team identifier.

        Returns:
            Last update timestamp (ms), or ``0`` when the row is
            missing or the column is null.
        """
        async with self._session_local() as session:
            result = await session.execute(select(Team.updated_at).where(Team.team_name == team_name))
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0
