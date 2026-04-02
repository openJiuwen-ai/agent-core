# coding: utf-8

"""Unit tests for persistent team feature."""

from unittest.mock import (
    AsyncMock,
)

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordinator import (
    CoordinatorLoop,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.status import (
    is_valid_transition,
    MEMBER_TRANSITIONS,
    MemberStatus,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_events import (
    EventMessage,
    TeamEvent,
    TeamStandbyEvent,
)
from tests.test_logger import logger


# ========== Fixtures ==========

@pytest.fixture
def db_config():
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("session_1")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def message_bus():
    bus = AsyncMock(spec=Messager)
    yield bus


# ========== CoordinatorLoop pause/resume ==========

class TestCoordinatorLoopPauseResume:

    @pytest.mark.asyncio
    async def test_pause_polls_stops_polling(self):
        loop = CoordinatorLoop(role=TeamRole.LEADER)
        await loop.start()
        assert loop.is_running
        assert not loop.polls_paused

        await loop.pause_polls()
        assert loop.polls_paused
        assert loop._mailbox_poll_task is None
        assert loop._task_poll_task is None
        assert loop.is_running  # main loop still running

        await loop.stop()

    @pytest.mark.asyncio
    async def test_resume_polls_restarts_polling(self):
        loop = CoordinatorLoop(role=TeamRole.LEADER)
        await loop.start()

        await loop.pause_polls()
        assert loop.polls_paused

        await loop.resume_polls()
        assert not loop.polls_paused
        assert loop._mailbox_poll_task is not None
        assert loop._task_poll_task is not None

        await loop.stop()

    @pytest.mark.asyncio
    async def test_pause_polls_idempotent(self):
        loop = CoordinatorLoop(role=TeamRole.LEADER)
        await loop.start()
        await loop.pause_polls()
        await loop.pause_polls()  # should not raise
        assert loop.polls_paused
        await loop.stop()

    @pytest.mark.asyncio
    async def test_resume_polls_noop_when_not_paused(self):
        loop = CoordinatorLoop(role=TeamRole.LEADER)
        await loop.start()
        original_mailbox = loop._mailbox_poll_task
        await loop.resume_polls()  # should be noop
        assert loop._mailbox_poll_task is original_mailbox
        await loop.stop()


# ========== TeamStandbyEvent ==========

class TestTeamStandbyEvent:

    def test_standby_event_serialization(self):
        event = TeamStandbyEvent(team_id="test_team")
        msg = EventMessage.from_event(event)
        assert msg.event_type == TeamEvent.STANDBY
        logger.info("Standby event type: {}", msg.event_type)

    def test_standby_event_deserialization(self):
        event = TeamStandbyEvent(team_id="test_team")
        msg = EventMessage.from_event(event)
        payload = msg.get_payload()
        assert isinstance(payload, TeamStandbyEvent)
        assert payload.team_id == "test_team"


# ========== MemberStatus READY self-transition ==========

class TestReadySelfTransition:

    def test_ready_to_ready_is_valid(self):
        assert is_valid_transition(MemberStatus.READY, MemberStatus.READY, MEMBER_TRANSITIONS)

    def test_ready_to_busy_still_valid(self):
        assert is_valid_transition(MemberStatus.READY, MemberStatus.BUSY, MEMBER_TRANSITIONS)


# ========== Persistent team build_team + member status ==========

class TestPersistentTeamBuildTeam:

    @pytest_asyncio.fixture
    async def persistent_team(self, db, message_bus):
        predefined = [
            TeamMemberSpec(
                member_id="dev-1",
                name="Developer",
                persona="Backend dev",
                domain="backend",
            ),
        ]
        return TeamBackend(
            team_id="persistent_team",
            member_id="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            predefined_members=predefined,
        )

    @pytest.mark.asyncio
    async def test_build_team_persistent_members_unstarted(self, persistent_team, db):
        await persistent_team.build_team(
            name="Persistent Team",
            desc="A persistent team",
            prompt="prompt",
            leader_name="Leader",
            leader_desc="PM",
        )

        dev = await db.get_member("dev-1")
        assert dev.status == MemberStatus.UNSTARTED.value
        logger.info("Persistent team member status after build: {}", dev.status)

    @pytest.mark.asyncio
    async def test_persistent_team_member_can_go_ready_then_ready(self, persistent_team, db):
        """Verify READY -> READY transition works for persistent team resume."""
        await persistent_team.build_team(
            name="Persistent Team",
            desc="desc",
            prompt="prompt",
            leader_name="Leader",
            leader_desc="PM",
        )
        # Simulate member starting up
        await db.update_member_status("dev-1", MemberStatus.READY.value)
        dev = await db.get_member("dev-1")
        assert dev.status == MemberStatus.READY.value

        # Simulate persistent team resume (READY -> READY)
        success = await db.update_member_status("dev-1", MemberStatus.READY.value)
        assert success
        dev = await db.get_member("dev-1")
        assert dev.status == MemberStatus.READY.value


# ========== resume_for_new_session ==========

class TestResumeForNewSession:

    @pytest_asyncio.fixture
    async def db_file(self, tmp_path):
        """Use file-based SQLite so state persists across session switches."""
        db_path = str(tmp_path / "team.db")
        config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=db_path)
        token = set_session_id("session_1")
        database = TeamDatabase(config)
        try:
            await database.initialize()
            yield database, config
        finally:
            reset_session_id(token)
            await database.close()

    @pytest.mark.asyncio
    async def test_new_session_creates_dynamic_tables(self, db_file):
        database, config = db_file
        team_id = "persistent_team"
        await database.create_team(team_id=team_id, name="PT", leader_member_id="leader1")

        # Switch session
        token = set_session_id("session_2")
        try:
            await database.create_cur_session_tables()
            # Verify team still exists (static table)
            team = await database.get_team(team_id)
            assert team is not None
            assert team.name == "PT"
            logger.info("Team persists across sessions: {}", team.name)
        finally:
            reset_session_id(token)
