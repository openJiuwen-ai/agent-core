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
from openjiuwen.agent_teams.schema.events import (
    BroadcastEvent,
    EventMessage,
    MessageEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database import TeamDatabase, TeamMessageBase
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

    def __init__(
        self,
        team_name: str,
        member_name: str,
        db: TeamDatabase,
        messager: Messager,
    ):
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
        protocol: str = "plain",
        meta: dict | None = None,
    ) -> Optional[str]:
        """Send a point-to-point message.

        Args:
            content: Message content. Empty for a templated framework message,
                whose text is rendered from ``meta`` at delivery time.
            to_member_name: Recipient member ID.
            from_member_name: Override sender ID. Defaults to self.member_name.
            protocol: Message format — ``"plain"`` for normal text,
                ``"json"`` for structured payloads.
            meta: Framework-only delivery payload (template key + refs +
                params). Not reachable from the send_message tool — only the
                framework (scheduler handoffs) sets it. See
                ``message_template.py``.
        """
        sender = from_member_name or self.member_name
        message_id = str(uuid.uuid4())

        success = await self.db.message.create_message(
            message_id=message_id,
            team_name=self.team_name,
            from_member_name=sender,
            content=content,
            to_member_name=to_member_name,
            broadcast=False,
            is_read=False,
            protocol=protocol,
            meta=meta,
        )
        if not success:
            team_logger.error(f"Failed to create message {message_id}")
            return None

        try:
            await self.messager.publish(
                topic_id=TeamTopic.MESSAGE.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    MessageEvent(
                        message_id=message_id,
                        team_name=self.team_name,
                        from_member_name=sender,
                        to_member_name=to_member_name,
                    )
                ),
            )
            team_logger.debug(f"Message event published: {message_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish message event for {message_id}: {e}")

        team_logger.debug(f"Message sent from {sender} to {to_member_name}: {message_id}")
        return message_id

    async def broadcast_message(
        self,
        content: str,
        from_member_name: str | None = None,
    ) -> Optional[str]:
        """Send a broadcast message.

        Args:
            content: Message content.
            from_member_name: Override sender ID. Defaults to
                ``self.member_name``.
        """
        sender = from_member_name or self.member_name
        message_id = str(uuid.uuid4())

        success = await self.db.message.create_message(
            message_id=message_id,
            team_name=self.team_name,
            from_member_name=sender,
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
                message=EventMessage.from_event(
                    BroadcastEvent(
                        message_id=message_id,
                        team_name=self.team_name,
                        from_member_name=sender,
                    )
                ),
            )
            team_logger.debug(f"Broadcast event published: {message_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish broadcast event for {message_id}: {e}")

        team_logger.debug(f"Broadcast message sent from {sender}: {message_id}")
        return message_id

    async def multicast_message(
        self,
        content: str,
        to_member_names: List[str],
        from_member_name: str | None = None,
        protocol: str = "plain",
    ) -> List[str]:
        """Send identical content to several members as N point-to-point messages.

        Persists all recipients in a SINGLE DB transaction (one fsync) via
        ``create_direct_messages`` instead of N separate ``send_message``
        writes — this is the multicast write-tail optimization. Message events
        are published per recipient AFTER the commit; the messager bus runs off
        the DB write lock, so the fan-out never extends the write critical
        section.

        Args:
            content: Shared message body.
            to_member_names: Recipient member ids (caller de-duplicates /
                validates existence; empty list is a no-op).
            from_member_name: Override sender id. Defaults to
                ``self.member_name``.
            protocol: Message format (``"plain"`` / ``"json"``).

        Returns:
            The created message ids in recipient order, or an empty list when
            there were no recipients or the batch write failed.
        """
        sender = from_member_name or self.member_name
        if not to_member_names:
            return []

        pairs = [(str(uuid.uuid4()), to) for to in to_member_names]
        created = await self.db.message.create_direct_messages(
            team_name=self.team_name,
            from_member_name=sender,
            content=content,
            recipients=pairs,
            protocol=protocol,
        )
        if created != len(pairs):
            team_logger.error("Failed to batch-create multicast messages from %s", sender)
            return []

        for message_id, to_member_name in pairs:
            try:
                await self.messager.publish(
                    topic_id=TeamTopic.MESSAGE.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(
                        MessageEvent(
                            message_id=message_id,
                            team_name=self.team_name,
                            from_member_name=sender,
                            to_member_name=to_member_name,
                        )
                    ),
                )
            except Exception as e:
                team_logger.error(f"Failed to publish message event for {message_id}: {e}")

        team_logger.debug(f"Multicast sent from {sender} to {len(pairs)} members")
        return [message_id for message_id, _ in pairs]

    async def get_messages(
        self,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
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
        return await self.db.message.get_messages(
            team_name=self.team_name,
            to_member_name=to_member_name,
            unread_only=unread_only,
            from_member_name=from_member_name,
        )

    async def get_broadcast_messages(
        self,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
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
        return await self.db.message.get_broadcast_messages(
            team_name=self.team_name,
            member_name=member_name,
            unread_only=unread_only,
            from_member_name=from_member_name,
        )

    async def get_team_messages(self, team_name: str) -> List[TeamMessageBase]:
        """Get all messages for a team

        Args:
            team_name: Team ID

        Returns:
            List of TeamMessage objects
        """
        return await self.db.message.get_team_messages(team_name=team_name)

    async def has_unread_messages(self, *, include_broadcast: bool = True) -> bool:
        """Whether any team message is still unread by its intended reader.

        Args:
            include_broadcast: When False, only direct (point-to-point)
                messages count toward the check; broadcast messages are
                excluded. Defaults to True.

        Returns:
            True if at least one matching message has not been read by its
            intended reader.
        """
        return await self.db.message.has_unread_messages(self.team_name, include_broadcast=include_broadcast)

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
        success = await self.db.message.mark_message_read(message_id=message_id, member_name=member_name)
        if success:
            team_logger.debug(f"Message {message_id} marked as read by {member_name}")
        else:
            team_logger.error(f"Failed to mark message {message_id} as read by {member_name}")
        return success

    async def mark_messages_read(self, messages: List[TeamMessageBase], member_name: str) -> int:
        """Mark a batch of delivered messages read for one member.

        Takes the raw message objects (not ids) so this layer — which owns
        read-state semantics — can honour the two read models before handing
        a safe id list to the DAO:

          - Direct messages each flip their own ``is_read`` row, so every
            direct id is kept.
          - Broadcast read state is a per-member watermark: one
            ``(member, team)`` row keyed by the newest read broadcast
            timestamp. Marking the newest broadcast advances the watermark
            past every older one, so only that single id is kept. Passing
            more than one broadcast id to the DAO in one transaction would
            re-insert the same ``(member, team)`` primary key and the commit
            would raise ``UNIQUE constraint failed``, rolling back the whole
            batch and stalling the mailbox. Collapsing broadcasts here keeps
            that invariant out of every caller (handler, external client,
            stress harness).

        The DAO write stays a single transaction (one fsync) — the dominant
        write-throughput lever on SQLite.

        Args:
            messages: Delivered message objects to mark read.
            member_name: Member who read the messages.

        Returns:
            Count of messages whose read state was applied.
        """
        if not messages:
            return 0

        direct_ids = [m.message_id for m in messages if not m.broadcast]
        broadcasts = [m for m in messages if m.broadcast]
        ids = list(direct_ids)
        if broadcasts:
            newest_broadcast = max(broadcasts, key=lambda m: m.timestamp)
            ids.append(newest_broadcast.message_id)
        if not ids:
            return 0

        marked = await self.db.message.mark_messages_read(ids, member_name)
        if marked:
            team_logger.debug("Marked %d messages read by %s", marked, member_name)
        return marked
