# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member table data access object."""

from typing import List, Optional

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError

from openjiuwen.agent_teams.schema.status import (
    EXECUTION_TRANSITIONS,
    MEMBER_DEPARTED_STATUSES,
    MEMBER_TRANSITIONS,
    MEMBER_UNREACHABLE_STATUSES,
    ExecutionStatus,
    MemberMode,
    MemberStatus,
)
from openjiuwen.agent_teams.tools.database.engine import DbSessions, get_current_time
from openjiuwen.agent_teams.tools.member_options import (
    MemberWorktreeOptions,
    set_member_worktree_options,
)
from openjiuwen.agent_teams.tools.models import TeamMember
from openjiuwen.core.common.logging import team_logger


_DEPARTED_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in MEMBER_DEPARTED_STATUSES)
_UNREACHABLE_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in MEMBER_UNREACHABLE_STATUSES)


def _valid_predecessor_values(target, transitions) -> list[str]:
    """Return the status string values that may legally transition to ``target``.

    Inverts a ``{from: [to, ...]}`` transition table into "which from-states
    reach ``target``", so an update can be expressed as one CAS UPDATE with a
    ``status IN (...)`` guard instead of a SELECT + Python validation.
    """
    return [state.value for state, targets in transitions.items() if target in targets]


class MemberDao:
    """Data access object for the team_member table."""

    def __init__(self, sessions: DbSessions) -> None:
        """Initialize member DAO with the shared read/write session provider."""
        self._sessions = sessions

    async def create_member(
        self,
        member_name: str,
        team_name: str,
        display_name: str,
        agent_card: str,
        status: str,
        *,
        role: str = "teammate",
        desc: Optional[str] = None,
        execution_status: Optional[str] = None,
        mode: str = MemberMode.BUILD_MODE.value,
        prompt: Optional[str] = None,
        options: Optional[str] = None,
    ) -> bool:
        """Create a new team member.

        Args:
            role: ``TeamRole`` enum value (``leader`` / ``teammate`` /
                ``human_agent``). Persisted so cold-recovery can rebuild
                the right runtime profile (tools / rails / prompt
                sections) without depending on the leader's in-memory
                roster. Defaults to ``"teammate"`` (the literal value
                of ``TeamRole.TEAMMATE``; spelled as a literal to keep
                this module out of the ``schema.team`` import cycle)
                because that matches the overwhelmingly common spawn
                path. HITT callers must pass
                ``role=TeamRole.HUMAN_AGENT.value`` explicitly.
            options: JSON object for extensible member configuration.
                Current shape: ``{"model_ref": {...}, "worktree": {...},
                "permissions_override": {...}}``.
        """
        async with self._sessions.write() as session:
            try:
                member = TeamMember(
                    member_name=member_name,
                    team_name=team_name,
                    display_name=display_name,
                    agent_card=agent_card,
                    status=status,
                    role=role,
                    desc=desc,
                    execution_status=execution_status,
                    mode=mode,
                    prompt=prompt,
                    options=options,
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

    async def is_human_agent(self, team_name: str, member_name: str) -> bool:
        """Return True if ``member_name`` is a human-agent member.

        Single-row probe (index-friendly) for the common case of
        checking one member's role without scanning the full roster.

        Role only — a member that has already left the team still answers
        True. Guards that must not fire for a departed member want
        :meth:`is_live_human_agent` instead.
        """
        from openjiuwen.agent_teams.schema.team import TeamRole

        async with self._sessions.read() as session:
            stmt = select(TeamMember.member_name).where(
                TeamMember.team_name == team_name,
                TeamMember.member_name == member_name,
                TeamMember.role == TeamRole.HUMAN_AGENT.value,
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def _is_human_agent_excluding(
        self,
        team_name: str,
        member_name: str,
        excluded: tuple[str, ...],
    ) -> bool:
        """Single-row human-agent probe with a status exclusion applied."""
        from openjiuwen.agent_teams.schema.team import TeamRole

        async with self._sessions.read() as session:
            stmt = select(TeamMember.member_name).where(
                TeamMember.team_name == team_name,
                TeamMember.member_name == member_name,
                TeamMember.role == TeamRole.HUMAN_AGENT.value,
                TeamMember.status.notin_(excluded),
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def _list_human_agents_excluding(self, team_name: str, excluded: tuple[str, ...]) -> list[str]:
        """Human-agent roster with a status exclusion applied."""
        from openjiuwen.agent_teams.schema.team import TeamRole

        async with self._sessions.read() as session:
            stmt = select(TeamMember.member_name).where(
                TeamMember.team_name == team_name,
                TeamMember.role == TeamRole.HUMAN_AGENT.value,
                TeamMember.status.notin_(excluded),
            )
            return list((await session.execute(stmt)).scalars().all())

    async def is_live_human_agent(self, team_name: str, member_name: str) -> bool:
        """Return True if ``member_name`` is a human-agent member still on the team.

        Excludes ``MEMBER_DEPARTED_STATUSES``. The HITT task lock keys on this
        rather than on the bare role: the lock exists to stop the leader from
        stealing work out from under a live human, and a human the leader has
        already released is no longer there to do it.
        """
        return await self._is_human_agent_excluding(team_name, member_name, _DEPARTED_STATUS_VALUES)

    async def is_reachable_human_agent(self, team_name: str, member_name: str) -> bool:
        """Return True if ``member_name`` is a human-agent member still reachable.

        Excludes only ``MEMBER_UNREACHABLE_STATUSES`` — a member that merely has
        shutdown *requested* is still reachable, and must be, or the notice that
        it was removed would never reach its controller. Message delivery keys on
        this; work guards key on the stricter :meth:`is_live_human_agent`.
        """
        return await self._is_human_agent_excluding(team_name, member_name, _UNREACHABLE_STATUS_VALUES)

    async def list_human_agent_names(self, team_name: str) -> list[str]:
        """Return member names whose ``role`` is ``human_agent``.

        Used by ``TeamBackend.human_agent_names()`` to enumerate all
        human-agent members on the team. Role only — members on their way out
        or already gone are included; see :meth:`list_live_human_agent_names`
        and :meth:`list_reachable_human_agent_names`.
        """
        from openjiuwen.agent_teams.schema.team import TeamRole

        async with self._sessions.read() as session:
            stmt = select(TeamMember.member_name).where(
                TeamMember.team_name == team_name,
                TeamMember.role == TeamRole.HUMAN_AGENT.value,
            )
            return list((await session.execute(stmt)).scalars().all())

    async def list_live_human_agent_names(self, team_name: str) -> list[str]:
        """Return human-agent member names that have not left the team.

        Batch counterpart of :meth:`is_live_human_agent`, used by the cancel-all
        path to skip the tasks held by humans still on the team while cancelling
        a departed human's leftovers like anyone else's.
        """
        return await self._list_human_agents_excluding(team_name, _DEPARTED_STATUS_VALUES)

    async def list_reachable_human_agent_names(self, team_name: str) -> list[str]:
        """Return human-agent member names that can still be delivered to.

        Batch counterpart of :meth:`is_reachable_human_agent`, used to fan a
        broadcast out to human controllers.
        """
        return await self._list_human_agents_excluding(team_name, _UNREACHABLE_STATUS_VALUES)

    async def get_member_status(self, team_name: str, member_name: str) -> Optional[str]:
        """Return one member's ``status``, or None when it does not exist.

        Narrow projection: the coordination layer consults a member's status on
        every mailbox drain to decide whether its harness may be fed, and pulling
        the whole row (serialized card, private prompt, options JSON) for one
        column would be pure waste on that path.
        """
        async with self._sessions.read() as session:
            stmt = select(TeamMember.status).where(
                TeamMember.team_name == team_name,
                TeamMember.member_name == member_name,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def member_exists(self, member_name: str, team_name: str) -> bool:
        """Check whether a member row exists, without loading it.

        An ``EXISTS`` probe for callers that only need presence (e.g.
        recipient validation) instead of ``get_member``, which loads the
        full row (``agent_card`` / ``prompt`` / ``options``).

        Args:
            member_name: Member identifier.
            team_name: Team identifier.

        Returns:
            True when a matching member row exists.
        """
        async with self._sessions.read() as session:
            stmt = select(
                exists().where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                )
            )
            return bool((await session.execute(stmt)).scalar())

    async def get_member(self, member_name: str, team_name: str) -> Optional[TeamMember]:
        """Get member information by ID."""
        async with self._sessions.read() as session:
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
        async with self._sessions.read() as session:
            stmt = select(TeamMember).where(TeamMember.team_name == team_name)
            if status is not None:
                stmt = stmt.where(TeamMember.status == status)
            return (await session.execute(stmt)).scalars().all()

    async def get_member_roster(self, team_name: str) -> List[tuple[str, str, str]]:
        """Get a projected roster of ``(member_name, display_name, status)``.

        A column projection instead of ``select(TeamMember)`` so the roster
        view never loads the heavy columns (``agent_card`` / ``prompt`` /
        ``options``) it does not render.

        Args:
            team_name: Team identifier.

        Returns:
            One ``(member_name, display_name, status)`` tuple per member.
        """
        async with self._sessions.read() as session:
            stmt = select(
                TeamMember.member_name,
                TeamMember.display_name,
                TeamMember.status,
            ).where(TeamMember.team_name == team_name)
            rows = (await session.execute(stmt)).all()
            return [(row.member_name, row.display_name, row.status) for row in rows]

    async def get_members_max_updated_at(self, team_name: str) -> int:
        """Probe MAX(``team_member.updated_at``) for the team.

        Args:
            team_name: Team identifier.

        Returns:
            Largest member update timestamp (ms), or ``0`` when no
            members exist or all rows have null ``updated_at``.
        """
        async with self._sessions.read() as session:
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
        """Update member status via a single guarded CAS UPDATE.

        The transition validation lives in the ``WHERE status IN (valid
        predecessors)`` clause, so the happy path is one UPDATE — no SELECT
        held inside the write lock. Only the rare rowcount=0 (failure) path
        reads back the row to log whether the member was missing or the
        transition was illegal.
        """
        valid_from = _valid_predecessor_values(MemberStatus(status), MEMBER_TRANSITIONS)
        async with self._sessions.write() as session:
            result = await session.execute(
                update(TeamMember)
                .where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                    TeamMember.status.in_(valid_from),
                )
                .values(status=status)
            )
            if result.rowcount == 1:
                await session.commit()
                team_logger.debug("Member %s status updated to %s", member_name, status)
                return True

            await self._log_member_update_rejection(session, member_name, team_name, TeamMember.status, status)
            return False

    async def _log_member_update_rejection(
        self,
        session,
        member_name: str,
        team_name: str,
        column,
        target: str,
    ) -> None:
        """Log the reason a guarded member update matched no row (failure path).

        Reads the PK (to distinguish a missing member from a legal-but-rejected
        transition) plus the current value of ``column`` (which may itself be
        NULL, e.g. ``execution_status``) for the invalid-transition message.
        """
        existing = await session.execute(
            select(TeamMember.member_name, column).where(
                TeamMember.member_name == member_name,
                TeamMember.team_name == team_name,
            )
        )
        row = existing.first()
        if row is None:
            team_logger.error("Member %s not found in team %s", member_name, team_name)
        else:
            team_logger.error(
                "Invalid state transition for member %s: %s -> %s",
                member_name,
                row[1],
                target,
            )

    async def try_transition_member_status(
        self,
        member_name: str,
        team_name: str,
        from_status: MemberStatus,
        to_status: MemberStatus,
    ) -> bool:
        """Atomically transition member status from from_status to to_status.

        Uses a single UPDATE with WHERE status = from_status so only
        one concurrent caller can succeed (rowcount=1). The database
        transaction ensures atomicity; if the WHERE clause no longer
        matches, rowcount=0 and the method returns False.

        Args:
            member_name: The member whose status to transition.
            team_name: The team the member belongs to.
            from_status: The expected current status (must match).
            to_status: The target status.

        Returns:
            True if the transition succeeded, False otherwise.
        """
        async with self._sessions.write() as session:
            result = await session.execute(
                update(TeamMember)
                .where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                    TeamMember.status == from_status.value,
                )
                .values(status=to_status.value)
            )
            await session.commit()
            transitioned = result.rowcount == 1
            if not transitioned:
                team_logger.debug(
                    "CAS %s -> %s for member %s failed (rowcount=%s)",
                    from_status.value,
                    to_status.value,
                    member_name,
                    result.rowcount,
                )
            return transitioned

    async def update_member_execution_status(
        self,
        member_name: str,
        team_name: str,
        execution_status: str,
    ) -> bool:
        """Update member execution status via a single guarded CAS UPDATE.

        Mirror of ``update_member_status``: the transition validation is the
        ``WHERE execution_status IN (valid predecessors)`` guard, so the happy
        path is one UPDATE with no in-lock SELECT; only rowcount=0 reads back
        to log the precise rejection reason.
        """
        valid_from = _valid_predecessor_values(ExecutionStatus(execution_status), EXECUTION_TRANSITIONS)
        async with self._sessions.write() as session:
            result = await session.execute(
                update(TeamMember)
                .where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name,
                    TeamMember.execution_status.in_(valid_from),
                )
                .values(execution_status=execution_status)
            )
            if result.rowcount == 1:
                await session.commit()
                team_logger.debug("Member %s execution status updated to %s", member_name, execution_status)
                return True

            await self._log_member_update_rejection(
                session, member_name, team_name, TeamMember.execution_status, execution_status
            )
            return False

    async def update_member_worktree(
        self,
        member_name: str,
        team_name: str,
        worktree: MemberWorktreeOptions | None = None,
        *,
        isolation: Optional[str] = None,
        worktree_path: Optional[str] = None,
    ) -> bool:
        """Update worktree isolation metadata for a member."""
        async with self._sessions.write() as session:
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
            member.options = set_member_worktree_options(
                member.options,
                worktree,
                isolation=isolation,
                worktree_path=worktree_path,
            )
            await session.commit()
            return True
