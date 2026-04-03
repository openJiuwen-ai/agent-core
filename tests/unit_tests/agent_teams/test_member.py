# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamMember module"""

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.core.multi_agent.team_runtime.message_bus import MessageBus
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture
def agent_card():
    """Provide AgentCard instance for testing"""
    return AgentCard(
        name="TestAgent",
        description="Test agent for unit tests",
        version="1.0.0"
    )


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing"""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance"""
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def message_bus():
    """Provide MessageBus instance for testing"""
    bus = MessageBus()
    await bus.start()
    try:
        yield bus
    finally:
        await bus.stop()


@pytest_asyncio.fixture
async def team_member(db, agent_card, message_bus):
    """Provide initialized TeamMember instance"""
    await db.create_team(
        team_id="test_team",
        name="Test Team",
        leader_member_id="leader1"
    )
    await db.create_member(
        member_id="member1",
        team_id="test_team",
        name="Test Member",
        agent_card=agent_card.model_dump_json(),
        status=MemberStatus.READY.value,
        execution_status=ExecutionStatus.IDLE.value
    )
    return TeamMember(
        member_id="member1",
        team_id="test_team",
        name="Test Member",
        agent_card=agent_card,
        db=db,
        messager=message_bus
    )


class TestTeamMemberInit:
    """Test TeamMember initialization"""

    @pytest.mark.asyncio
    async def test_member_initialization(self, team_member, agent_card):
        """Test that member is initialized with correct values"""
        assert team_member.member_id == "member1"
        assert team_member.team_id == "test_team"
        assert team_member.name == "Test Member"
        assert team_member.agent_card == agent_card
        assert await team_member.status() == MemberStatus.READY
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_member_with_optional_fields(self, db, agent_card, message_bus):
        """Test member initialization with optional fields"""
        await db.create_team(
            team_id="test_team",
            name="Test Team",
            leader_member_id="leader1"
        )

        member = TeamMember(
            member_id="member2",
            team_id="test_team",
            name="Test Member with Options",
            agent_card=agent_card,
            db=db,
            messager=message_bus,
            prompt="You are a helpful assistant",
            desc="A helpful team member"
        )

        assert member.prompt == "You are a helpful assistant"
        assert member.desc == "A helpful team member"


class TestMemberStatus:
    """Test member status management"""

    @pytest.mark.asyncio
    async def test_get_initial_status(self, team_member):
        """Test getting initial status"""
        assert await team_member.status() == MemberStatus.READY

    @pytest.mark.asyncio
    async def test_update_status_valid_transition(self, team_member):
        """Test updating status with valid transition"""
        result = await team_member.update_status(MemberStatus.BUSY)
        assert result is True
        assert await team_member.status() == MemberStatus.BUSY

    @pytest.mark.asyncio
    async def test_update_status_invalid_transition(self, team_member):
        """Test updating status with invalid transition"""
        # Set to BUSY
        await team_member.update_status(MemberStatus.BUSY)

        # Try invalid transition: BUSY -> SHUTDOWN (should go through SHUTDOWN_REQUESTED)
        result = await team_member.update_status(MemberStatus.SHUTDOWN)
        assert result is False
        assert await team_member.status() == MemberStatus.BUSY

    @pytest.mark.asyncio
    async def test_status_transition_ready_to_busy(self, team_member):
        """Test READY -> BUSY transition"""
        assert await team_member.status() == MemberStatus.READY
        result = await team_member.update_status(MemberStatus.BUSY)
        assert result is True
        assert await team_member.status() == MemberStatus.BUSY

    @pytest.mark.asyncio
    async def test_status_transition_busy_to_ready(self, team_member):
        """Test BUSY -> READY transition"""
        await team_member.update_status(MemberStatus.BUSY)
        result = await team_member.update_status(MemberStatus.READY)
        assert result is True
        assert await team_member.status() == MemberStatus.READY

    @pytest.mark.asyncio
    async def test_status_transition_ready_to_shutdown_requested(self, team_member):
        """Test READY -> SHUTDOWN_REQUESTED transition"""
        result = await team_member.update_status(MemberStatus.SHUTDOWN_REQUESTED)
        assert result is True
        assert await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED

    @pytest.mark.asyncio
    async def test_status_transition_shutdown_requested_to_shutdown(self, team_member):
        """Test SHUTDOWN_REQUESTED -> SHUTDOWN transition"""
        await team_member.update_status(MemberStatus.SHUTDOWN_REQUESTED)
        result = await team_member.update_status(MemberStatus.SHUTDOWN)
        assert result is True
        assert await team_member.status() == MemberStatus.SHUTDOWN

    @pytest.mark.asyncio
    async def test_status_transition_ready_to_error(self, team_member):
        """Test READY -> ERROR transition"""
        result = await team_member.update_status(MemberStatus.ERROR)
        assert result is True
        assert await team_member.status() == MemberStatus.ERROR

    @pytest.mark.asyncio
    async def test_status_transition_error_to_ready(self, team_member):
        """Test ERROR -> READY transition"""
        await team_member.update_status(MemberStatus.ERROR)
        result = await team_member.update_status(MemberStatus.READY)
        assert result is True
        assert await team_member.status() == MemberStatus.READY

    @pytest.mark.asyncio
    async def test_status_no_transition_from_shutdown(self, team_member):
        """Test that no transitions are allowed from SHUTDOWN"""
        await team_member.update_status(MemberStatus.BUSY)
        await team_member.update_status(MemberStatus.SHUTDOWN_REQUESTED)
        await team_member.update_status(MemberStatus.SHUTDOWN)

        # Try to transition from SHUTDOWN - should fail
        result = await team_member.update_status(MemberStatus.READY)
        assert result is False
        assert await team_member.status() == MemberStatus.SHUTDOWN


