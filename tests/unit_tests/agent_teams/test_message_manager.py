# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamMessageManager module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.spawn.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
    TeamMessageBase,
)
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.core.single_agent import AgentCard


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing"""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance"""
    database = TeamDatabase(db_config)
    token = set_session_id("session_id")
    try:
        await database.initialize()
        yield database
    finally:
        # Close and cleanup database
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def message_bus():
    """Provide Messager mock instance for testing"""
    bus = AsyncMock(spec=Messager)
    yield bus


@pytest_asyncio.fixture
async def team_messaging(db, message_bus):
    """Create TeamMessageManager instance with in-memory database"""
    # First create a team
    await db.team.create_team(
        team_name="test_team_123",
        display_name="Test Team",
        leader_member_name="leader"
    )
    # create member
    agent_card = AgentCard(name="TestAgent").model_dump_json()
    await db.member.create_member(
        member_name="member1",
        team_name="test_team_123",
        display_name="Member One",
        agent_card=agent_card,
        status="busy"
    )
    agent_card = AgentCard(name="TestAgent").model_dump_json()
    await db.member.create_member(
        member_name="member2",
        team_name="test_team_123",
        display_name="Member Two",
        agent_card=agent_card,
        status="busy"
    )
    # Then create messaging instance
    return TeamMessageManager(team_name="test_team_123", db=db, messager=message_bus, member_name="member1")


# ==================== Test send_message ====================

class TestSendMessage:
    """Test send_message method"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_send_message_success(self, team_messaging):
        """Test successful point-to-point message sending"""
        message_id = await team_messaging.send_message(
            content="Hello member2",
            to_member_name="member2"
        )

        assert message_id is not None
        assert isinstance(message_id, str)

        # Verify message was stored using get interface
        messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert len(messages) == 1
        assert messages[0].message_id == message_id
        assert messages[0].content == "Hello member2"
        assert messages[0].from_member_name == "member1"
        assert messages[0].to_member_name == "member2"
        assert messages[0].broadcast is False
        assert messages[0].is_read is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_send_message_unicode_content(self, team_messaging):
        """Test sending message with unicode content"""
        message_id = await team_messaging.send_message(
            content="你好，世界！🎉",
            to_member_name="member2"
        )

        assert message_id is not None
        messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert messages[0].content == "你好，世界！🎉"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_send_multiple_messages(self, team_messaging):
        """Test sending multiple messages"""
        message_ids = []
        for i in range(5):
            message_id = await team_messaging.send_message(
                content=f"Message {i}",
                to_member_name="receiver_0"
            )
            message_ids.append(message_id)
            assert message_id is not None

        # Verify all messages were stored
        messages = await team_messaging.get_messages(
            to_member_name="receiver_0")
        assert len(messages) == 5
        for i, msg in enumerate(messages):
            assert msg.content == f"Message {i}"
            assert msg.message_id == message_ids[i]


# ==================== Test broadcast_message ====================

class TestBroadcastMessage:
    """Test broadcast_message method"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_broadcast_message_success(self, team_messaging):
        """Test successful broadcast message sending"""
        leader_messaging = TeamMessageManager(team_messaging.team_name, member_name="leader", db=team_messaging.db,
                                              messager=message_bus)

        message_id = await leader_messaging.broadcast_message(
            content="Team meeting at 3PM",
        )

        assert message_id is not None
        assert isinstance(message_id, str)

        # Verify broadcast message was stored using get interface
        broadcasts = await team_messaging.get_broadcast_messages(member_name="member1")
        assert len(broadcasts) == 1
        assert broadcasts[0].message_id == message_id
        assert broadcasts[0].content == "Team meeting at 3PM"
        assert broadcasts[0].from_member_name == "leader"
        assert broadcasts[0].to_member_name is None
        assert broadcasts[0].broadcast is True
        assert broadcasts[0].is_read is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_broadcast_multiple_messages(self, team_messaging):
        """Test sending multiple broadcast messages"""
        senders = ["leader", "member1", "member2"]
        message_ids = []

        for sender in senders:
            if sender == team_messaging.messager:
                message_id = await team_messaging.broadcast_message(
                    content=f"Announcement from {sender}"
                )
                message_ids.append(message_id)
            else:
                sender_messaging = TeamMessageManager(team_messaging.team_name, member_name=sender, db=team_messaging.db,
                                                      messager=message_bus)
                message_id = await sender_messaging.broadcast_message(content=f"Announcement from {sender}")
                message_ids.append(message_id)

        # Verify all broadcasts except current member self
        broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")
        assert len(broadcasts) == 2
        for i, msg in enumerate(broadcasts):
            assert msg.broadcast is True
            assert msg.from_member_name == senders[i]
            assert msg.message_id == message_ids[i]


