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
from openjiuwen.agent_teams.external.format import render_messages, render_task_board
from openjiuwen.agent_teams.i18n import set_language
from openjiuwen.agent_teams.messager.base import create_messager
from openjiuwen.agent_teams.messager.messager import Messager
from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
from openjiuwen.agent_teams.schema.task import TaskCreateResult, TaskDetail, TaskOpResult
from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.models import TeamMember, TeamMessageBase, TeamTaskBase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.database import TeamDatabase
    from openjiuwen.agent_teams.tools.team import TeamBackend
    from openjiuwen.core.foundation.tool.base import Tool

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
        self._backend: "TeamBackend | None" = None
        self._tasks: TeamTaskManager | None = None
        self._messages: TeamMessageManager | None = None
        self._db: "TeamDatabase | None" = None
        self._session_token: Token[str] | None = None
        # Member-scope real team tools, keyed by card.name. Built at connect()
        # for the ``member`` scope so an external CLI member calls the exact
        # same TeamTool instances (same schema + map_result text) as a native
        # in-process teammate. Empty for the ``operator`` scope.
        self._tools: dict[str, "Tool"] = {}
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

    @property
    def is_human_agent(self) -> bool:
        """Whether this member is a human-agent avatar (drives the inbound note)."""
        return self._descriptor.role == "human_agent"

    @property
    def scope(self) -> str:
        """The external-access scenario: ``"member"`` or ``"operator"``."""
        return self._descriptor.scope

    @property
    def tools(self) -> dict[str, "Tool"]:
        """The real team tools for the ``member`` scope (keyed by card.name).

        Populated at :meth:`connect` only when ``scope == "member"`` — the
        external CLI member then drives the exact same ``view_task`` /
        ``claim_task`` / ``send_message`` ``TeamTool`` instances a native
        teammate uses. Empty for the ``operator`` scope (operators act through
        the explicit op methods / backend).
        """
        return self._tools

    def bind_session_context(self) -> None:
        """Re-assert the session-id + language contextvars for this call.

        A dispatcher that runs each operation in its own ``asyncio.Task``
        (e.g. an MCP server handling each tool call in a fresh task) must call
        this before every operation: the bind done in :meth:`connect` only
        lives in that connect call's task context, so without re-binding a
        later call sees an empty session id and targets a non-existent
        per-session dynamic table.
        """
        set_session_id(self._descriptor.session_id)
        set_language(self._descriptor.language)  # type: ignore[arg-type]

    async def connect(self) -> None:
        """Open the team database and messager and wire up the managers.

        Builds a minimal :class:`TeamBackend` over the shared db + messager;
        for the ``member`` scope it also builds the real teammate team tools
        via ``create_team_tools`` so the external member is indistinguishable
        from an in-process teammate. Idempotent: a second call is a no-op.
        """
        if self._connected:
            return

        from openjiuwen.agent_teams.tools.team import TeamBackend
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools

        set_language(self._descriptor.language)  # type: ignore[arg-type]
        self._session_token = set_session_id(self._descriptor.session_id)

        db = get_shared_db(self._descriptor.db_config)
        await db.initialize()

        transport_config = self._descriptor.transport_config
        if transport_config.backend == "hybrid":
            from openjiuwen.agent_teams.messager.hybrid import HybridMessager, WebSocketEventPublisher

            if not transport_config.external_publish_url:
                raise ValueError("hybrid messager requires external_publish_url")
            publisher = WebSocketEventPublisher(
                url=transport_config.external_publish_url,
                session_id=self.session_id,
                team_name=self.team_name,
                request_timeout=transport_config.request_timeout,
            )
            self._messager = HybridMessager(
                publisher=publisher,
                sender_id=self.member_name,
            )
        else:
            self._messager = create_messager(transport_config)
        await self._messager.start()

        backend = TeamBackend(
            team_name=self.team_name,
            member_name=self.member_name,
            is_leader=self.is_leader,
            db=db,
            messager=self._messager,
        )
        self._backend = backend
        self._tasks = backend.task_manager
        self._messages = backend.message_manager
        self._db = db

        if self._descriptor.scope == "member":
            # Same factory, same dispatch mode as an in-process teammate: the
            # tool set an external CLI member sees must match the system prompt
            # it was spawned with.
            real_tools = create_team_tools(
                role="teammate",
                agent_team=backend,
                dispatch_mode=self._descriptor.dispatch_mode,
                lang=self._descriptor.language,
            )
            self._tools = {tool.card.name: tool for tool in real_tools}

        self._connected = True
        team_logger.info(
            "ExternalTeamClient connected: team=%s member=%s scope=%s",
            self.team_name,
            self.member_name,
            self._descriptor.scope,
        )

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
        self._backend = None
        self._tools = {}
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

    async def create_task(
        self,
        *,
        title: str,
        content: str,
        task_id: str | None = None,
        dependencies: list[str] | None = None,
    ) -> TaskCreateResult:
        """Create a team task (operator scope — external team control).

        An external operator drives the team's work by creating tasks the
        members claim. Goes through the same ``TeamTaskManager.add`` an
        in-process leader uses, so it publishes the same task-created event.
        """
        return await self._require_tasks().add(
            title=title,
            content=content,
            task_id=task_id,
            dependencies=dependencies,
        )

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

    async def read_inbox(self, *, mark_read: bool = True) -> str:
        """Render unread messages + the task board as one text block.

        The external analog of how a native member is *pushed* its inbound
        messages and task board by the coordination layer — external members
        must *pull*, so this is the one tool with no native counterpart. The
        text mirrors the in-process dispatcher (``render_messages`` +
        ``render_task_board``) so a member reads the same shape either way.
        """
        view = await self.fetch_inbox(mark_read=mark_read)
        now_ms = get_current_time()
        parts: list[str] = []
        if view.messages:
            parts.append(render_messages(view.messages, is_human_agent=self.is_human_agent, now_ms=now_ms))
        board = render_task_board(view.tasks, is_leader=self.is_leader, now_ms=now_ms)
        if board:
            parts.append(board)
        return "\n\n".join(parts) if parts else "(inbox empty)"

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

    def _require_db(self) -> "TeamDatabase":
        if self._db is None:
            self._raise_not_connected()
        return self._db


__all__ = [
    "BROADCAST_TARGET",
    "ExternalTeamClient",
    "InboxView",
    "InboxObserver",
]
