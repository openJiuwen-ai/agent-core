# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Message and message-read-status data access object."""

import json
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.team_workspace.session_file_store import SessionFileStore

from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.tools.database.engine import (
    DbSessions,
    get_current_time,
    retry_on_locked,
)
from openjiuwen.agent_teams.tools.models import (
    TeamMember,
    TeamMessageBase,
    _get_message_model,
    _get_message_read_status_model,
)
from openjiuwen.core.common.logging import team_logger


class MessageDao:
    """Data access object for message and message-read-status tables."""

    def __init__(
        self,
        sessions: DbSessions,
        *,
        file_store: Optional["SessionFileStore"] = None,
    ) -> None:
        """Initialize message DAO with the shared read/write session provider.

        Args:
            file_store: Optional content store. When set, message
                bodies are written to session files (DB keeps the ``#file#``
                placeholder; the path is derived from the row) and
                dereferenced on read; when ``None`` (default) behaviour is
                unchanged (inline ``content``).
        """
        self._sessions = sessions
        self._file_store = file_store

    @staticmethod
    def _session_id() -> Optional[str]:
        from openjiuwen.agent_teams.context import get_session_id

        return get_session_id()

    def _to_stored(self, team_name: str, content: str, *, object_id: str, kind: str, to_member: Optional[str]) -> str:
        """Persist ``content`` to a session file; return the ``#file#`` placeholder.

        On IO failure the raw text is returned instead, so the caller stores
        it inline — the non-placeholder value keeps the degradation visible
        (and read-back correct) without any path stored in the DB. Empty
        content (templated messages, UC-B6) stays inline — ``""`` is not a
        placeholder, so no file is created and no read is triggered.
        """
        if not content:
            return content
        if self._file_store is None:
            return content
        session_id = self._session_id()
        if not session_id:
            return content
        from openjiuwen.agent_teams.team_workspace.session_file_store import CONTENT_IN_FILE, FileAddress

        try:
            self._file_store.put(
                content,
                FileAddress(
                    team_name=team_name,
                    session_id=session_id,
                    kind=kind,
                    object_id=object_id,
                    to_member=to_member,
                ),
            )
            return CONTENT_IN_FILE
        except (OSError, ValueError) as exc:
            team_logger.warning("content spill failed for %s; keeping inline: %s", object_id, exc)
            return content

    @staticmethod
    def _is_placeholder(content: str) -> bool:
        """True when ``content`` marks a body stored in a session file.

        Imported lazily — a module-level import of the team_workspace
        package from the DAO layer triggers a circular import chain.
        """
        from openjiuwen.agent_teams.team_workspace.session_file_store import CONTENT_IN_FILE

        return content == CONTENT_IN_FILE

    def _deref_row(self, row: TeamMessageBase) -> str:
        """Read a placeholder row's body from its session file.

        The file path is derived from the row's own fields (kind by
        ``broadcast``, object id = ``message_id``, to-member), so the DB
        never stores a pointer. Any resolution/IO failure degrades back to
        the stored value (the placeholder) instead of raising.
        """
        session_id = self._session_id()
        if not session_id:
            return row.content
        from openjiuwen.agent_teams.team_workspace.session_file_store import (
            KIND_BROADCAST,
            KIND_DIRECT,
            FileAddress,
        )

        try:
            return self._file_store.get(
                FileAddress(
                    team_name=row.team_name,
                    session_id=session_id,
                    kind=KIND_BROADCAST if row.broadcast else KIND_DIRECT,
                    object_id=row.message_id,
                    to_member=row.to_member_name,
                )
            )
        except (ValueError, OSError) as exc:
            team_logger.warning("content deref failed for %s: %s", row.message_id, exc)
            return row.content

    def _hydrate_row(self, row: Optional[TeamMessageBase]) -> Optional[TeamMessageBase]:
        """Dereference a message row's ``content`` in place."""
        if row is not None and self._is_placeholder(row.content):
            row.content = self._deref_row(row)
        return row

    def _hydrate_rows(self, rows: List[TeamMessageBase]) -> List[TeamMessageBase]:
        for row in rows:
            if self._is_placeholder(row.content):
                row.content = self._deref_row(row)
        return rows

    async def get_message(self, message_id: str) -> Optional[TeamMessageBase]:
        """Get message information by ID."""
        message_model = _get_message_model()
        async with self._sessions.read() as session:
            result = await session.execute(select(message_model).where(message_model.message_id == message_id))
            return self._hydrate_row(result.scalar_one_or_none())

    async def create_message(
        self,
        message_id: str,
        team_name: str,
        from_member_name: str,
        content: str,
        *,
        to_member_name: Optional[str] = None,
        broadcast: bool = False,
        is_read: bool = False,
        protocol: str = "plain",
        meta: Optional[dict] = None,
    ) -> bool:
        """Create a new team message.

        Args:
            is_read: Initial read flag for direct messages. Used to mark
                messages addressed to members with no live consumer (e.g.
                the HITT human_agent) as already read so mailbox polling
                does not keep re-firing on them. Ignored for broadcasts,
                whose per-member read state lives in MessageReadStatus.
            protocol: Message format — ``"plain"`` for normal text,
                ``"json"`` for structured payloads (e.g. approval results).
            meta: Framework-only delivery payload, JSON-serialized into the
                ``meta`` column. A templated message carries the template key
                plus its refs/params here and stores an empty ``content`` —
                the delivery path expands it. See ``message_template.py``.
        """
        message_model = _get_message_model()

        from openjiuwen.agent_teams.team_workspace.session_file_store import (
            KIND_BROADCAST,
            KIND_DIRECT,
        )

        stored_content = self._to_stored(
            team_name,
            content,
            object_id=message_id,
            kind=KIND_BROADCAST if broadcast else KIND_DIRECT,
            to_member=to_member_name,
        )

        async def _op() -> bool:
            try:
                async with self._sessions.write() as session:
                    message = message_model(
                        message_id=message_id,
                        team_name=team_name,
                        from_member_name=from_member_name,
                        to_member_name=to_member_name,
                        content=stored_content,
                        timestamp=get_current_time(),
                        broadcast=broadcast,
                        protocol=protocol,
                        meta=json.dumps(meta, ensure_ascii=False) if meta else None,
                        is_read=None if broadcast else is_read,
                    )
                    session.add(message)
                    await session.commit()
                team_logger.info("Message %s created", message_id)
                return True
            except IntegrityError as e:
                team_logger.error("Failed to create %s, reason is %s", message_id, e)
                return False

        return await retry_on_locked(_op, on_locked_result=False, label=f"create_message {message_id}")

    async def create_direct_messages(
        self,
        *,
        team_name: str,
        from_member_name: str,
        content: str,
        recipients: List[tuple[str, str]],
        protocol: str = "plain",
    ) -> int:
        """Insert N point-to-point messages (same content) in ONE transaction.

        Batch counterpart to ``create_message`` for multicast: one write-lock
        acquisition + one COMMIT (one fsync) covers every recipient, instead
        of N separate transactions each paying their own fsync — the dominant
        multicast write-tail cost under the process-wide write lock. The whole
        batch is atomic: an ``IntegrityError`` rolls back all rows and returns
        0. ``is_read`` starts False (multicast never targets consumer-less
        pseudo-members — the tool layer rejects ``user`` and ``*``).

        Args:
            team_name: Team the messages belong to.
            from_member_name: Sender member id.
            content: Shared message body.
            recipients: ``(message_id, to_member_name)`` pairs, in delivery
                order. Message ids are minted by the caller (mirrors
                ``create_message``).
            protocol: Message format (``"plain"`` / ``"json"``).

        Returns:
            Number of rows inserted; 0 when ``recipients`` is empty or the
            batch failed (nothing committed).
        """
        if not recipients:
            return 0
        message_model = _get_message_model()
        now = get_current_time()

        # Multicast: one session file per row. Every row
        # must be able to derive its own file path from its own fields
        # (``message_id`` + ``to_member_name``), so there is no shared file
        # or shared pointer — the same content is written N times and the DB
        # keeps the ``#file#`` placeholder on every row. All spills happen
        # *before* the write transaction: synchronous file IO must not hold
        # the process-wide SQLite write lock (matches ``create_message``).
        from openjiuwen.agent_teams.team_workspace.session_file_store import KIND_DIRECT

        stored_contents = [
            self._to_stored(
                team_name,
                content,
                object_id=message_id,
                kind=KIND_DIRECT,
                to_member=to_member_name,
            )
            for message_id, to_member_name in recipients
        ]

        async def _op() -> int:
            try:
                async with self._sessions.write() as session:
                    for (message_id, to_member_name), stored_content in zip(recipients, stored_contents):
                        session.add(
                            message_model(
                                message_id=message_id,
                                team_name=team_name,
                                from_member_name=from_member_name,
                                to_member_name=to_member_name,
                                content=stored_content,
                                timestamp=now,
                                broadcast=False,
                                protocol=protocol,
                                is_read=False,
                            )
                        )
                    await session.commit()
                team_logger.info("Created %d direct messages from %s", len(recipients), from_member_name)
                return len(recipients)
            except IntegrityError as e:
                team_logger.error(
                    "Failed to batch-create %d messages from %s: %s",
                    len(recipients),
                    from_member_name,
                    e,
                )
                return 0

        return await retry_on_locked(_op, on_locked_result=0, label=f"create_direct_messages ({len(recipients)})")

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get direct (point-to-point) messages for a specific member."""
        message_model = _get_message_model()
        async with self._sessions.read() as session:
            query = select(message_model).where(
                message_model.team_name == team_name,
                message_model.to_member_name == to_member_name,
                message_model.broadcast.is_(False),
            )

            if from_member_name is not None:
                query = query.where(message_model.from_member_name == from_member_name)

            if unread_only:
                query = query.where(message_model.is_read.is_(False))

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()

            return self._hydrate_rows(rows)

    async def get_broadcast_messages(
        self,
        team_name: str,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get broadcast messages for a specific member, with read status."""
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()
        async with self._sessions.read() as session:
            query = select(message_model).where(
                message_model.team_name == team_name,
                message_model.broadcast.is_(True),
                message_model.from_member_name != member_name,
            )

            if from_member_name is not None:
                query = query.where(message_model.from_member_name == from_member_name)

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()

            read_result = await session.execute(
                select(read_status_model).where(
                    read_status_model.member_name == member_name,
                    read_status_model.team_name == team_name,
                )
            )
            read_status = read_result.scalar_one_or_none()

            if not unread_only:
                return self._hydrate_rows(list(rows))

            return self._hydrate_rows(
                [row for row in rows if read_status is None or row.timestamp > read_status.read_at]
            )

    async def get_team_messages(self, team_name: str, broadcast: Optional[bool] = None) -> List[TeamMessageBase]:
        """Get all messages for a team (without read status)."""
        message_model = _get_message_model()
        async with self._sessions.read() as session:
            query = select(message_model).where(message_model.team_name == team_name)

            if broadcast is not None:
                query = query.where(message_model.broadcast.is_(broadcast))

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()
            return self._hydrate_rows(rows)

    async def has_unread_messages(self, team_name: str, *, include_broadcast: bool = True) -> bool:
        """Return True if any team message is still unread by its intended reader.

        Direct messages: unread when ``is_read`` is False and the recipient
        has not already reached SHUTDOWN. Broadcast messages:
        read state is a per-member high-water mark in MessageReadStatus, so a
        broadcast is unread by reachable member M when M is not its sender and M's
        watermark does not yet cover the broadcast timestamp. This honors
        ``is_read`` as-is — messages addressed to consumer-less members (the
        ``user`` pseudo-member, human_agent) are marked read on write or
        auto-acked by the leader, so they do not block completion.

        Args:
            team_name: Team identifier.
            include_broadcast: When False, only direct (point-to-point)
                messages count toward the unread check; the broadcast
                watermark comparison is skipped. Defaults to True to keep
                the original behavior.

        Returns:
            True if at least one matching message has not been read.
        """
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()
        async with self._sessions.read() as session:
            # Direct messages: a single unread row is enough.
            recipient_is_shutdown = (
                select(TeamMember.member_name)
                .where(
                    TeamMember.team_name == message_model.team_name,
                    TeamMember.member_name == message_model.to_member_name,
                    TeamMember.status == MemberStatus.SHUTDOWN.value,
                )
                .exists()
            )
            direct_unread = await session.execute(
                select(message_model.message_id)
                .where(
                    message_model.team_name == team_name,
                    message_model.broadcast.is_(False),
                    message_model.is_read.is_(False),
                    ~recipient_is_shutdown,
                )
                .limit(1)
            )
            if direct_unread.first() is not None:
                return True

            if not include_broadcast:
                return False

            # Broadcast messages: a broadcast B is unread by member M when M
            # is not its sender and M has no read watermark covering B's
            # timestamp. Push the whole "does any such (member, broadcast)
            # pair exist?" check into one correlated EXISTS query instead of
            # loading every broadcast + member + watermark row and doing an
            # O(members x broadcasts) scan in Python. A NULL / absent
            # watermark never satisfies ``read_at >= B.timestamp`` (SQL
            # three-valued logic), so it correctly counts as uncovered.
            covered_by_watermark = (
                select(read_status_model.member_name)
                .where(
                    read_status_model.member_name == TeamMember.member_name,
                    read_status_model.team_name == team_name,
                    read_status_model.read_at >= message_model.timestamp,
                )
                .exists()
            )
            unread_broadcast = await session.execute(
                select(message_model.message_id)
                .join(TeamMember, TeamMember.team_name == message_model.team_name)
                .where(
                    message_model.team_name == team_name,
                    message_model.broadcast.is_(True),
                    TeamMember.status != MemberStatus.SHUTDOWN.value,
                    TeamMember.member_name != message_model.from_member_name,
                    ~covered_by_watermark,
                )
                .limit(1)
            )
            return unread_broadcast.first() is not None

    async def _mark_read_in_session(
        self,
        session: AsyncSession,
        message_id: str,
        member_name: str,
    ) -> bool:
        """Apply read state for one message within an existing session.

        No commit — the caller owns the transaction boundary so single and
        batch marks share one code path. Returns True when the read state
        was applied (caller should commit), False when the message is
        missing or the member / validation check fails.

        Idempotent: re-marking a direct message or advancing a broadcast
        watermark to an already-covered timestamp is a no-op-safe write, so
        the caller may safely retry the enclosing transaction on a locked
        database.
        """
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()

        result = await session.execute(select(message_model).where(message_model.message_id == message_id))
        message = result.scalar_one_or_none()
        if not message:
            team_logger.error("Message %s not found", message_id)
            return False

        if member_name == "user":
            if message.broadcast:
                team_logger.error("'user' pseudo-member cannot read broadcast message %s", message_id)
                return False
        else:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == message.team_name,
                )
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error("Member %s not found", member_name)
                return False

        if message.broadcast:
            read_result = await session.execute(
                select(read_status_model).where(
                    read_status_model.member_name == member_name,
                    read_status_model.team_name == message.team_name,
                )
            )
            read_status = read_result.scalar_one_or_none()
            if read_status is None:
                read_status = read_status_model(
                    member_name=member_name,
                    team_name=message.team_name,
                    read_at=message.timestamp,
                )
                session.add(read_status)
                # Flush the pending INSERT so a second broadcast id in the same
                # batch transaction sees this watermark row on its SELECT. The
                # session runs autoflush=False, so without this the next
                # broadcast re-inserts the same (member_name, team_name) PK and
                # the commit hits a UNIQUE violation. Flush holds no fsync — the
                # single commit still batches the whole drain into one.
                await session.flush()
            elif read_status.read_at is None or message.timestamp > read_status.read_at:
                read_status.read_at = message.timestamp
        else:
            message.is_read = True

        return True

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        """Mark a message as read by a member (works for both direct and broadcast messages)."""

        async def _op() -> bool:
            async with self._sessions.write() as session:
                marked = await self._mark_read_in_session(session, message_id, member_name)
                if marked:
                    await session.commit()
            if marked:
                team_logger.info("Message %s marked as read by %s", message_id, member_name)
            return marked

        return await retry_on_locked(_op, on_locked_result=False, label=f"mark_message_read {message_id}")

    async def mark_messages_read(self, message_ids: List[str], member_name: str) -> int:
        """Mark several messages read for one member in a single transaction.

        Batches the whole mailbox drain into one commit (one fsync) — the
        dominant write-throughput lever on SQLite. Direct messages are marked
        with a single set-based ``UPDATE ... WHERE message_id IN (...)`` rather
        than one SELECT+write per id, so draining a busy inbox costs a constant
        few statements instead of ``O(n)`` round-trips. Broadcasts keep the
        per-message watermark path (they are few — the manager layer collapses
        them to the newest before the DAO). Skips ids that are missing or fail
        validation; returns the number actually marked.

        Args:
            message_ids: Message ids to mark read, in delivery order.
            member_name: Member reading the messages.

        Returns:
            Count of messages whose read state was applied.
        """
        if not message_ids:
            return 0

        message_model = _get_message_model()

        async def _op() -> int:
            async with self._sessions.write() as session:
                # One SELECT for every target row — missing ids simply drop out.
                result = await session.execute(select(message_model).where(message_model.message_id.in_(message_ids)))
                messages = result.scalars().all()
                if not messages:
                    return 0

                direct = [m for m in messages if not m.broadcast]
                broadcasts = [m for m in messages if m.broadcast]

                marked = 0
                # Direct messages: one set-based UPDATE for the whole batch.
                direct_ids = await self._eligible_direct_ids(session, direct, member_name)
                if direct_ids:
                    await session.execute(
                        update(message_model).where(message_model.message_id.in_(direct_ids)).values(is_read=True)
                    )
                    marked += len(direct_ids)

                # Broadcasts: per-message watermark advance (few in practice).
                for message in broadcasts:
                    if await self._mark_read_in_session(session, message.message_id, member_name):
                        marked += 1

                if marked:
                    await session.commit()
                return marked

        return await retry_on_locked(_op, on_locked_result=0, label=f"mark_messages_read ({len(message_ids)})")

    async def _eligible_direct_ids(
        self,
        session: AsyncSession,
        direct_messages: List[TeamMessageBase],
        member_name: str,
    ) -> List[str]:
        """Return the direct-message ids ``member_name`` is allowed to ack.

        Mirrors the per-message validation of ``_mark_read_in_session`` but in
        one roster query: the ``user`` pseudo-member may ack any direct message
        without a roster row; every other member must exist in the message's
        team. Messages whose team lacks the member are dropped (not marked),
        matching the single-message path's "member not found -> skip".
        """
        if not direct_messages:
            return []
        if member_name == "user":
            return [m.message_id for m in direct_messages]
        teams = {m.team_name for m in direct_messages}
        result = await session.execute(
            select(TeamMember.team_name).where(
                TeamMember.member_name == member_name,
                TeamMember.team_name.in_(teams),
            )
        )
        valid_teams = set(result.scalars().all())
        return [m.message_id for m in direct_messages if m.team_name in valid_teams]