class TestExecutionStatus:
    """Test execution status management"""

    @pytest.mark.asyncio
    async def test_get_initial_execution_status(self, team_member):
        """Test getting initial execution status"""
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_update_execution_status_valid_transition(self, team_member):
        """Test updating execution status with valid transition"""
        result = await team_member.update_execution_status(ExecutionStatus.STARTING)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.STARTING

    @pytest.mark.asyncio
    async def test_update_execution_status_invalid_transition(self, team_member):
        """Test updating execution status with invalid transition"""
        # Try invalid transition: IDLE -> RUNNING (should go through STARTING)
        result = await team_member.update_execution_status(ExecutionStatus.RUNNING)
        assert result is False
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_execution_transition_idle_to_starting(self, team_member):
        """Test IDLE -> STARTING transition"""
        assert await team_member.execution_status() == ExecutionStatus.IDLE
        result = await team_member.update_execution_status(ExecutionStatus.STARTING)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.STARTING

    @pytest.mark.asyncio
    async def test_execution_transition_starting_to_running(self, team_member):
        """Test STARTING -> RUNNING transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        result = await team_member.update_execution_status(ExecutionStatus.RUNNING)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_execution_transition_running_to_completing(self, team_member):
        """Test RUNNING -> COMPLETING transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        result = await team_member.update_execution_status(ExecutionStatus.COMPLETING)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.COMPLETING

    @pytest.mark.asyncio
    async def test_execution_transition_completing_to_completed(self, team_member):
        """Test COMPLETING -> COMPLETED transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        await team_member.update_execution_status(ExecutionStatus.COMPLETING)
        result = await team_member.update_execution_status(ExecutionStatus.COMPLETED)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execution_transition_completed_to_idle(self, team_member):
        """Test COMPLETED -> IDLE transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        await team_member.update_execution_status(ExecutionStatus.COMPLETING)
        await team_member.update_execution_status(ExecutionStatus.COMPLETED)
        result = await team_member.update_execution_status(ExecutionStatus.IDLE)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_execution_transition_running_to_cancel_requested(self, team_member):
        """Test RUNNING -> CANCEL_REQUESTED transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        result = await team_member.update_execution_status(ExecutionStatus.CANCEL_REQUESTED)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.CANCEL_REQUESTED

    @pytest.mark.asyncio
    async def test_execution_transition_cancel_requested_to_cancelling(self, team_member):
        """Test CANCEL_REQUESTED -> CANCELLING transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        await team_member.update_execution_status(ExecutionStatus.CANCEL_REQUESTED)
        result = await team_member.update_execution_status(ExecutionStatus.CANCELLING)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.CANCELLING

    @pytest.mark.asyncio
    async def test_execution_transition_cancelling_to_cancelled(self, team_member):
        """Test CANCELLING -> CANCELLED transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.RUNNING)
        await team_member.update_execution_status(ExecutionStatus.CANCEL_REQUESTED)
        await team_member.update_execution_status(ExecutionStatus.CANCELLING)
        result = await team_member.update_execution_status(ExecutionStatus.CANCELLED)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_execution_transition_failed_to_idle(self, team_member):
        """Test FAILED -> IDLE transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.FAILED)
        result = await team_member.update_execution_status(ExecutionStatus.IDLE)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_execution_transition_timed_out_to_idle(self, team_member):
        """Test TIMED_OUT -> IDLE transition"""
        await team_member.update_execution_status(ExecutionStatus.STARTING)
        await team_member.update_execution_status(ExecutionStatus.TIMED_OUT)
        result = await team_member.update_execution_status(ExecutionStatus.IDLE)
        assert result is True
        assert await team_member.execution_status() == ExecutionStatus.IDLE


