# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-boundary client that lets an external agent act as a team member.

:class:`ExternalTeamClient` opens the shared team database and messager from
a :class:`TeamJoinDescriptor` and exposes the same collaboration operations
an in-process member performs through team tools — sending messages,
viewing / claiming / updating tasks, listing members, and reading an inbox.

The write path is symmetric with in-process members: each operation goes
through the same ``TeamTaskManager`` / ``TeamMessageManager`` and therefore
publishes the same events to the same topics. At the database + messager
layer an external member is indistinguishable from an in-process one.
"""

from __future__ import annotations

import asyncio
from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, NoReturn

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.i18n import set_language
from openjiuwen.agent_teams.messager.base import create_messager
from openjiuwen.agent_teams.messager.messager import Messager
from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
from openjiuwen.agent_teams.schema.task import TaskDetail, TaskOpResult
from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.models import TeamMember, TeamMessageBase, TeamTaskBase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.database import TeamDatabase
    from openjiuwen.agent_teams.tools.memory_database import InMemoryTeamDatabase

# Broadcast routing sentinel, matching the team ``send_message`` tool.
BROADCAST_TARGET = "*"

# Wakeup callback invoked on each relevant transport event during ``watch``.
InboxObserver = Callable[["InboxView"], Awaitable[None]]


@dataclass(slots=True)
class InboxView:
    """A snapshot of what currently needs the member's attention.

    Attributes:
        messages: Unread direct + broadcast messages addressed to the member.
        tasks: All non-terminal team tasks (the actionable task board).
    """

    messages: list[TeamMessageBase] = field(default_factory=list)
    tasks: list[TeamTaskBase] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Whether there is nothing for the member to act on."""
        return not self.messages and not self.tasks