# ==================== Test get_messages ====================

class TestGetMessages:
    """Test get_messages method"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_messages_all(self, team_messaging):
        """Test getting all direct messages for a member"""
        # Create test messages
        await team_messaging.send_message("Msg1", "member2")
        await team_messaging.send_message("Msg2", "member2")

        messages = await team_messaging.get_messages(
            to_member_name="member2")

        assert len(messages) == 2
        for msg in messages:
            assert isinstance(msg, TeamMessageBase)
        assert messages[0].content == "Msg1"
        assert messages[1].content == "Msg2"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_messages_for_member(self, team_messaging):
        """Test getting messages for a specific member"""
        # Create test messages
        await team_messaging.send_message("To member2", "member2")
        await team_messaging.send_message("To member1", "member1")
        await team_messaging.send_message("Also to member2", "member2")

        messages = await team_messaging.get_messages(
            to_member_name="member2")

        assert len(messages) == 2
        for msg in messages:
            assert msg.to_member_name == "member2"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_messages_unread_only(self, team_messaging):
        """Test getting unread messages only"""
        # Create messages and mark one as read
        msg_id1 = await team_messaging.send_message("Unread message", "member2")
        msg_id2 = await team_messaging.send_message("Read message", "member2")
        await team_messaging.mark_message_read(msg_id2, "member2")

        messages = await team_messaging.get_messages(
            to_member_name="member2", unread_only=True)

        assert len(messages) == 1
        assert messages[0].message_id == msg_id1
        assert messages[0].is_read is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_messages_empty(self, team_messaging):
        """Test getting messages when no messages exist"""
        messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert len(messages) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_messages_mixed_broadcast_and_direct(self, team_messaging):
        """Test getting messages when both broadcast and direct messages exist"""
        await team_messaging.send_message("Direct message", "member2")
        await team_messaging.broadcast_message("Broadcast")

        # Get direct messages for member2
        direct_messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert len(direct_messages) == 1
        assert direct_messages[0].broadcast is False


# ==================== Test get_broadcast_messages ====================

class TestGetBroadcastMessages:
    """Test get_broadcast_messages method"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_broadcast_messages_all(self, team_messaging):
        """Test getting all broadcast messages"""
        # Create broadcast messages
        await team_messaging.broadcast_message("Announcement 1")
        await team_messaging.broadcast_message("Announcement 2")

        messages = await team_messaging.get_broadcast_messages(member_name="member2")

        assert len(messages) == 2
        for msg in messages:
            assert isinstance(msg, TeamMessageBase)
            assert msg.broadcast is True
            assert msg.to_member_name is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_broadcast_messages_unread_only(self, team_messaging):
        """Test getting unread broadcast messages only"""
        # Create broadcast messages and mark one as read
        msg_id2 = await team_messaging.broadcast_message("Old announcement")
        msg_id1 = await team_messaging.broadcast_message("New announcement")
        await team_messaging.mark_message_read(msg_id2, "member2")

        messages = await team_messaging.get_broadcast_messages(member_name="member2", unread_only=True)

        assert len(messages) == 1
        assert messages[0].message_id == msg_id1
        assert messages[0].is_read is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_broadcast_messages_empty(self, team_messaging):
        """Test getting broadcast messages when none exist"""
        # Create direct message only
        await team_messaging.send_message("Direct message", "member1")

        broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")
        assert len(broadcasts) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_broadcast_messages_filters_correctly(self, team_messaging):
        """Test that get_broadcast_messages correctly filters out direct messages"""
        # Create mixed messages
        await team_messaging.send_message("Direct 1", "member1")
        await team_messaging.broadcast_message("Broadcast 1")
        await team_messaging.send_message("Direct 2", "member2")
        await team_messaging.broadcast_message("Broadcast 2")

        broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")

        assert len(broadcasts) == 2
        for msg in broadcasts:
            assert msg.broadcast is True
            assert msg.to_member_name is None


# ==================== Test. mark_message_read ====================