class TestStatusTransitionsComplex:
    """Test complex status transition scenarios"""

    @pytest.mark.asyncio
    async def test_full_member_lifecycle(self, team_member):
        """Test complete member lifecycle: READY -> BUSY -> READY -> SHUTDOWN_REQUESTED -> SHUTDOWN"""
        assert await team_member.status() == MemberStatus.READY

        # Start working
        assert await team_member.update_status(MemberStatus.BUSY)
        assert await team_member.status() == MemberStatus.BUSY

        # Finish work
        assert await team_member.update_status(MemberStatus.READY)
        assert await team_member.status() == MemberStatus.READY

        # Request shutdown
        assert await team_member.update_status(MemberStatus.SHUTDOWN_REQUESTED)
        assert await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED

        # Complete shutdown
        assert await team_member.update_status(MemberStatus.SHUTDOWN)
        assert await team_member.status() == MemberStatus.SHUTDOWN

    @pytest.mark.asyncio
    async def test_full_execution_lifecycle(self, team_member):
        """Test complete execution lifecycle: IDLE -> STARTING -> RUNNING -> COMPLETING -> COMPLETED -> IDLE"""
        assert await team_member.execution_status() == ExecutionStatus.IDLE

        # Start execution
        assert await team_member.update_execution_status(ExecutionStatus.STARTING)
        assert await team_member.execution_status() == ExecutionStatus.STARTING

        # Running
        assert await team_member.update_execution_status(ExecutionStatus.RUNNING)
        assert await team_member.execution_status() == ExecutionStatus.RUNNING

        # Completing
        assert await team_member.update_execution_status(ExecutionStatus.COMPLETING)
        assert await team_member.execution_status() == ExecutionStatus.COMPLETING

        # Completed
        assert await team_member.update_execution_status(ExecutionStatus.COMPLETED)
        assert await team_member.execution_status() == ExecutionStatus.COMPLETED

        # Back to idle
        assert await team_member.update_execution_status(ExecutionStatus.IDLE)
        assert await team_member.execution_status() == ExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_cancellation_flow(self, team_member):
        """Test execution cancellation flow: IDLE -> STARTING -> RUNNING -> CANCEL_REQUESTED -> CANCELLING -> CANCELLED -> IDLE"""
        assert await team_member.execution_status() == ExecutionStatus.IDLE

        assert await team_member.update_execution_status(ExecutionStatus.STARTING)
        assert await team_member.update_execution_status(ExecutionStatus.RUNNING)
        assert await team_member.update_execution_status(ExecutionStatus.CANCEL_REQUESTED)
        assert await team_member.update_execution_status(ExecutionStatus.CANCELLING)
        assert await team_member.update_execution_status(ExecutionStatus.CANCELLED)
        assert await team_member.update_execution_status(ExecutionStatus.IDLE)

        assert await team_member.execution_status() == ExecutionStatus.IDLE