class ExternalTeamClient:
    """Attach an external agent to a running team via shared db + messager.

    Construct from a :class:`TeamJoinDescriptor`, then ``await connect()``
    (or use as an async context manager). All operations run in the caller's
    async context, which carries the team ``session_id`` so published events
    land on the right topics.
    """

    def __init__(self, descriptor: TeamJoinDescriptor):
        """Bind the client to a join descriptor without opening resources."""
        self._descriptor = descriptor
        self._messager: Messager | None = None
        self._tasks: TeamTaskManager | None = None
        self._messages: TeamMessageManager | None = None
        self._db: "TeamDatabase | InMemoryTeamDatabase | None" = None
        self._session_token: Token[str] | None = None
        self._connected = False

    @property
    def session_id(self) -> str:
        """The team session id this client is bound to.

        Drives the per-session dynamic table names. Callers that dispatch
        operations across task boundaries (e.g. an MCP server handling each
        tool call in its own task) must re-assert this on the session-id
        contextvar before each operation, since the bind done in
        ``connect()`` only lives in that call's task context.
        """
        return self._descriptor.session_id

    @property
    def language(self) -> str:
        """The team runtime language this client renders with."""
        return self._descriptor.language

    @property
    def member_name(self) -> str:
        """The member identity this client serves."""
        return self._descriptor.member_name

    @property
    def team_name(self) -> str:
        """The target team identifier."""
        return self._descriptor.team_name

    @property
    def is_leader(self) -> bool:
        """Whether this member carries the leader role."""
        return self._descriptor.role == "leader"

    async def connect(self) -> None:
        """Open the team database and messager and wire up the managers.

        Idempotent: a second call is a no-op.
        """
        if self._connected:
            return

        set_language(self._descriptor.language)  # type: ignore[arg-type]
        self._session_token = set_session_id(self._descriptor.session_id)

        db = get_shared_db(self._descriptor.db_config)
        await db.initialize()

        self._messager = create_messager(self._descriptor.transport_config)
        await self._messager.start()

        self._tasks = TeamTaskManager(self.team_name, self.member_name, db, self._messager)
        self._messages = TeamMessageManager(self.team_name, self.member_name, db, self._messager)
        self._db = db
        self._connected = True
        team_logger.info("ExternalTeamClient connected: team=%s member=%s", self.team_name, self.member_name)

    async def close(self) -> None:
        """Stop the messager and release the session context. Idempotent."""
        if self._messager is not None:
            await self._messager.stop()
            self._messager = None
        if self._session_token is not None:
            reset_session_id(self._session_token)
            self._session_token = None
        self._tasks = None
        self._messages = None
        self._connected = False

    async def __aenter__(self) -> "ExternalTeamClient":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ---- messaging ------------------------------------------------------

    async def send_message(self, to: str, content: str) -> str | None:
        """Send a direct message, or broadcast when ``to == "*"``.

        Args:
            to: Recipient member name, or ``"*"`` for a team broadcast.
            content: Message body.

        Returns:
            The new message id, or ``None`` if persistence failed.
        """
        messages = self._require_messages()
        if to == BROADCAST_TARGET:
            return await messages.broadcast_message(content)
        return await messages.send_message(content, to_member_name=to)

    # ---- task board -----------------------------------------------------

    async def list_tasks(self, status: str | None = None) -> list[TeamTaskBase]:
        """List team tasks, optionally filtered by status."""
        return await self._require_tasks().list_tasks(status=status)

    async def claimable_tasks(self) -> list[TeamTaskBase]:
        """List pending tasks available to claim."""
        return await self._require_tasks().get_claimable_tasks()

    async def get_task(self, task_id: str) -> TaskDetail | None:
        """Get full detail for a single task, or ``None`` if absent."""
        return await self._require_tasks().get_task_detail(task_id)

    async def claim_task(self, task_id: str) -> TaskOpResult:
        """Claim a pending task for this member."""
        return await self._require_tasks().claim(task_id)

    async def complete_task(self, task_id: str) -> TaskOpResult:
        """Mark a claimed task complete."""
        return await self._require_tasks().complete(task_id)

    async def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> TaskOpResult:
        """Edit a task's title and/or content (pending / blocked tasks only)."""
        return await self._require_tasks().update_task(task_id, title=title, content=content)

    # ---- roster ---------------------------------------------------------

    async def list_members(self) -> list[TeamMember]:
        """List all team member rows."""
        return await self._require_db().member.get_team_members(self.team_name)

    # ---- inbox ----------------------------------------------------------

    async def fetch_inbox(self, *, mark_read: bool = True) -> InboxView:
        """Read unread messages and the current task board for this member.

        Args:
            mark_read: When True (default), unread messages are marked read so
                a subsequent poll does not re-deliver them.

        Returns:
            An :class:`InboxView` with unread messages and non-terminal tasks.
        """
        messages = self._require_messages()
        direct = await messages.get_messages(to_member_name=self.member_name, unread_only=True)
        broadcast = await messages.get_broadcast_messages(member_name=self.member_name, unread_only=True)
        unread = [*direct, *broadcast]

        if mark_read:
            for msg in unread:
                await messages.mark_message_read(msg.message_id, self.member_name)

        tasks = await self.list_tasks()
        return InboxView(messages=unread, tasks=tasks)

    async def watch(self, observer: InboxObserver) -> None:
        """Block on team events, invoking ``observer`` with a fresh inbox.

        Subscribes to the MESSAGE and TASK topics; every relevant event is
        treated as a wakeup that triggers an inbox re-fetch. This sidesteps
        self-event filtering — the re-fetch only ever returns messages
        addressed to this member plus the live task board. Runs until the
        surrounding task is cancelled.

        Args:
            observer: Async callback receiving the refreshed inbox view.
        """
        messager = self._require_messager()
        session_id = self._descriptor.session_id
        message_topic = TeamTopic.MESSAGE.build(session_id, self.team_name)
        task_topic = TeamTopic.TASK.build(session_id, self.team_name)

        async def _on_event(_event: EventMessage) -> None:
            view = await self.fetch_inbox(mark_read=True)
            if not view.is_empty():
                await observer(view)

        await messager.subscribe(message_topic, _on_event)
        await messager.subscribe(task_topic, _on_event)
        try:
            await asyncio.Event().wait()
        finally:
            await messager.unsubscribe(message_topic)
            await messager.unsubscribe(task_topic)

    # ---- internals ------------------------------------------------------

    @staticmethod
    def _raise_not_connected() -> NoReturn:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="ExternalTeamClient is not connected; call connect() first",
        )
        raise AssertionError  # pragma: no cover - raise_error always raises

    def _require_tasks(self) -> TeamTaskManager:
        if self._tasks is None:
            self._raise_not_connected()
        return self._tasks

    def _require_messages(self) -> TeamMessageManager:
        if self._messages is None:
            self._raise_not_connected()
        return self._messages

    def _require_messager(self) -> Messager:
        if self._messager is None:
            self._raise_not_connected()
        return self._messager

    def _require_db(self) -> "TeamDatabase | InMemoryTeamDatabase":
        if self._db is None:
            self._raise_not_connected()
        return self._db


__all__ = [
    "BROADCAST_TARGET",
    "ExternalTeamClient",
    "InboxView",
    "InboxObserver",
]
