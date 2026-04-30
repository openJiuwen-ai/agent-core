# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Message and message-read-status data access object."""

import asyncio
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.models import (
    TeamMember,
    TeamMessageBase,
    _get_message_model,
    _get_message_read_status_model,
)
from openjiuwen.core.common.logging import team_logger


_DB_RETRY_ATTEMPTS = 3
_DB_RETRY_BASE_DELAY = 0.5


class MessageDao:
    """Data access object for message and message-read-status tables."""

    def __init__(self, session_local: async_sessionmaker) -> None:
        """Initialize message DAO with the shared session factory."""
        self._session_local = session_local

    async def get_message(self, message_id: str) -> Optional[TeamMessageBase]:
        """Get message information by ID."""
        message_model = _get_message_model()
        async with self._session_local() as session:
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
    ) -> bool:
        """Create a new team message.

        Args:
            is_read: Initial read flag for direct messages. Used to mark
                messages addressed to members with no live consumer (e.g.
                the HITT human_agent) as already read so mailbox polling
                does not keep re-firing on them. Ignored for broadcasts,
                whose per-member read state lives in MessageReadStatus.
        """
        message_model = _get_message_model()
        for attempt in range(_DB_RETRY_ATTEMPTS):
            async with self._session_local() as session:
                try:
                    message = message_model(
                        message_id=message_id,
                        team_name=team_name,
                        from_member_name=from_member_name,
                        to_member_name=to_member_name,
                        content=content,
                        timestamp=get_current_time(),
                        broadcast=broadcast,
                        is_read=None if broadcast else is_read,
                    )
                    session.add(message)
                    await session.commit()
                    team_logger.info("Message %s created", message_id)
                    return True
                except IntegrityError as e:
                    await session.rollback()
                    team_logger.error("Failed to create %s, reason is %s", message_id, e)
                    return False
                except OperationalError as e:
                    await session.rollback()
                    if attempt < _DB_RETRY_ATTEMPTS - 1:
                        delay = _DB_RETRY_BASE_DELAY * (2**attempt)
                        team_logger.warning(
                            "Database locked on create_message (attempt %d), retrying in %ss",
                            attempt + 1,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        team_logger.error(
                            "Failed to create message %s after %d attempts: %s",
                            message_id,
                            _DB_RETRY_ATTEMPTS,
                            e,
                        )
                        return False
        return False

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get direct (point-to-point) messages for a specific member."""
        message_model = _get_message_model()
        async with self._session_local() as session:
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
        async with self._session_local() as session:
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
        async with self._session_local() as session:
            query = select(message_model).where(message_model.team_name == team_name)

            if broadcast is not None:
                query = query.where(message_model.broadcast.is_(broadcast))

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()
            return rows

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        """Mark a message as read by a member (works for both direct and broadcast messages)."""
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()
        async with self._session_local() as session:
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

            await session.commit()

            team_logger.info("Message %s marked as read by %s", message_id, member_name)
            return True
