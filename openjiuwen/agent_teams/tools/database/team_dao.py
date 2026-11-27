# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team table data access object."""

from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from openjiuwen.agent_teams.tools.database.engine import DbSessions, get_current_time
from openjiuwen.agent_teams.tools.models import Team, TeamMember
from openjiuwen.core.common.logging import team_logger


class TeamDao:
    """Data access object for the team_info table."""

    def __init__(self, sessions: DbSessions) -> None:
        """Initialize team DAO with the shared read/write session provider."""
        self._sessions = sessions

    async def create_team(
        self,
        team_name: str,
        display_name: str,
        leader_member_name: str,
        *,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
        dispatch_mode: str = "autonomous",
        enable_task_verification: bool = False,
    ) -> bool:
        """Create a new team.

        The optional columns are keyword-only: ``desc`` / ``prompt`` sit next to
        each other and ``dispatch_mode`` / ``enable_task_verification`` carry the
        effective per-instance capability choices made at ``build_team`` time
        (F_62, persisted so cold recovery restores them) — naming them at the
        call site is what keeps them from being swapped.
        """
        async with self._sessions.write() as session:
            try:
                ts = get_current_time()
                team = Team(
                    team_name=team_name,
                    display_name=display_name,
                    leader_member_name=leader_member_name,
                    desc=desc,
                    prompt=prompt,
                    dispatch_mode=dispatch_mode,
                    enable_task_verification=enable_task_verification,
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
        async with self._sessions.read() as session:
            result = await session.execute(select(Team).where(Team.team_name == team_name))
            return result.scalar_one_or_none()

    async def team_exists(self, team_name: str) -> bool:
        """Check whether a team row exists in the static table.

        Lighter than ``get_team`` when only existence is needed: selects the
        team name column instead of hydrating a full ORM object.
        """
        async with self._sessions.read() as session:
            result = await session.execute(
                select(Team.team_name).where(Team.team_name == team_name)
            )
            return result.scalar_one_or_none() is not None

    async def delete_team(self, team_name: str) -> bool:
        """Delete a team (cascade delete will remove related records).

        Uses a single conditional ``DELETE ... WHERE team_name = ?`` and
        checks ``rowcount`` rather than a SELECT-then-``session.delete``
        read-modify-write: the conditional DELETE is atomic at the row level
        on every backend, so a concurrent caller that already deleted the
        row yields ``rowcount == 0`` and we return ``False`` instead of
        clobbering a re-created team.

        Child rows (``team_member``) carry an ``ondelete=CASCADE`` FK, so the
        database removes them in the same statement whenever FK enforcement
        is on (``PRAGMA foreign_keys=ON`` on SQLite, always on for PG/MySQL).
        We additionally issue an explicit ``DELETE FROM team_member`` after
        the team row delete so cleanup does not silently rely on the FK
        cascade firing — a legacy SQLite file or a connection that lost the
        PRAGMA would otherwise leave orphan members pointing at a team that
        no longer exists. The extra DELETE is a no-op when the cascade
        already removed the rows in the same statement (issue #115 review
        follow-up). Per-session dynamic tables (``team_task_*``,
        ``team_message_*``, ...) are torn down by ``_drop_cur_session_tables``
        / ``force_delete_team_session`` and rely on the same FK cascade for
        cross-session rows.

        Returns:
            ``True`` if a row was deleted, ``False`` when the team did
            not exist. Callers that treat "missing" as success can map
            ``False`` themselves.
        """
        async with self._sessions.write() as session:
            result = await session.execute(
                delete(Team).where(Team.team_name == team_name)
            )
            deleted = (result.rowcount or 0) == 1
            if not deleted:
                team_logger.debug("Team %s not found for deletion", team_name)
                return False
            # Explicitly remove team_member rows so cleanup does not depend
            # solely on the FK ondelete=CASCADE firing (PRAGMA foreign_keys
            # may be off on a legacy SQLite connection). No-op when the
            # cascade already removed them in the same statement.
            await session.execute(
                delete(TeamMember).where(TeamMember.team_name == team_name)
            )
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
        async with self._sessions.read() as session:
            result = await session.execute(select(Team.updated_at).where(Team.team_name == team_name))
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0