class TestMarkMessageRead:
    """Test mark_message_read method"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_message_read_success(self, team_messaging):
        """Test successfully marking a message as read"""
        message_id = await team_messaging.send_message("Hello", "member2")

        result = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")

        assert result is True

        # Verify message is marked as read using get interface
        messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert len(messages) == 1
        assert messages[0].is_read is True

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_message_read_nonexistent(self, team_messaging):
        """Test marking a nonexistent message as read"""
        result = await team_messaging.mark_message_read(message_id="nonexistent_msg", member_name="member2")

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_message_read_idempotent(self, team_messaging):
        """Test marking already read message is idempotent"""
        message_id = await team_messaging.send_message("Hello", "member2")

        # Mark as read first time
        result1 = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")
        assert result1 is True

        # Mark as read second time
        result2 = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")
        assert result2 is True

        # Verify still read
        messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert messages[0].is_read is True

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_broadcast_message_read(self, team_messaging):
        """Test marking a broadcast message as read"""
        message_id = await team_messaging.broadcast_message("Announcement")

        result = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")

        assert result is True

        # Verify using get interface
        broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")
        assert len(broadcasts) == 1
        # is_read only for non-broadcast messages, indicates if the recipient has read the message
        assert broadcasts[0].is_read is False
        assert broadcasts[0].broadcast is True


# ==================== Test Integration Scenarios ====================

class TestIntegrationScenarios:
    """Test integration scenarios with combined operations"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_send_and_get_message_flow(self, team_messaging):
        """Test flow: send message, get message, mark as read"""
        # Send message
        message_id = await team_messaging.send_message(
            content="Hello",
            to_member_name="member2"
        )
        assert message_id is not None

        # Get unread messages
        messages = await team_messaging.get_messages(
            to_member_name="member2",
            unread_only=True
        )
        assert len(messages) == 1
        assert messages[0].message_id == message_id

        # Mark as read
        result = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")
        assert result is True

        # Verify no longer in unread
        unread_messages = await team_messaging.get_messages(
            to_member_name="member2",
            unread_only=True
        )
        assert len(unread_messages) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_broadcast_and_get_flow(self, team_messaging):
        """Test flow: broadcast message, get broadcasts, mark as read"""
        # Broadcast message
        message_id = await team_messaging.broadcast_message(
            content="Team meeting at 3PM"
        )
        assert message_id is not None

        # Get broadcast messages
        broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")
        assert len(broadcasts) == 1
        assert broadcasts[0].broadcast is True
        assert broadcasts[0].message_id == message_id

        # Mark as read
        result = await team_messaging.mark_message_read(message_id=message_id, member_name="member2")
        assert result is True

        # Verify no longer in unread broadcasts
        unread_broadcasts = await team_messaging.get_broadcast_messages(member_name="member2", unread_only=True)
        assert len(unread_broadcasts) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_multi_member_messaging_scenario(self, team_messaging):
        """Test a scenario with multiple members sending messages"""
        members = ["member1", "member2", "member3"]

        # Each member sends messages to each other
        for sender in members:
            for recipient in members:
                if sender != recipient:
                    await team_messaging.send_message(
                        content=f"Hello from {sender} to {recipient}",
                        to_member_name=recipient
                    )

        # Leader sends broadcast
        await team_messaging.broadcast_message(
            content="Welcome to the team!"
        )

        # Verify member2 received correct messages (from member1 and member3)
        member2_messages = await team_messaging.get_messages(
            to_member_name="member2")
        assert len(member2_messages) == 2

        # Verify broadcast
        all_broadcasts = await team_messaging.get_broadcast_messages(member_name="member2")
        assert len(all_broadcasts) == 1
        assert all_broadcasts[0].content == "Welcome to the team!"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_message_timestamp_ordering(self, team_messaging):
        """Test that messages are returned in timestamp order"""
        # Send messages with expected order
        msg1 = await team_messaging.send_message("First", "member2")
        msg2 = await team_messaging.send_message("Second", "member1")
        msg3 = await team_messaging.send_message("Third", "member2")

        messages = await team_messaging.get_team_messages(team_name="test_team_123")

        assert len(messages) == 3
        assert messages[0].message_id == msg1
        assert messages[1].message_id == msg2
        assert messages[2].message_id == msg3

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_team_message_isolation(self, db, message_bus):
        """Test that messaging is isolated to a single team"""
        # Create two teams
        await db.team.create_team(team_name="team1", display_name="Team 1", leader_member_name="leader1")
        await db.team.create_team(team_name="team2", display_name="Team 2", leader_member_name="leader2")

        messaging1 = TeamMessageManager(team_name="team1", db=db, messager=message_bus, member_name="leader1")
        messaging2 = TeamMessageManager(team_name="team2", db=db, messager=message_bus, member_name="leader2")

        # Send messages in both teams
        await messaging1.send_message("Team 1 message", "member1")
        await messaging2.send_message("Team 2 message", "member1")

        # Verify isolation using get interfaces
        team1_messages = await messaging1.get_messages(
            to_member_name="member1")
        team2_messages = await messaging2.get_messages(
            to_member_name="member1")

        assert len(team1_messages) == 1
        assert len(team2_messages) == 1
        assert team1_messages[0].content == "Team 1 message"
        assert team2_messages[0].content == "Team 2 message"
