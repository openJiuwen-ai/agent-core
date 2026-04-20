# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Messaging Module

This module provides messaging functionality for team members and team leader.
"""

import uuid
from typing import (
    List,
    Optional,
)

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.database import (
    TeamDatabase,
    TeamMessageBase
)
from openjiuwen.agent_teams.schema.events import (
    BroadcastEvent,
    EventMessage,
    MessageEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.core.common.logging import team_logger


class TeamMessageManager:
    """Team Message Manager

    This class provides messaging functionality for team communication.
    Both team leader and members can use this class to send messages.

    Attributes:
        team_name: Team identifier
        member_name: Current member identifier, used as sender in messages
        db: Team database instance
        messager: Messager instance for event publishing
    """

    def __init__(self, team_name: str, member_name: str, db: TeamDatabase, messager: Messager):
        """Initialize team messaging manager

        Args:
            team_name: Team identifier
            member_name: Current member identifier
            db: Team database instance
            messager: Messager instance for event publishing
        """
        self.team_name = team_name
        self.member_name = member_name
        self.db = db
        self.messager = messager

    async def send_message(
        self,
        content: str,
        to_member_name: str,
        from_member_name: str | None = None,
    ) -> Optional[str]:
        """Send a point-to-point message.

        Args:
            content: Message content.
            to_member_name: Recipient member ID.
            from_member_name: Override sender ID. Defaults to self.member_name.
        """
        sender = from_member_name or self.member_name
        message_id = str(uuid.uuid4())

        success = await self.db.create_message(
            message_id=message_id,
            team_name=self.team_name,
            from_member_name=sender,
            content=content,
            to_member_name=to_member_name,
            broadcast=False,
        )
        if not success:
            team_logger.error(f"Failed to create message {message_id}")
            return None

        try:
            await self.messager.publish(
                topic_id=TeamTopic.MESSAGE.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MessageEvent(
                    message_id=message_id,
                    team_name=self.team_name,
                    from_member_name=sender,
                    to_member_name=to_member_name,
                )),
            )
            team_logger.debug(f"Message event published: {message_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish message event for {message_id}: {e}")

        team_logger.debug(f"Message sent from {sender} to {to_member_name}: {message_id}")
        return message_id

    async def broadcast_message(self, content: str) -> Optional[str]:
        """Send a broadcast message.

        Args:
            content: Message content.
        """
        message_id = str(uuid.uuid4())

        success = await self.db.create_message(
            message_id=message_id,
            team_name=self.team_name,
            from_member_name=self.member_name,
            content=content,
            to_member_name=None,
            broadcast=True,
        )
        if not success:
            team_logger.error(f"Failed to create broadcast message {message_id}")
            return None

        try:
            await self.messager.publish(
                topic_id=TeamTopic.MESSAGE.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(BroadcastEvent(
                    message_id=message_id,
                    team_name=self.team_name,
                    from_member_name=self.member_name,
                )),
            )
            team_logger.debug(f"Broadcast event published: {message_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish broadcast event for {message_id}: {e}")

        team_logger.debug(f"Broadcast message sent from {self.member_name}: {message_id}")
        return message_id

    async def get_messages(
        self,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None
    ) -> List[TeamMessageBase]:
        """Get direct (point-to-point) messages for a member

        Args:
            to_member_name: Member ID who is recipient of the messages
            unread_only: Whether to only return unread messages
            from_member_name: Optional filter for messages from a specific sender

        Returns:
            List of TeamMessage objects

        Example:
            # Get all direct messages for a member
            messages = messaging.get_messages(to_member_name="member1")

            # Get unread direct messages for a member
            messages = messaging.get_messages(to_member_name="member1", unread_only=True)

            # Get direct messages from a specific sender
            messages = messaging.get_messages(to_member_name="member1", from_member_name="leader")
        """
        return await self.db.get_messages(
            team_name=self.team_name,
            to_member_name=to_member_name,
            unread_only=unread_only,
            from_member_name=from_member_name
        )

    async def get_broadcast_messages(
        self,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None
    ) -> List[TeamMessageBase]:
        """Get broadcast messages for a member, with read status

        Args:
            member_name: Member ID to check read status for
            unread_only: Whether to only return unread broadcast messages
            from_member_name: Optional filter for messages from a specific sender

        Returns:
            List of TeamMessage objects

        Example:
            # Get all broadcast messages for a member
            messages = messaging.get_broadcast_messages(member_name="member1")

            # Get unread broadcast messages for a member
            messages = messaging.get_broadcast_messages(member_name="member1", unread_only=True)

            # Get broadcast messages from a specific sender
            messages = messaging.get_broadcast_messages(member_name="member1", from_member_name="leader")
        """
        return await self.db.get_broadcast_messages(
            team_name=self.team_name,
            member_name=member_name,
            unread_only=unread_only,
            from_member_name=from_member_name
        )

    async def get_team_messages(self, team_name: str) -> List[TeamMessageBase]:
        """Get all messages for a team

        Args:
            team_name: Team ID

        Returns:
            List of TeamMessage objects
        """
        return await self.db.get_team_messages(team_name=team_name)

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        """Mark a message as read by a member

        Args:
            message_id: Message ID to mark as read
            member_name: Member ID who is reading the message

        Returns:
            True if successful, False otherwise

        Example:
            success = messaging.mark_message_read(message_id="msg_123", member_name="member1")
        """
        success = await self.db.mark_message_read(message_id=message_id, member_name=member_name)
        if success:
            team_logger.debug(f"Message {message_id} marked as read by {member_name}")
        else:
            team_logger.error(f"Failed to mark message {message_id} as read by {member_name}")
        return success
