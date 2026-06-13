# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Message and message-read-status data access object."""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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

    def __init__(self, sessions: DbSessions) -> None:
        """Initialize message DAO with the shared read/write session provider."""
        self._sessions = sessions

    async def get_message(self, message_id: str) -> Optional[TeamMessageBase]:
        """Get message information by ID."""
        message_model = _get_message_model()
        async with self._sessions.read() as session:
            result = await session.execute(select(message_model).where(message_model.message_id == message_id))
            return result.scalar_one_or_none()

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
        """
        message_model = _get_message_model()

        async def _op() -> bool:
            try:
                async with self._sessions.write() as session:
                    message = message_model(
                        message_id=message_id,
                        team_name=team_name,
                        from_member_name=from_member_name,
                        to_member_name=to_member_name,
                        content=content,
                        timestamp=get_current_time(),
                        broadcast=broadcast,
                        protocol=protocol,
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

            return rows

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
                return list(rows)

            return [row for row in rows if read_status is None or row.timestamp > read_status.read_at]

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
            return rows

    async def has_unread_messages(self, team_name: str, *, include_broadcast: bool = True) -> bool:
        """Return True if any team message is still unread by its intended reader.

        Direct messages: unread when ``is_read`` is False. Broadcast messages:
        read state is a per-member high-water mark in MessageReadStatus, so a
        broadcast is unread by member M when M is not its sender and M's
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
            direct_unread = await session.execute(
                select(message_model.message_id)
                .where(
                    message_model.team_name == team_name,
                    message_model.broadcast.is_(False),
                    message_model.is_read.is_(False),
                )
                .limit(1)
            )
            if direct_unread.first() is not None:
                return True

            if not include_broadcast:
                return False

            # Broadcast messages: per-member watermark comparison.
            broadcast_result = await session.execute(
                select(message_model).where(
                    message_model.team_name == team_name,
                    message_model.broadcast.is_(True),
                )
            )
            broadcasts = broadcast_result.scalars().all()
            if not broadcasts:
                return False

            member_result = await session.execute(
                select(TeamMember.member_name).where(TeamMember.team_name == team_name)
            )
            members = member_result.scalars().all()

            read_result = await session.execute(
                select(read_status_model).where(read_status_model.team_name == team_name)
            )
            read_at_by_member = {row.member_name: row.read_at for row in read_result.scalars().all()}

            for member_name in members:
                watermark = read_at_by_member.get(member_name)
                for msg in broadcasts:
                    if msg.from_member_name == member_name:
                        continue
                    if watermark is None or msg.timestamp > watermark:
                        return True
            return False

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

        Batches what would otherwise be one transaction (one fsync) per
        message into a single commit — the dominant write-throughput lever
        on SQLite. Skips ids that are missing or fail validation; returns
        the number actually marked. Idempotent and safe to retry on a
        locked database (see ``_mark_read_in_session``).

        Args:
            message_ids: Message ids to mark read, in delivery order.
            member_name: Member reading the messages.

        Returns:
            Count of messages whose read state was applied.
        """
        if not message_ids:
            return 0

        async def _op() -> int:
            marked = 0
            async with self._sessions.write() as session:
                for message_id in message_ids:
                    if await self._mark_read_in_session(session, message_id, member_name):
                        marked += 1
                if marked:
                    await session.commit()
            return marked

        return await retry_on_locked(_op, on_locked_result=0, label=f"mark_messages_read ({len(message_ids)})")
