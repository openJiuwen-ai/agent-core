# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team monitor that observes a leader's TeamAgent.

Provides query APIs for team info, members, tasks, and messages,
plus a real-time event stream via an async iterator.
"""

from __future__ import annotations

import asyncio
# Avoid hard imports to keep the module self-contained;
# runtime types are referenced by string or duck-typed.
from typing import (
    AsyncIterator,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.monitor.models import (
    MemberInfo,
    MessageInfo,
    MonitorEvent,
    TaskInfo,
    TeamInfo,
)
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.events import EventMessage
    from openjiuwen.agent_teams.tools.database import TeamDatabase


class TeamMonitor:
    """Observes a leader TeamAgent for state queries and real-time events.

    Lifecycle:
        monitor = create_monitor(team_agent)
        await monitor.start()      # begin listening
        async for evt in monitor.events():
            ...                     # consume events
        await monitor.stop()       # clean up

    Attributes:
        team_id: The team being monitored.
        session_id: Current session identifier.
    """

    def __init__(
        self,
        team_id: str,
        session_id: str,
        db: TeamDatabase,
        team_agent: TeamAgent,
    ) -> None:
        """Initialize the monitor.

        Args:
            team_id: Team identifier.
            session_id: Session identifier for topic routing.
            db: TeamDatabase instance for state queries.
            team_agent: Leader TeamAgent to register event listener on.
        """
        self._team_id = team_id
        self._session_id = session_id
        self._db = db
        self._team_agent = team_agent
        self._event_queue: asyncio.Queue[MonitorEvent | None] = asyncio.Queue()
        self._started = False

    @property
    def team_id(self) -> str:
        return self._team_id

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start monitoring by registering as an event listener on the leader."""
        if self._started:
            return
        self._team_agent.add_event_listener(self._on_event)
        self._started = True
        team_logger.info("TeamMonitor started for team {}", self._team_id)

    async def stop(self) -> None:
        """Stop monitoring, unregister listener, and terminate the event stream."""
        if not self._started:
            return
        self._team_agent.remove_event_listener(self._on_event)
        self._started = False
        self._event_queue.put_nowait(None)
        team_logger.info("TeamMonitor stopped for team {}", self._team_id)

    # ------------------------------------------------------------------
    # Query APIs
    # ------------------------------------------------------------------

    async def get_team_info(self) -> TeamInfo | None:
        """Query team basic information.

        Returns:
            TeamInfo or None if the team does not exist.
        """
        team = await self._db.get_team(self._team_id)
        if team is None:
            return None
        return TeamInfo.from_internal(team)

    async def get_members(self, status: str | None = None) -> list[MemberInfo]:
        """Query team member list.

        Args:
            status: Optional MemberStatus value to filter by.

        Returns:
            List of MemberInfo.
        """
        members = await self._db.get_team_members(self._team_id, status=status)
        return [MemberInfo.from_internal(m) for m in members]

    async def get_member(self, member_name: str) -> MemberInfo | None:
        """Query a single member by ID.

        Args:
            member_name: Member identifier.

        Returns:
            MemberInfo or None if not found.
        """
        member = await self._db.get_member(member_name, self._team_id)
        if member is None:
            return None
        return MemberInfo.from_internal(member)

    async def get_tasks(self, status: str | None = None) -> list[TaskInfo]:
        """Query task list.

        Args:
            status: Optional TaskStatus value to filter by.

        Returns:
            List of TaskInfo.
        """
        tasks = await self._db.get_team_tasks(self._team_id, status=status)
        return [TaskInfo.from_internal(t) for t in tasks]

    async def get_messages(
        self,
        *,
        to_member: str | None = None,
        from_member: str | None = None,
    ) -> list[MessageInfo]:
        """Query mailbox messages.

        Without filters, returns all messages for the team.
        With ``to_member``, returns direct messages to that member.

        Args:
            to_member: Filter by recipient member ID.
            from_member: Filter by sender member ID.

        Returns:
            List of MessageInfo.
        """
        if to_member is not None:
            rows = await self._db.get_messages(
                team_name=self._team_id,
                to_member_name=to_member,
                from_member_name=from_member,
            )
        else:
            rows = await self._db.get_team_messages(team_name=self._team_id)
        return [MessageInfo.from_internal(r) for r in rows]

    # ------------------------------------------------------------------
    # Event stream
    # ------------------------------------------------------------------

    async def events(self) -> AsyncIterator[MonitorEvent]:
        """Async iterator that yields MonitorEvent instances.

        Blocks until an event is available. Terminates when ``stop()``
        is called (a ``None`` sentinel is placed in the queue).

        Yields:
            MonitorEvent instances.
        """
        while True:
            event = await self._event_queue.get()
            if event is None:
                break
            yield event

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _on_event(self, event: EventMessage) -> None:
        """Messager event callback registered on TeamAgent.

        Converts the internal EventMessage into a MonitorEvent and
        enqueues it for the ``events()`` iterator.  Internal events
        not in MonitorEventType are silently dropped.

        Args:
            event: Internal EventMessage from the transport.
        """
        monitor_event = MonitorEvent.from_event_message(event)
        if monitor_event is None:
            return
        self._event_queue.put_nowait(monitor_event)


def create_monitor(team_agent: TeamAgent) -> TeamMonitor:
    """Create a TeamMonitor bound to a leader TeamAgent.

    Args:
        team_agent: A fully configured leader TeamAgent instance.

    Returns:
        A new TeamMonitor ready to be started.

    Raises:
        ValueError: If the TeamAgent is not a leader or is not
            fully configured.
    """
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.agent_teams.spawn.context import get_session_id

    if team_agent.role != TeamRole.LEADER:
        raise ValueError("TeamMonitor can only be bound to a leader TeamAgent")

    backend = team_agent.team_backend
    if backend is None:
        raise ValueError("TeamAgent has no team backend configured")

    return TeamMonitor(
        team_id=backend.team_name,
        session_id=get_session_id(),
        db=backend.db,
        team_agent=team_agent,
    )
