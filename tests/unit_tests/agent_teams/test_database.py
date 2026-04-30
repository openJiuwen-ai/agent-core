# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamDatabase module"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import inspect

from openjiuwen.agent_teams.spawn.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.models import _sanitize_session_id_for_table
from openjiuwen.core.single_agent import AgentCard


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing"""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance"""
    token = set_session_id("session_id")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        # Close and cleanup database
        await database.close()
        reset_session_id(token)


class TestDatabaseConfig:
    """Test DatabaseConfig class;"""

    @pytest.mark.level0
    def test_database_config_default(self):
        """Test default database configuration"""
        config = DatabaseConfig()
        assert config.db_type == DatabaseType.SQLITE
        assert config.connection_string == ""

    @pytest.mark.level0
    def test_database_config_custom_custom(self):
        """Test custom database configuration"""
        config = DatabaseConfig(
            db_type=DatabaseType.POSTGRESQL,
            connection_string="postgresql://user:pass@localhost/db"
        )
        assert config.db_type == DatabaseType.POSTGRESQL
        assert config.connection_string == "postgresql://user:pass@localhost/db"


class TestDatabaseType:
    """Test DatabaseType class;"""

    @pytest.mark.level0
    def test_database_type_values(self):
        """Test database type enum values"""
        assert DatabaseType.SQLITE == "sqlite"
        assert DatabaseType.POSTGRESQL == "postgresql"
        assert DatabaseType.MYSQL == "mysql"


class TestTeamDatabaseInit:
    """Test TeamDatabase initialization"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_database_initialize_creates_tables(self, db_config):
        """Test that initialize creates all necessary tables"""
        database = TeamDatabase(db_config)
        try:
            assert not database._initialized
            assert database.engine is None

            await database.initialize()

            assert database._initialized
            assert database.engine is not None
            assert database.session_local is not None
        finally:
            await database.close()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_database_initialize_idempotent(self, db_config):
        """Test that calling initialize multiple times only initializes once"""
        database = TeamDatabase(db_config)
        try:
            # Initialize
            await database.initialize()
            first_engine = database.engine
            first_session_local = database.session_local
            assert first_engine is not None
            assert first_session_local is not None

            # Call again - should return early and not reinitialize
            await database.initialize()

            # Verify engine and session_local were not recreated
            assert database.engine is first_engine
            assert database.session_local is first_session_local
        finally:
            await database.close()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_unsupported_database_type_raises(self):
        """Test that unsupported database types raise NotImplementedError"""
        config = DatabaseConfig(
            db_type="unsupported_db",
            connection_string=":memory:"
        )
        database = TeamDatabase(config)
        try:
            with pytest.raises(NotImplementedError) as exc_info:
                await database.initialize()
            assert "not yet implemented" in str(exc_info.value)
        finally:
            if database.engine:
                await database.close()

    @pytest.mark.asyncio
    async def test_postgresql_initialize_uses_asyncpg_engine(self, monkeypatch):
        """Test PostgreSQL initialization uses asyncpg DSN and pool settings."""
        captured: dict = {}

        class _FakeConn:
            async def run_sync(self, _fn, *args, **kwargs):
                return None

        class _FakeBeginCtx:
            async def __aenter__(self):
                return _FakeConn()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeEngine:
            def begin(self):
                return _FakeBeginCtx()

            async def dispose(self):
                return None

        def _fake_create_async_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeEngine()

        async def _fake_create_cur_session_tables(self):
            return None

        monkeypatch.setattr(
            "openjiuwen.agent_teams.tools.database.engine.create_async_engine",
            _fake_create_async_engine,
        )
        monkeypatch.setattr(
            TeamDatabase,
            "create_cur_session_tables",
            _fake_create_cur_session_tables,
        )

        config = DatabaseConfig(
            db_type=DatabaseType.POSTGRESQL,
            connection_string="postgresql://user:pass@localhost:5432/team_db",
        )
        database = TeamDatabase(config)
        try:
            await database.initialize()
            assert database._initialized is True
            assert captured["url"] == "postgresql+asyncpg://user:pass@localhost:5432/team_db"
            assert captured["kwargs"]["pool_size"] == 10
            assert captured["kwargs"]["max_overflow"] == 20
            assert captured["kwargs"]["pool_pre_ping"] is True
            assert captured["kwargs"]["pool_recycle"] == 1800
        finally:
            await database.close()


class TestTeamOperations:
    """Test team CRUD operations"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_team_success(self, db):
        """Test successful team creation"""
        success = await db.team.create_team(
            team_name="team1",
            display_name="Test Team",
            leader_member_name="leader1",
            desc="Test description",
            prompt="Test prompt"
        )
        assert success is True

        team = await db.team.get_team("team1")
        assert team is not None
        assert team.team_name == "team1"
        assert team.display_name == "Test Team"
        assert team.leader_member_name == "leader1"
        assert team.desc == "Test description"
        assert team.prompt == "Test prompt"
        assert isinstance(team.created, int)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_team_minimal(self, db):
        """Test team creation with minimal parameters"""
        result = await db.team.create_team(
            team_name="team2",
            display_name="Minimal Team",
            leader_member_name="leader2"
        )
        assert result is True

        team = await db.team.get_team("team2")
        assert team is not None
        assert team.team_name == "team2"
        assert team.display_name == "Minimal Team"
        assert team.leader_member_name == "leader2"
        assert team.desc is None
        assert team.prompt is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_team_duplicate_fails(self, db):
        """Test that creating duplicate team fails"""
        await db.team.create_team(
            team_name="team3",
            display_name="Team 3",
            leader_member_name="leader3"
        )

        success = await db.team.create_team(
            team_name="team3",
            display_name="Duplicate Team",
            leader_member_name="leader3"
        )
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_team_not_found(self, db):
        """Test getting non-existent team returns None"""
        team = await db.team.get_team("nonexistent")
        assert team is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_success(self, db):
        """Test successful team deletion"""
        await db.team.create_team(
            team_name="team4",
            display_name="Team 4",
            leader_member_name="leader4"
        )

        success = await db.team.delete_team("team4")
        assert success is True

        team = await db.team.get_team("team4")
        assert team is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_not_found(self, db):
        """Test deleting non-existent team returns False"""
        success = await db.team.delete_team("nonexistent")
        assert success is False


class TestMemberOperations:
    """Test member CRUD operations"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_member_success(self, db):
        """Test successful member creation"""
        await db.team.create_team(
            team_name="team5",
            display_name="Team 5",
            leader_member_name="leader5"
        )

        agent_card = AgentCard(name="CodeReviewAgent").model_dump_json()
        success = await db.member.create_member(
            member_name="member1",
            team_name="team5",
            display_name="Member One",
            agent_card=agent_card,
            status="ready",
            desc="Code reviewer",
            execution_status="idle",
            prompt="Review code",
        )
        assert success is True

        member = await db.member.get_member("member1", "team5")
        assert member is not None
        assert member.member_name == "member1"
        assert member.team_name == "team5"
        assert member.display_name == "Member One"
        assert member.agent_card == agent_card
        assert member.status == "ready"
        assert member.desc == "Code reviewer"
        assert member.execution_status == "idle"
        assert member.prompt == "Review code"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_member_duplicate_fails(self, db):
        """Test that creating duplicate member fails"""
        await db.team.create_team(
            team_name="team_dup_member",
            display_name="Team Dup Member",
            leader_member_name="leader_dup"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member_dup",
            team_name="team_dup_member",
            display_name="Member Dup",
            agent_card=agent_card,
            status="ready"
        )

        success = await db.member.create_member(
            member_name="member_dup",
            team_name="team_dup_member",
            display_name="Duplicate Member",
            agent_card=agent_card,
            status="busy"
        )
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_member_not_found(self, db):
        """Test getting non-existent member returns None"""
        member = await db.member.get_member("nonexistentmember", "team5")
        assert member is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_member_status(self, db):
        """Test updating member status"""
        await db.team.create_team(
            team_name="team6",
            display_name="Team 6",
            leader_member_name="leader6"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member3",
            team_name="team6",
            display_name="Member Three",
            agent_card=agent_card,
            status="ready"
        )

        success = await db.member.update_member_status("member3", "team6", "busy")
        assert success is True

        member = await db.member.get_member("member3", "team6")
        assert member.status == "busy"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_member_status_not_found(self, db):
        """Test updating status for non-existent member returns False"""
        success = await db.member.update_member_status("nonexistentmember", "team6", "busy")
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_member_execution_status(self, db):
        """Test updating member execution status"""
        await db.team.create_team(
            team_name="team7",
            display_name="Team 7",
            leader_member_name="leader7"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member4",
            team_name="team7",
            display_name="Member Four",
            agent_card=agent_card,
            status="ready",
            execution_status="idle"
        )

        success = await db.member.update_member_execution_status("member4", "team7", "starting")
        assert success is True

        member = await db.member.get_member("member4", "team7")
        assert member.execution_status == "starting"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_member_execution_status_not_found(self, db):
        """Test updating execution status for non-existent member returns False"""
        success = await db.member.update_member_execution_status("nonexistentmember", "team7", "running")
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_team_members(self, db):
        """Test getting all members of a team"""
        await db.team.create_team(
            team_name="team8",
            display_name="Team 8",
            leader_member_name="leader8"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member5",
            team_name="team8",
            display_name="Member Five",
            agent_card=agent_card,
            status="ready"
        )
        await db.member.create_member(
            member_name="member6",
            team_name="team8",
            display_name="Member Six",
            agent_card=agent_card,
            status="busy"
        )

        members = await db.member.get_team_members("team8")
        assert len(members) == 2
        member_ids = [m.member_name for m in members]
        assert "member5" in member_ids
        assert "member6" in member_ids

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_team_members_empty(self, db):
        """Test getting members for empty team returns empty list"""
        await db.team.create_team(
            team_name="team9",
            display_name="Team 9",
            leader_member_name="leader9"
        )

        members = await db.member.get_team_members("team9")
        assert members == []


class TestTaskOperations:
    """Test task CRUD operations"""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_task_success(self, db):
        """Test successful task creation"""
        await db.team.create_team(
            team_name="team10",
            display_name="Team 10",
            leader_member_name="leader10"
        )

        success = await db.task.create_task(
            task_id="task1",
            team_name="team10",
            title="Test Task",
            content="Complete test",
            status="pending"
        )
        assert success is True

        task = await db.task.get_task("task1")
        assert task is not None
        assert task.task_id == "task1"
        assert task.team_name == "team10"
        assert task.title == "Test Task"
        assert task.content == "Complete test"
        assert task.status == "pending"
        assert task.assignee is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_task_not_found(self, db):
        """Test getting non-existent task returns None"""
        task = await db.task.get_task("nonexistenttask")
        assert task is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_status(self, db):
        """Test updating task status"""
        await db.team.create_team(
            team_name="team11",
            display_name="Team 11",
            leader_member_name="leader11"
        )
        await db.task.create_task(
            task_id="task2",
            team_name="team11",
            title="Task 2",
            content="Content",
            status="claimed"
        )

        success = await db.task.update_task_status("task2", "completed")
        assert success is True

        task = await db.task.get_task("task2")
        assert task.status == "completed"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_status_not_found(self, db):
        """Test updating status for non-existent task returns False"""
        success = await db.task.update_task_status("nonexistenttask", "completed")
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_title_only(self, db):
        """Test updating task title only"""
        await db.team.create_team(
            team_name="team_title_update",
            display_name="Team Title Update",
            leader_member_name="leader_title_update"
        )
        await db.task.create_task(
            task_id="task_title",
            team_name="team_title_update",
            title="Original Title",
            content="Original Content",
            status="pending"
        )

        success = await db.task.update_task("task_title", title="Updated Title")
        assert success is True

        task = await db.task.get_task("task_title")
        assert task.title == "Updated Title"
        assert task.content == "Original Content"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_content_only(self, db):
        """Test updating task content only"""
        await db.team.create_team(
            team_name="team_content_update",
            display_name="Team Content Update",
            leader_member_name="leader_content_update"
        )
        await db.task.create_task(
            task_id="task_content",
            team_name="team_content_update",
            title="Original Title",
            content="Original Content",
            status="pending"
        )

        success = await db.task.update_task("task_content", content="Updated Content")
        assert success is True

        task = await db.task.get_task("task_content")
        assert task.title == "Original Title"
        assert task.content == "Updated Content"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_both_title_and_content(self, db):
        """Test updating both task title and content"""
        await db.team.create_team(
            team_name="team_both_update",
            display_name="Team Both Update",
            leader_member_name="leader_both_update"
        )
        await db.task.create_task(
            task_id="task_both",
            team_name="team_both_update",
            title="Original Title",
            content="Original Content",
            status="pending"
        )

        success = await db.task.update_task(
            "task_both",
            title="Updated Title",
            content="Updated Content"
        )
        assert success is True

        task = await db.task.get_task("task_both")
        assert task.title == "Updated Title"
        assert task.content == "Updated Content"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_not_found(self, db):
        """Test updating non-existent task returns False"""
        success = await db.task.update_task("nonexistent_task", title="New Title")
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_update_task_same_values(self, db):
        """Test updating task with same values (no commit needed)"""
        await db.team.create_team(
            team_name="team_same_update",
            display_name="Team Same Update",
            leader_member_name="leader_same_update"
        )
        await db.task.create_task(
            task_id="task_same",
            team_name="team_same_update",
            title="Same Title",
            content="Same Content",
            status="pending"
        )

        # Update with same values - should still return True
        success = await db.task.update_task("task_same", title="Same Title", content="Same Content")
        assert success is True

        task = await db.task.get_task("task_same")
        assert task.title == "Same Title"
        assert task.content == "Same Content"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_update_claimed_task_fails(self, db):
        """Test that updating a claimed task fails"""
        await db.team.create_team(
            team_name="team_claimed_update",
            display_name="Team Claimed Update",
            leader_member_name="leader_claimed_update"
        )
        await db.task.create_task(
            task_id="task_claimed",
            team_name="team_claimed_update",
            title="Claimed Task",
            content="Original content",
            status="claimed"
        )

        # Try to update a claimed task - should fail
        success = await db.task.update_task("task_claimed", title="New Title")
        assert success is False

        # Verify task was not changed
        task = await db.task.get_task("task_claimed")
        assert task.title == "Claimed Task"
        assert task.content == "Original content"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_claim_task(self, db):
        """Test claiming a task"""
        await db.team.create_team(
            team_name="team12",
            display_name="Team 12",
            leader_member_name="leader12"
        )
        await db.task.create_task(
            task_id="task3",
            team_name="team12",
            title="Task 3",
            content="Content",
            status="pending"
        )

        success = await db.task.claim_task("task3", "member7")
        assert success is True

        task = await db.task.get_task("task3")
        assert task.assignee == "member7"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_claim_task_not_found(self, db):
        """Test claiming non-existent task returns False"""
        success = await db.task.claim_task("nonexistenttask", "member7")
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_team_tasks(self, db):
        """Test getting all tasks of a team"""
        await db.team.create_team(
            team_name="team13",
            display_name="Team 13",
            leader_member_name="leader13"
        )
        await db.task.create_task(
            task_id="task4",
            team_name="team13",
            title="Task 4",
            content="Content 4",
            status="pending"
        )
        await db.task.create_task(
            task_id="task5",
            team_name="team13",
            title="Task 5",
            content="Content 5",
            status="completed"
        )

        tasks = await db.task.get_team_tasks("team13")
        assert len(tasks) == 2
        task_ids = [t.task_id for t in tasks]
        assert "task4" in task_ids
        assert "task5" in task_ids

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_team_tasks_with_status_filter(self, db):
        """Test getting tasks filtered by status"""
        await db.team.create_team(
            team_name="team14",
            display_name="Team 14",
            leader_member_name="leader14"
        )
        await db.task.create_task(
            task_id="task6",
            team_name="team14",
            title="Task 6",
            content="Content 6",
            status="pending"
        )
        await db.task.create_task(
            task_id="task7",
            team_name="team14",
            title="Task 7",
            content="Content 7",
            status="completed"
        )
        await db.task.create_task(
            task_id="task8",
            team_name="team14",
            title="Task 8",
            content="Content 8",
            status="pending"
        )

        pending_tasks = await db.task.get_team_tasks("team14", status="pending")
        assert len(pending_tasks) == 2

        completed_tasks = await db.task.get_team_tasks("team14", status="completed")
        assert len(completed_tasks) == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_task_duplicate_fails(self, db):
        """Test that creating duplicate task fails"""
        await db.team.create_team(
            team_name="team_err_2",
            display_name="Team Error 2",
            leader_member_name="leader_err_2"
        )
        await db.task.create_task(
            task_id="task_err_1",
            team_name="team_err_2",
            title="Task Error 1",
            content="Content",
            status="pending"
        )

        success = await db.task.create_task(
            task_id="task_err_1",
            team_name="team_err_2",
            title="Duplicate Task",
            content="Different content",
            status="blocked"
        )
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_concurrent_create_tasks_with_different_ids(self, db):
        """Test concurrently creating tasks with different task_ids all succeed."""
        await db.team.create_team(
            team_name="team_conc",
            display_name="Concurrent Team",
            leader_member_name="leader_conc"
        )

        task_ids = [f"conc_task_{i}" for i in range(5)]
        results = await asyncio.gather(*(
            db.task.create_task(
                task_id=tid,
                team_name="team_conc",
                title=f"Task {tid}",
                content=f"Content for {tid}",
                status="pending"
            )
            for tid in task_ids
        ))

        assert all(results), (
            f"Expected all concurrent creates to succeed, got {results}"
        )

        for tid in task_ids:
            task = await db.task.get_task(tid)
            assert task is not None
            assert task.task_id == tid

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_concurrent_create_tasks_with_file_db(self, tmp_path):
        """Test concurrently creating tasks with file-based SQLite.

        File-based SQLite uses multiple connections, exposing real
        concurrency issues that in-memory SQLite (single connection) hides.
        """
        db_path = tmp_path / "test_concurrent.db"
        config = DatabaseConfig(
            db_type=DatabaseType.SQLITE,
            connection_string=str(db_path)
        )
        token = set_session_id("session_id")
        db = TeamDatabase(config)
        await db.initialize()
        try:
            await db.team.create_team(
                team_name="team_file_conc",
            display_name="File Concurrent Team",
                leader_member_name="leader_file"
            )

            task_ids = [f"file_task_{i}" for i in range(5)]
            results = await asyncio.gather(*(
                db.task.create_task(
                    task_id=tid,
                    team_name="team_file_conc",
                    title=f"Task {tid}",
                    content=f"Content for {tid}",
                    status="pending"
                )
                for tid in task_ids
            ))

            assert all(results), (
                f"Expected all concurrent creates to succeed, got {results}"
            )

            for tid in task_ids:
                task = await db.task.get_task(tid)
                assert task is not None
                assert task.task_id == tid
        finally:
            reset_session_id(token)
            await db.close()


class TestTaskDependencyOperations:
    """Test task dependency operations"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mutate_dependency_graph_adds_single_edge(self, db):
        """Adding an edge through mutate_dependency_graph wires it into
        the dependency table; an edge to a COMPLETED target is born
        resolved so the source task's PENDING status survives refresh."""
        await db.team.create_team(
            team_name="team15",
            display_name="Team 15",
            leader_member_name="leader15"
        )
        await db.task.create_task(
            task_id="task9",
            team_name="team15",
            title="Task 9",
            content="Content 9",
            status="pending"
        )
        await db.task.create_task(
            task_id="task10",
            team_name="team15",
            title="Task 10",
            content="Content 10",
            status="completed"
        )

        result = await db.task.mutate_dependency_graph(
            team_name="team15",
            add_edges=[("task9", "task10")],
        )
        assert result.ok is True

        dependencies = await db.task.get_task_dependencies("task9")
        assert len(dependencies) == 1
        assert dependencies[0].depends_on_task_id == "task10"
        assert dependencies[0].resolved is True
        task9 = await db.task.get_task("task9")
        assert task9.status == "pending"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_task_dependencies_empty(self, db):
        """Test getting dependencies for task with no dependencies"""
        await db.team.create_team(
            team_name="team16",
            display_name="Team 16",
            leader_member_name="leader16"
        )
        await db.task.create_task(
            task_id="task11",
            team_name="team16",
            title="Task 11",
            content="Content 11",
            status="pending"
        )

        dependencies = await db.task.get_task_dependencies("task11")
        assert dependencies == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_task_dependencies_multiple(self, db):
        """Test getting multiple dependencies"""
        await db.team.create_team(
            team_name="team17",
            display_name="Team 17",
            leader_member_name="leader17"
        )
        await db.task.create_task(
            task_id="task12",
            team_name="team17",
            title="Task 12",
            content="Content 12",
            status="blocked"
        )
        await db.task.create_task(
            task_id="task13",
            team_name="team17",
            title="Task 13",
            content="Content 13",
            status="completed"
        )
        await db.task.create_task(
            task_id="task14",
            team_name="team17",
            title="Task 14",
            content="Content 14",
            status="completed"
        )

        await db.task.mutate_dependency_graph("team17", add_edges=[("task12", "task13")])
        await db.task.mutate_dependency_graph("team17", add_edges=[("task12", "task14")])

        dependencies = await db.task.get_task_dependencies("task12")
        assert len(dependencies) == 2
        dep_ids = [d.depends_on_task_id for d in dependencies]
        assert "task13" in dep_ids
        assert "task14" in dep_ids

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_dependents_only(self, db):
        """Test creating a task that existing tasks depend on (high priority)"""
        await db.team.create_team(
            team_name="team_bidir1",
            display_name="Team Bidir1",
            leader_member_name="leader1"
        )

        # Create pending tasks that will depend on new task
        await db.task.create_task("task1", "team_bidir1", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_bidir1", "Task 2", "Content 2", "pending")

        # Create a high priority task that task1 and task2 will depend on
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="priority_task",
            team_name="team_bidir1",
            title="Priority Task",
            content="High priority content",
            status="pending",
            dependencies=None,
            dependent_task_ids=["task1", "task2"]
        )
        assert success is True

        # Verify: priority task was created
        priority_task = await db.task.get_task("priority_task")
        assert priority_task is not None
        assert priority_task.status == "pending"

        # Verify: dependents now depend on priority task
        deps1 = await db.task.get_task_dependencies("task1")
        assert len(deps1) == 1
        assert deps1[0].depends_on_task_id == "priority_task"

        deps2 = await db.task.get_task_dependencies("task2")
        assert len(deps2) == 1
        assert deps2[0].depends_on_task_id == "priority_task"

        # Verify: dependents were changed from pending to blocked
        task1_updated = await db.task.get_task("task1")
        task2_updated = await db.task.get_task("task2")
        assert task1_updated.status == "blocked"
        assert task2_updated.status == "blocked"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_dependencies_only(self, db):
        """Test creating a task that depends on existing tasks"""
        await db.team.create_team(
            team_name="team_bidir2",
            display_name="Team Bidir2",
            leader_member_name="leader2"
        )

        # Create completed tasks that new task will depend on
        await db.task.create_task("task1", "team_bidir2", "Task 1", "Content 1", "completed")
        await db.task.create_task("task2", "team_bidir2", "Task 2", "Content 2", "completed")

        # Create a new task that depends on task1 and task2
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="new_task",
            team_name="team_bidir2",
            title="New Task",
            content="New task content",
            status="blocked",
            dependencies=["task1", "task2"],
            dependent_task_ids=None
        )
        assert success is True

        # Verify: new task was created. Both dependencies are already
        # COMPLETED, so the edges are born resolved and the post-mutation
        # refresh pass flips the seed BLOCKED status to PENDING — there
        # is nothing for the new task to wait on.
        new_task = await db.task.get_task("new_task")
        assert new_task is not None
        assert new_task.status == "pending"

        # Verify: new task depends on task1 and task2
        deps = await db.task.get_task_dependencies("new_task")
        assert len(deps) == 2
        dep_ids = [d.depends_on_task_id for d in deps]
        assert "task1" in dep_ids
        assert "task2" in dep_ids
        assert all(d.resolved for d in deps)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_insert_between(self, db):
        """Test inserting a task between other tasks in dependency chain"""
        await db.team.create_team(
            team_name="team_bidir3",
            display_name="Team Bidir3",
            leader_member_name="leader3"
        )

        # Setup: taskA -> taskB
        await db.task.create_task("taskA", "team_bidir3", "Task A", "Content A",
                             "completed")
        await db.task.create_task("taskB", "team_bidir3", "Task B", "Content B", "pending")
        await db.task.mutate_dependency_graph("team_bidir3", add_edges=[("taskB", "taskA")])

        # Insert taskM such that: taskA -> taskM -> taskB
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="taskM",
            team_name="team_bidir3",
            title="Task M (Middle)",
            content="Middle task content",
            status="blocked",  # Depends on taskA
            dependencies=["taskA"],
            dependent_task_ids=["taskB"]
        )
        assert success is True

        # Verify: taskM was created. taskA is COMPLETED so the edge
        # (taskM -> taskA) is born resolved; refresh flips taskM from
        # the seed BLOCKED to PENDING (nothing to wait on).
        task_m = await db.task.get_task("taskM")
        assert task_m is not None
        assert task_m.status == "pending"

        # Verify: taskM depends on taskA
        deps_m = await db.task.get_task_dependencies("taskM")
        assert len(deps_m) == 1
        assert deps_m[0].depends_on_task_id == "taskA"

        # Verify: taskB now depends on taskM
        deps_b = await db.task.get_task_dependencies("taskB")
        assert len(deps_b) == 2  # Both old dependency (taskA) and new (taskM)
        dep_ids = [d.depends_on_task_id for d in deps_b]
        assert "taskM" in dep_ids

        # Verify: taskB transitioned to BLOCKED because the (taskB -> taskM)
        # edge is unresolved (taskM is PENDING, not terminal).
        task_b = await db.task.get_task("taskB")
        assert task_b.status == "blocked"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_circular_dependency_detection(self, db):
        """Test circular dependency detection:
           Existing: taskA -> taskB
           Try to add: taskB depends on TaskC, and taskC depends on taskA
           Result would be: taskA -> taskB -> taskC -> taskA (cycle)
        """
        await db.team.create_team(
            team_name="team_cycle",
            display_name="Team Cycle",
            leader_member_name="leader4"
        )

        # Setup existing dependency: taskA -> taskB
        await db.task.create_task("taskA", "team_cycle", "Task A", "Content A", "pending")
        await db.task.create_task("taskB", "team_cycle", "Task B", "Content B", "completed")
        await db.task.mutate_dependency_graph("team_cycle", add_edges=[("taskA", "taskB")])

        # Try to add taskC that would create a cycle
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="taskC",
            team_name="team_cycle",
            title="Task C",
            content="Content C",
            status="blocked",
            dependencies=["taskA"],
            dependent_task_ids=["taskB"]
        )
        # Should fail due to circular dependency
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_bidirectional_no_dependencies(self, db):
        """Test creating a task with no dependencies or dependents"""
        await db.team.create_team(
            team_name="team_no_deps",
            display_name="Team NoDeps",
            leader_member_name="leader5"
        )

        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="task_no_deps",
            team_name="team_no_deps",
            title="No Deps Task",
            content="Content",
            status="pending",
            dependencies=None,
            dependent_task_ids=None
        )
        assert success is True

        task = await db.task.get_task("task_no_deps")
        assert task is not None
        assert task.status == "pending"

        deps = await db.task.get_task_dependencies("task_no_deps")
        assert len(deps) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_completed_dependent_fails(self, db):
        """Test that adding a dependency to a completed task fails"""
        await db.team.create_team(
            team_name="team_terminal1",
            display_name="Team Terminal1",
            leader_member_name="leader6"
        )

        # Create a completed task
        await db.task.create_task("task_completed", "team_terminal1", "Task Completed",
                             "Content", "completed")

        # Try to create a new task that the completed task would depend on
        # This should fail because completed is a terminal status
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="new_priority",
            team_name="team_terminal1",
            title="New Priority Task",
            content="Content",
            status="pending",
            dependencies=None,
            dependent_task_ids=["task_completed"]
        )
        assert success is False

        # Verify the new task was not created
        new_task = await db.task.get_task("new_priority")
        assert new_task is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_cancelled_dependent_fails(self, db):
        """Test that adding a dependency to a cancelled task fails"""
        await db.team.create_team(
            team_name="team_terminal2",
            display_name="Team Terminal2",
            leader_member_name="leader7"
        )

        # Create a cancelled task
        await db.task.create_task("task_cancelled", "team_terminal2", "Task Cancelled",
                             "Content", "cancelled")

        # Try to create a new task that the cancelled task would depend on
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="new_priority2",
            team_name="team_terminal2",
            title="New Priority Task 2",
            content="Content",
            status="pending",
            dependencies=None,
            dependent_task_ids=["task_cancelled"]
        )
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_claimed_dependent_fails(self, db):
        """Test that adding a dependency to a claimed task fails (claimed -> blocked is invalid)"""
        await db.team.create_team(
            team_name="team_terminal3",
            display_name="Team Terminal3",
            leader_member_name="leader8"
        )

        # Create a claimed task
        await db.task.create_task("task_claimed", "team_terminal3", "Task Claimed",
                             "Content", "claimed")

        # Create a new task that the claimed task would depend on
        # This should succeed because CLAIMED -> BLOCKED is a valid state transition
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="new_priority3",
            team_name="team_terminal3",
            title="New Priority Task 3",
            content="Content",
            status="pending",
            dependencies=None,
            dependent_task_ids=["task_claimed"]
        )
        assert success is False

        # Verify: claimed task status is not changed
        claimed_task = await db.task.get_task("task_claimed")
        assert (claimed_task.status == "claimed")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_task_with_nonexistent_dependent_fails(self, db):
        """Test that adding a dependency to a non-existent task fails"""
        await db.team.create_team(
            team_name="team_terminal4",
            display_name="Team Terminal4",
            leader_member_name="leader9"
        )

        # Try to create a task that depends on a non-existent task
        success = await db.task.add_task_with_bidirectional_dependencies(
            task_id="new_task",
            team_name="team_terminal4",
            title="New Task",
            content="Content",
            status="pending",
            dependencies=["nonexistent_task"],
            dependent_task_ids=None
        )
        assert success is False


class TestMessageOperations:
    """Test message CRUD operations"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_message_point_to_point(self, db):
        """Test creating point-to-point message"""
        await db.team.create_team(
            team_name="team18",
            display_name="Team 18",
            leader_member_name="leader18"
        )

        success = await db.message.create_message(
            message_id="msg1",
            team_name="team18",
            from_member_name="member8",
            to_member_name="member9",
            content="Hello",
            broadcast=False
        )
        assert success is True

        message = await db.message.get_message("msg1")
        assert message is not None
        assert message.message_id == "msg1"
        assert message.from_member_name == "member8"
        assert message.to_member_name == "member9"
        assert message.content == "Hello"
        assert message.broadcast == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_message_broadcast(self, db):
        """Test creating broadcast message"""
        await db.team.create_team(
            team_name="team19",
            display_name="Team 19",
            leader_member_name="leader19"
        )

        success = await db.message.create_message(
            message_id="msg2",
            team_name="team19",
            from_member_name="member10",
            to_member_name=None,
            content="Broadcast message",
            broadcast=True
        )
        assert success is True

        message = await db.message.get_message("msg2")
        assert message is not None
        assert message.to_member_name is None
        assert message.broadcast == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_message_not_found(self, db):
        """Test getting non-existent message returns None"""
        message = await db.message.get_message("nonexistentmsg")
        assert message is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_team_messages(self, db):
        """Test getting all messages of a team"""
        await db.team.create_team(
            team_name="team20",
            display_name="Team 20",
            leader_member_name="leader20"
        )
        await db.message.create_message(
            message_id="msg3",
            team_name="team20",
            from_member_name="member11",
            to_member_name="member12",
            content="Message 3",
            broadcast=False
        )
        await db.message.create_message(
            message_id="msg4",
            team_name="team20",
            from_member_name="member13",
            to_member_name=None,
            content="Message 4",
            broadcast=True
        )

        messages = await db.message.get_team_messages(team_name="team20")
        assert len(messages) == 2
        message_ids = [m.message_id for m in messages]
        assert "msg3" in message_ids
        assert "msg4" in message_ids

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_messages_for_member(self, db):
        """Test getting messages for a specific member"""
        await db.team.create_team(
            team_name="team21",
            display_name="Team 21",
            leader_member_name="leader21"
        )
        await db.message.create_message(
            message_id="msg5",
            team_name="team21",
            from_member_name="member14",
            to_member_name="member15",
            content="For member15",
            broadcast=False
        )
        await db.message.create_message(
            message_id="msg6",
            team_name="team21",
            from_member_name="member16",
            to_member_name="member15",
            content="Also for member15",
            broadcast=False
        )
        await db.message.create_message(
            message_id="msg7",
            team_name="team21",
            from_member_name="member17",
            to_member_name="member18",
            content="For member18",
            broadcast=False
        )

        messages = await db.message.get_messages(team_name="team21", to_member_name="member15")
        assert len(messages) == 2
        message_ids = [m.message_id for m in messages]
        assert "msg5" in message_ids
        assert "msg6" in message_ids
        assert "msg7" not in message_ids

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_message_read(self, db):
        """Test marking message as read"""
        await db.team.create_team(
            team_name="team22",
            display_name="Team 22",
            leader_member_name="leader22"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member20",
            team_name="team22",
            display_name="Member Six",
            agent_card=agent_card,
            status="busy"
        )
        await db.message.create_message(
            message_id="msg8",
            team_name="team22",
            from_member_name="member19",
            to_member_name="member20",
            content="Test message",
            broadcast=False
        )

        # Initially unread
        messages = await db.message.get_messages(team_name="team22", to_member_name="member20")
        assert len(messages) == 1
        assert messages[0].is_read is False

        success = await db.message.mark_message_read("msg8", "member20")
        assert success is True

        # Now should be read
        messages = await db.message.get_messages(team_name="team22", to_member_name="member20")
        assert len(messages) == 1
        assert messages[0].is_read is True

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_message_duplicate_fails(self, db):
        """Test that creating duplicate message fails"""
        await db.team.create_team(
            team_name="team_err_3",
            display_name="Team Error 3",
            leader_member_name="leader_err_3"
        )
        await db.message.create_message(
            message_id="msg_err_1",
            team_name="team_err_3",
            from_member_name="member_err_1",
            to_member_name="member_err_2",
            content="First message",
            broadcast=False
        )

        success = await db.message.create_message(
            message_id="msg_err_1",
            team_name="team_err_3",
            from_member_name="member_err_3",
            to_member_name="member_err_4",
            content="Duplicate message",
            broadcast=False
        )
        assert success is False

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_mark_message_read_not_found(self, db):
        """Test marking non-existent message as read returns False"""
        success = await db.message.mark_message_read("nonexistent_msg", "member1")
        assert success is False


class TestCascadeDelete:
    """Test cascade delete functionality"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_cascades_to_members(self, db):
        """Test that deleting team cascades to members"""
        await db.team.create_team(
            team_name="team23",
            display_name="Team 23",
            leader_member_name="leader23"
        )
        agent_card = AgentCard(name="TestAgent").model_dump_json()
        await db.member.create_member(
            member_name="member21",
            team_name="team23",
            display_name="Member 21",
            agent_card=agent_card,
            status="ready"
        )

        await db.team.delete_team("team23")

        member = await db.member.get_member("member21", "team23")
        assert member is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_cascades_to_tasks(self, db):
        """Test that deleting team cascades to tasks"""
        await db.team.create_team(
            team_name="team24",
            display_name="Team 24",
            leader_member_name="leader24"
        )
        await db.task.create_task(
            task_id="task15",
            team_name="team24",
            title="Task 15",
            content="Content 15",
            status="pending"
        )

        await db.team.delete_team("team24")

        task = await db.task.get_task("task15")
        assert task is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_cascades_to_messages(self, db):
        """Test that deleting team cascades to messages"""
        await db.team.create_team(
            team_name="team25",
            display_name="Team  25",
            leader_member_name="leader25"
        )
        await db.message.create_message(
            message_id="msg9",
            team_name="team25",
            from_member_name="member22",
            to_member_name="member23",
            content="Test message",
            broadcast=False
        )

        await db.team.delete_team("team25")

        message = await db.message.get_message("msg9")
        assert message is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_cascades_to_dependencies(self, db):
        """Test that deleting team cascades to task dependencies"""
        await db.team.create_team(
            team_name="team26",
            display_name="Team 26",
            leader_member_name="leader26"
        )
        await db.task.create_task(
            task_id="task16",
            team_name="team26",
            title="Task 16",
            content="Content 16",
            status="blocked"
        )
        await db.task.create_task(
            task_id="task17",
            team_name="team26",
            title="Task 17",
            content="Content 17",
            status="completed"
        )
        await db.task.mutate_dependency_graph("team26", add_edges=[("task16", "task17")])

        await db.team.delete_team("team26")

        # Task and its dependencies should be gone
        task = await db.task.get_task("task16")
        assert task is None


class TestVerifyAndFixTaskConsistency:
    """Test verify_and_fix_task_consistency for data consistency recovery"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_empty_team(self, db):
        """Test fixing consistency when team has no tasks"""
        await db.team.create_team(
            team_name="team27",
            display_name="Team 27",
            leader_member_name="leader27"
        )

        fixed = await db.task.verify_and_fix_task_consistency("team27")
        assert fixed == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_no_blocked_tasks(self, db):
        """Test when all tasks are pending (nothing to fix)"""
        await db.team.create_team(
            team_name="team28",
            display_name="Team 28",
            leader_member_name="leader28"
        )
        await db.task.create_task(
            task_id="task18",
            team_name="team28",
            title="Task 18",
            content="Content 18",
            status="pending"
        )
        await db.task.create_task(
            task_id="task19",
            team_name="team28",
            title="Task 19",
            content="Content 19",
            status="pending"
        )

        fixed = await db.task.verify_and_fix_task_consistency("team28")
        assert fixed == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_blocked_nothing_to_fix(self, db):
        """Test when blocked task's dependency is not complete (nothing to fix)"""
        await db.team.create_team(
            team_name="team29",
            display_name="Team 29",
            leader_member_name="leader29"
        )
        await db.task.create_task(
            task_id="task20",
            team_name="team29",
            title="Task 20",
            content="Content 20",
            status="pending"
        )
        await db.task.create_task(
            task_id="task21",
            team_name="team29",
            title="Task 21",
            content="Content 21",
            status="blocked"
        )
        await db.task.mutate_dependency_graph("team29", add_edges=[("task21", "task20")])

        # task21 is BLOCKED because task20 is not complete, nothing to fix
        fixed = await db.task.verify_and_fix_task_consistency("team29")
        assert fixed == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_single_blocked_task(self, db):
        """Recovery sweep flips a BLOCKED task whose deps are all resolved.

        Drift is fabricated by inserting an already-resolved edge to a
        BLOCKED task without going through the unified mutation path —
        the kind of state a crash between completing a dep and refreshing
        downstream could leave behind.
        """
        from openjiuwen.agent_teams.tools.models import _get_task_dependency_model
        await db.team.create_team(
            team_name="team30",
            display_name="Team 30",
            leader_member_name="leader30"
        )
        await db.task.create_task(
            task_id="task22",
            team_name="team30",
            title="Task 22",
            content="Content 22",
            status="completed"
        )
        await db.task.create_task(
            task_id="task23",
            team_name="team30",
            title="Task 23",
            content="Content 23",
            status="blocked"
        )
        # Hand-write the drifted edge: resolved=True against a BLOCKED task.
        async with db.session_local() as session:
            task_dependency_model = _get_task_dependency_model()
            session.add(task_dependency_model(
                task_id="task23",
                depends_on_task_id="task22",
                team_name="team30",
                resolved=True,
            ))
            await session.commit()

        fixed = await db.task.verify_and_fix_task_consistency("team30")
        assert len(fixed) == 1
        assert fixed[0].task_id == "task23"
        assert fixed[0].status == "pending"

        task = await db.task.get_task("task23")
        assert task.status == "pending"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_multiple_blocked_tasks(self, db):
        """Recovery sweep handles multiple drifted BLOCKED tasks at once."""
        from openjiuwen.agent_teams.tools.models import _get_task_dependency_model
        await db.team.create_team(
            team_name="team31",
            display_name="Team 31",
            leader_member_name="leader31"
        )
        await db.task.create_task(
            task_id="task24",
            team_name="team31",
            title="Task 24",
            content="Content 24",
            status="completed"
        )
        await db.task.create_task(
            task_id="task25",
            team_name="team31",
            title="Task 25",
            content="Content 25",
            status="blocked"
        )
        await db.task.create_task(
            task_id="task26",
            team_name="team31",
            title="Task 26",
            content="Content 26",
            status="blocked"
        )
        async with db.session_local() as session:
            task_dependency_model = _get_task_dependency_model()
            for src in ("task25", "task26"):
                session.add(task_dependency_model(
                    task_id=src,
                    depends_on_task_id="task24",
                    team_name="team31",
                    resolved=True,
                ))
            await session.commit()

        fixed = await db.task.verify_and_fix_task_consistency("team31")
        assert len(fixed) == 2
        fixed_ids = [t.task_id for t in fixed]
        assert "task25" in fixed_ids
        assert "task26" in fixed_ids

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_verify_and_fix_partial_dependencies(self, db):
        """Test that tasks only get fixed when ALL dependencies are completed"""
        await db.team.create_team(
            team_name="team32",
            display_name="Team 32",
            leader_member_name="leader32"
        )
        await db.task.create_task(
            task_id="task27",
            team_name="team32",
            title="Task 27",
            content="Content 27",
            status="completed"
        )
        await db.task.create_task(
            task_id="task28",
            team_name="team32",
            title="Task 28",
            content="Content 28",
            status="pending"
        )
        await db.task.create_task(
            task_id="task29",
            team_name="team32",
            title="Task 29",
            content="Content 29",
            status="blocked"
        )
        await db.task.mutate_dependency_graph("team32", add_edges=[("task29", "task27")])
        await db.task.mutate_dependency_graph("team32", add_edges=[("task29", "task28")])

        # Only resolve first dependency (task27 -> task29)
        async with db.session_local() as session:
            from sqlalchemy import update

            from openjiuwen.agent_teams.tools.models import _get_task_dependency_model
            task_dependency_model = _get_task_dependency_model()
            await session.execute(
                update(task_dependency_model).where(
                    task_dependency_model.task_id == "task29",
                    task_dependency_model.depends_on_task_id == "task27"
                ).values(resolved=True)
            )
            await session.commit()

        # task29 should still be BLOCKED (task28 not complete)
        fixed = await db.task.verify_and_fix_task_consistency("team32")
        assert fixed == []

        task = await db.task.get_task("task29")
        assert task.status == "blocked"

        # Now resolve second dependency
        async with db.session_local() as session:
            await session.execute(
                update(task_dependency_model).where(
                    task_dependency_model.task_id == "task29",
                    task_dependency_model.depends_on_task_id == "task28"
                ).values(resolved=True)
            )
            await session.commit()

        # Now task29 should be fixed
        fixed = await db.task.verify_and_fix_task_consistency("team32")
        assert len(fixed) == 1
        assert fixed[0].task_id == "task29"
        assert fixed[0].status == "pending"


class TestCancelAllTasks:
    """Test cancel_all_tasks functionality"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_pending_tasks(self, db):
        """Test cancelling all pending tasks"""
        await db.team.create_team(
            team_name="team_cancel_all",
            display_name="Cancel All Team",
            leader_member_name="leader1"
        )

        # Create multiple pending tasks
        await db.task.create_task("task1", "team_cancel_all", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_cancel_all", "Task 2", "Content 2", "pending")
        await db.task.create_task("task3", "team_cancel_all", "Task 3", "Content 3", "pending")

        # Cancel all tasks
        cancelled_tasks = (await db.task.cancel_all_tasks("team_cancel_all"))["cancelled_tasks"]

        assert len(cancelled_tasks) == 3

        # Verify all tasks are cancelled
        task1 = await db.task.get_task("task1")
        task2 = await db.task.get_task("task2")
        task3 = await db.task.get_task("task3")
        assert task1.status == "cancelled"
        assert task2.status == "cancelled"
        assert task3.status == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_mixed_status_tasks(self, db):
        """Test cancelling tasks with mixed statuses"""
        await db.team.create_team(
            team_name="team_mixed_cancel",
            display_name="Mixed Cancel Team",
            leader_member_name="leader1"
        )

        # Create tasks with different statuses
        await db.task.create_task("task1", "team_mixed_cancel", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_mixed_cancel", "Task 2", "Content 2", "claimed")
        await db.task.create_task("task3", "team_mixed_cancel", "Task 3", "Content 3", "blocked")
        await db.task.create_task("task4", "team_mixed_cancel", "Task 4", "Content 4", "cancelled")
        await db.task.create_task("task5", "team_mixed_cancel", "Task 5", "Content 5", "completed")

        # Cancel all tasks
        cancelled_tasks = (await db.task.cancel_all_tasks("team_mixed_cancel"))["cancelled_tasks"]

        assert len(cancelled_tasks) == 3  # Only pending, claimed, blocked tasks

        # Verify correct tasks were cancelled
        task1 = await db.task.get_task("task1")
        task2 = await db.task.get_task("task2")
        task3 = await db.task.get_task("task3")
        task4 = await db.task.get_task("task4")
        task5 = await db.task.get_task("task5")

        assert task1.status == "cancelled"
        assert task2.status == "cancelled"
        assert task3.status == "cancelled"
        assert task4.status == "cancelled"  # Already cancelled, stays cancelled
        assert task5.status == "completed"  # Stays completed

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_no_active_tasks(self, db):
        """Test cancelling when there are no active tasks"""
        await db.team.create_team(
            team_name="team_no_active",
            display_name="No Active Team",
            leader_member_name="leader1"
        )

        # Only have cancelled and completed tasks
        await db.task.create_task("task1", "team_no_active", "Task 1", "Content 1", "cancelled")
        await db.task.create_task("task2", "team_no_active", "Task 2", "Content 2", "completed")

        # Cancel all tasks
        cancelled_tasks = (await db.task.cancel_all_tasks("team_no_active"))["cancelled_tasks"]

        assert len(cancelled_tasks) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_empty_team(self, db):
        """Test cancelling tasks for team with no tasks"""
        await db.team.create_team(
            team_name="team_empty",
            display_name="Empty Team",
            leader_member_name="leader1"
        )

        # Cancel all tasks
        cancelled_tasks = (await db.task.cancel_all_tasks("team_empty"))["cancelled_tasks"]

        assert len(cancelled_tasks) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_tasks_atomic(self, db):
        """Test that cancel_all_tasks is atomic (single transaction)"""
        await db.team.create_team(
            team_name="team_atomic",
            display_name="Atomic Team",
            leader_member_name="leader1"
        )

        # Create many tasks
        for i in range(10):
            await db.task.create_task(f"task{i}", "team_atomic", f"Task {i}", f"Content {i}", "pending")

        # Cancel all in one call - should be atomic
        cancelled_tasks = (await db.task.cancel_all_tasks("team_atomic"))["cancelled_tasks"]

        assert len(cancelled_tasks) == 10

        # Verify all were cancelled
        for i in range(10):
            task = await db.task.get_task(f"task{i}")
            assert task.status == "cancelled"


class TestResetTask:
    """Test reset_task functionality"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_claimed_task(self, db):
        """Test resetting a claimed task back to pending"""
        await db.team.create_team(
            team_name="team_reset",
            display_name="Reset Team",
            leader_member_name="leader1"
        )
        await db.task.create_task(
            task_id="task_reset",
            team_name="team_reset",
            title="Reset Task",
            content="Content",
            status="pending"
        )
        # Claim task to set assignee (this sets status to claimed)
        await db.task.claim_task("task_reset", "member1")

        task_before = await db.task.get_task("task_reset")
        assert task_before.status == "claimed"
        assert task_before.assignee == "member1"

        # Reset task
        result = await db.task.reset_task("task_reset")
        assert result is not None
        assert result.task_id == "task_reset"
        assert result.status == "pending"
        assert result.assignee is None

        # Verify in database
        task_after = await db.task.get_task("task_reset")
        assert task_after.status == "pending"
        assert task_after.assignee is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_nonexistent_task(self, db):
        """Test resetting non-existent task returns None"""
        result = await db.task.reset_task("nonexistent-task-id")
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_pending_task_fails(self, db):
        """Test resetting a pending task fails (invalid state transition)"""
        await db.team.create_team(
            team_name="team_reset_pending",
            display_name="Reset Pending Team",
            leader_member_name="leader1"
        )
        await db.task.create_task(
            task_id="task_pending",
            team_name="team_reset_pending",
            title="Pending Task",
            content="Content",
            status="pending"
        )

        result = await db.task.reset_task("task_pending")
        assert result is None

        task = await db.task.get_task("task_pending")
        assert task.status == "pending"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_completed_task_fails(self, db):
        """Test resetting a completed task fails (invalid state transition)"""
        await db.team.create_team(
            team_name="team_reset_completed",
            display_name="Reset Completed Team",
            leader_member_name="leader1"
        )
        await db.task.create_task(
            task_id="task_completed",
            team_name="team_reset_completed",
            title="Completed Task",
            content="Content",
            status="completed"
        )

        result = await db.task.reset_task("task_completed")
        assert result is None

        task = await db.task.get_task("task_completed")
        assert task.status == "completed"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_cancelled_task_fails(self, db):
        """Test resetting a cancelled task fails (invalid state transition)"""
        await db.team.create_team(
            team_name="team_reset_cancelled",
            display_name="Reset Cancelled Team",
            leader_member_name="leader1"
        )
        await db.task.create_task(
            task_id="task_cancelled",
            team_name="team_reset_cancelled",
            title="Cancelled Task",
            content="Content",
            status="cancelled"
        )

        result = await db.task.reset_task("task_cancelled")
        assert result is None

        task = await db.task.get_task("task_cancelled")
        assert task.status == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_reset_blocked_task_fails(self, db):
        """Test resetting a blocked task fails (invalid state transition)"""
        await db.team.create_team(
            team_name="team_reset_blocked",
            display_name="Reset Blocked Team",
            leader_member_name="leader1"
        )
        await db.task.create_task(
            task_id="task_blocked",
            team_name="team_reset_blocked",
            title="Blocked Task",
            content="Content",
            status="blocked"
        )

        result = await db.task.reset_task("task_blocked")
        assert result is None

        task = await db.task.get_task("task_blocked")
        assert task.status == "blocked"


class TestGetTasksByAssignee:
    """Test get_tasks_by_assignee functionality"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_tasks_by_assignee_empty(self, db):
        """Test getting tasks by assignee when none exist"""
        await db.team.create_team(
            team_name="team_assignee_empty",
            display_name="Assignee Empty Team",
            leader_member_name="leader1"
        )

        tasks = await db.task.get_tasks_by_assignee("team_assignee_empty", "member1")
        assert tasks == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_tasks_by_assignee_with_tasks(self, db):
        """Test getting tasks assigned to a specific member"""
        await db.team.create_team(
            team_name="team_assignee",
            display_name="Assignee Team",
            leader_member_name="leader1"
        )
        await db.task.create_task("task1", "team_assignee", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_assignee", "Task 2", "Content 2", "pending")
        await db.task.claim_task("task1", "member1")
        await db.task.claim_task("task2", "member1")

        tasks = await db.task.get_tasks_by_assignee("team_assignee", "member1")
        assert len(tasks) == 2
        task_ids = [t.task_id for t in tasks]
        assert "task1" in task_ids
        assert "task2" in task_ids
        assert all(t.assignee == "member1" for t in tasks)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_tasks_by_assignee_with_status_filter(self, db):
        """Test getting tasks by assignee with status filter"""
        await db.team.create_team(
            team_name="team_assignee_filter",
            display_name="Assignee Filter Team",
            leader_member_name="leader1"
        )
        await db.task.create_task("task1", "team_assignee_filter", "Task 1", "Content 1", "pending")
        await db.task.claim_task("task1", "member1")
        # Reset task1 to pending
        await db.task.reset_task("task1")

        await db.task.create_task("task2", "team_assignee_filter", "Task 2", "Content 2", "pending")
        await db.task.claim_task("task2", "member1")

        # Get claimed tasks
        claimed_tasks = await db.task.get_tasks_by_assignee("team_assignee_filter", "member1", "claimed")
        assert len(claimed_tasks) == 1
        assert claimed_tasks[0].task_id == "task2"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_tasks_by_assignee_different_members(self, db):
        """Test that tasks are correctly filtered by assignee"""
        await db.team.create_team(
            team_name="team_assignee_multi",
            display_name="Assignee Multi Team",
            leader_member_name="leader1"
        )
        await db.task.create_task("task1", "team_assignee_multi", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_assignee_multi", "Task 2", "Content 2", "pending")
        await db.task.claim_task("task1", "member1")
        await db.task.claim_task("task2", "member2")

        tasks_member1 = await db.task.get_tasks_by_assignee("team_assignee_multi", "member1")
        assert len(tasks_member1) == 1
        assert tasks_member1[0].task_id == "task1"

        tasks_member2 = await db.task.get_tasks_by_assignee("team_assignee_multi", "member2")
        assert len(tasks_member2) == 1
        assert tasks_member2[0].task_id == "task2"

        tasks_member3 = await db.task.get_tasks_by_assignee("team_assignee_multi", "member3")
        assert len(tasks_member3) == 0

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_get_tasks_by_assignee_excludes_unclaimed(self, db):
        """Test that unclaimed tasks are not returned"""
        await db.team.create_team(
            team_name="team_assignee_unclaimed",
            display_name="Assignee Unclaimed Team",
            leader_member_name="leader1"
        )
        await db.task.create_task("task1", "team_assignee_unclaimed", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "team_assignee_unclaimed", "Task 2", "Content 2", "pending")
        await db.task.claim_task("task2", "member1")

        tasks = await db.task.get_tasks_by_assignee("team_assignee_unclaimed", "member1")
        assert len(tasks) == 1
        assert tasks[0].task_id == "task2"
        assert tasks[0].assignee == "member1"


class TestSessionTables:
    """Test session-specific table creation and deletion"""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_cur_session_tables_success(self, db):
        """Test that create_cur_session_tables creates dynamic tables"""
        await db.team.create_team(
            "team_session",
            "Session Team",
            "leader1"
        )
        await db.task.create_task(
            "task1",
            "team_session",
            "Task 1",
            "Content 1",
            "pending"
        )

        task = await db.task.get_task("task1")
        assert task is not None
        assert task.task_id == "task1"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_drop_cur_session_tables_success(self, db_config):
        """Test that drop_cur_session_tables removes dynamic tables"""
        token = set_session_id("test_drop_session")
        database = TeamDatabase(db_config)
        try:
            await database.initialize()

            await database.team.create_team(
                "team_drop",
                "Drop Team",
                "leader1"
            )
            await database.task.create_task(
                "task1",
                "team_drop",
                "Task 1",
                "Content 1",
                "pending"
            )

            task = await database.task.get_task("task1")
            assert task is not None

            await database.drop_cur_session_tables()

            try:
                task_after = await database.task.get_task("task1")
                assert False
            except Exception as e:
                assert "no such table" in str(e).lower()

        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_and_drop_symmetry(self, db_config):
        """Test that create and drop are symmetric operations"""
        token = set_session_id("test_symmetry_session")
        database = TeamDatabase(db_config)
        try:
            await database.initialize()
            await database.drop_cur_session_tables()
            await database.create_cur_session_tables()

            await database.team.create_team(
                "team_sym",
                "Sym Team",
                "leader1"
            )
            await database.task.create_task(
                "task1",
                "team_sym",
                "Task 1",
                "Content 1",
                "pending"
            )

            task = await database.task.get_task("task1")
            assert task is not None

        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_multiple_sessions_isolated(self, db_config):
        """Test that different sessions have isolated tables"""
        token = set_session_id("session_1")
        database = TeamDatabase(db_config)
        await database.initialize()

        await database.team.create_team(
            "team1",
            "Team 1",
            "leader1"
        )
        await database.task.create_task(
            "task1",
            "team1",
            "Task 1",
            "Content 1",
            "pending"
        )

        task1 = await database.task.get_task("task1")
        assert task1 is not None
        assert task1.task_id == "task1"

        reset_session_id(token)
        token = set_session_id("session_2")
        database2 = TeamDatabase(db_config)
        await database2.initialize()

        await database2.team.create_team(
            "team2",
            "Team 2",
            "leader2"
        )
        await database2.task.create_task(
            "task2",
            "team2",
            "Task 2",
            "Content 2",
            "pending"
        )

        task2 = await database2.task.get_task("task2")
        assert task2 is not None
        assert task2.task_id == "task2"

        task1_in_session2 = await database2.task.get_task("task1")
        assert task1_in_session2 is None

        reset_session_id(token)
        await database2.close()
        await database.close()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_tables_idempotent(self, db):
        """Test that create_cur_session_tables is idempotent"""
        await db.create_cur_session_tables()
        await db.create_cur_session_tables()

        await db.team.create_team(
            "team_idem",
            "Idem Team",
            "leader1"
        )
        await db.task.create_task(
            "task1",
            "team_idem",
            "Task 1",
            "Content 1",
            "pending"
        )

        task = await db.task.get_task("task1")
        assert task is not None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_drop_tables_idempotent(self, db_config):
        """Test that drop_cur_session_tables is idempotent"""
        token = set_session_id("test_drop_idempotent")
        database = TeamDatabase(db_config)
        try:
            await database.initialize()
            await database.drop_cur_session_tables()
            await database.drop_cur_session_tables()

            await database.create_cur_session_tables()

            await database.team.create_team(
                "team_idemp_drop",
                "Idem Drop Team",
                "leader1"
            )
            await database.task.create_task(
                "task1",
                "team_idemp_drop",
                "Task 1",
                "Content 1",
                "pending"
            )

            task = await database.task.get_task("task1")
            assert task is not None

        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_drop_then_create_same_session(self, db_config):
        """Test that dropping and recreating tables in same session works"""
        token = set_session_id("test_recreate")
        database = TeamDatabase(db_config)
        try:
            await database.initialize()

            await database.team.create_team(
                "team_recreate",
                "Recreate Team",
                "leader1"
            )
            await database.task.create_task(
                "task1",
                "team_recreate",
                "Task 1",
                "Content 1",
                "pending"
            )

            task1 = await database.task.get_task("task1")
            assert task1 is not None

            await database.drop_cur_session_tables()

            try:
                task_after_drop = await database.task.get_task("task1")
                assert False
            except Exception as e:
                assert "no such table" in str(e)

            await database.create_cur_session_tables()

            await database.team.create_team(
                "team_recreate2",
                "Recreate Team 2",
                "leader1"
            )
            await database.task.create_task(
                "task2",
                "team_recreate2",
                "Task 2",
                "Content 2",
                "pending"
            )

            task2 = await database.task.get_task("task2")
            assert task2 is not None
            assert task2.task_id == "task2"

        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_drop_without_session_id(self, db_config):
        """Test that drop without session_id in context is safe"""
        token = set_session_id("")
        database = TeamDatabase(db_config)
        try:
            await database.initialize()
            await database.drop_cur_session_tables()
        finally:
            await database.close()
            reset_session_id(token)


class TestRuntimeCleanup:
    """Test storage-level runtime cleanup helpers."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cleanup_all_runtime_state_clears_dynamic_tables_and_static_rows(self, tmp_path):
        """Cleanup should remove all session tables and clear team/member rows."""
        db_path = tmp_path / "runtime_cleanup.db"
        config = DatabaseConfig(
            db_type=DatabaseType.SQLITE,
            connection_string=str(db_path),
        )
        database = TeamDatabase(config)
        token = set_session_id("cleanup_session_a")
        try:
            await database.initialize()
            await database.team.create_team(
                "cleanup_team",
                "Cleanup Team",
                "leader1",
            )
            agent_card = AgentCard(name="CleanupAgent").model_dump_json()
            await database.member.create_member(
                member_name="member1",
                team_name="cleanup_team",
                display_name="Member One",
                agent_card=agent_card,
                status="ready",
            )
            await database.task.create_task(
                "task_a",
                "cleanup_team",
                "Task A",
                "Content A",
                "pending",
            )

            reset_session_id(token)
            token = set_session_id("cleanup_session_b")
            await database.create_cur_session_tables()
            await database.task.create_task(
                "task_b",
                "cleanup_team",
                "Task B",
                "Content B",
                "pending",
            )

            deleted_tables, cleared_tables = await database.cleanup_all_runtime_state()

            suffix_a = _sanitize_session_id_for_table("cleanup_session_a")
            suffix_b = _sanitize_session_id_for_table("cleanup_session_b")
            assert f"team_task_{suffix_a}" in deleted_tables
            assert f"team_task_{suffix_b}" in deleted_tables
            assert f"team_task_dependency_{suffix_a}" in deleted_tables
            assert f"team_task_dependency_{suffix_b}" in deleted_tables
            assert f"team_message_{suffix_a}" in deleted_tables
            assert f"team_message_{suffix_b}" in deleted_tables
            assert f"message_read_status_{suffix_a}" in deleted_tables
            assert f"message_read_status_{suffix_b}" in deleted_tables
            assert cleared_tables == ["team_info", "team_member"]

            async with database.engine.begin() as conn:
                table_names = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
                team_count = (
                    await conn.exec_driver_sql("SELECT COUNT(*) FROM team_info")
                ).scalar_one()
                member_count = (
                    await conn.exec_driver_sql("SELECT COUNT(*) FROM team_member")
                ).scalar_one()

            assert sorted(table_names) == ["team_info", "team_member"]
            assert team_count == 0
            assert member_count == 0
        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_force_delete_team_session_cleans_only_current_session(self, tmp_path):
        """Force delete should keep non-current session tables intact."""
        db_path = tmp_path / "force_cleanup.db"
        config = DatabaseConfig(
            db_type=DatabaseType.SQLITE,
            connection_string=str(db_path),
        )
        database = TeamDatabase(config)
        token = set_session_id("force_cleanup_a")
        try:
            await database.initialize()
            await database.team.create_team(
                "cleanup_team",
                "Cleanup Team",
                "leader1",
            )
            await database.task.create_task(
                "task_a",
                "cleanup_team",
                "Task A",
                "Content A",
                "pending",
            )

            reset_session_id(token)
            token = set_session_id("force_cleanup_b")
            await database.create_cur_session_tables()
            await database.task.create_task(
                "task_b",
                "cleanup_team",
                "Task B",
                "Content B",
                "pending",
            )

            assert await database.force_delete_team_session("cleanup_team") is True

            async with database.engine.begin() as conn:
                table_names = sorted(
                    await conn.run_sync(
                        lambda sync_conn: inspect(sync_conn).get_table_names()
                    )
                )
                team_count = (
                    await conn.exec_driver_sql("SELECT COUNT(*) FROM team_info")
                ).scalar_one()
                member_count = (
                    await conn.exec_driver_sql("SELECT COUNT(*) FROM team_member")
                ).scalar_one()

            suffix_a = _sanitize_session_id_for_table("force_cleanup_a")
            suffix_b = _sanitize_session_id_for_table("force_cleanup_b")
            assert "team_info" in table_names
            assert "team_member" in table_names
            assert f"team_task_{suffix_b}" not in table_names
            assert f"team_task_dependency_{suffix_b}" not in table_names
            assert f"team_message_{suffix_b}" not in table_names
            assert f"message_read_status_{suffix_b}" not in table_names
            assert f"team_task_{suffix_a}" in table_names
            assert f"team_task_dependency_{suffix_a}" in table_names
            assert f"team_message_{suffix_a}" in table_names
            assert f"message_read_status_{suffix_a}" in table_names
            assert team_count == 0
            assert member_count == 0
        finally:
            await database.close()
            reset_session_id(token)


# ---------------------------------------------------------------------------
# Dependency-graph primitive coverage (mutate_dependency_graph,
# _terminate_task_in_session, status refresh).
#
# New tests use plain pytest function style; older sections retain their
# class-based layout to keep diff churn focused.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mutate_dependency_graph_atomic_with_cycle(db):
    """A batch that closes a cycle is rejected and rolled back as one unit.

    No edge from the batch survives — the dependency table looks identical
    to the pre-call state.
    """
    await db.team.create_team(team_name="team_atomic_cycle", display_name="T", leader_member_name="leader")
    for tid in ("A", "B", "C"):
        await db.task.create_task(tid, "team_atomic_cycle", tid, "content", "pending")
    pre_result = await db.task.mutate_dependency_graph(
        team_name="team_atomic_cycle",
        add_edges=[("A", "B")],
    )
    assert pre_result.ok is True

    # Batch: B -> C, C -> A (the second edge closes A -> B -> C -> A).
    result = await db.task.mutate_dependency_graph(
        team_name="team_atomic_cycle",
        add_edges=[("B", "C"), ("C", "A")],
    )
    assert result.ok is False
    assert "Circular dependency" in result.reason

    deps_b = await db.task.get_task_dependencies("B")
    deps_c = await db.task.get_task_dependencies("C")
    assert deps_b == []
    assert deps_c == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mutate_dependency_graph_refreshes_downstream(db):
    """Adding a single edge flips the source from PENDING to BLOCKED."""
    await db.team.create_team(team_name="team_refresh", display_name="T", leader_member_name="leader")
    await db.task.create_task("upstream", "team_refresh", "U", "c", "pending")
    await db.task.create_task("downstream", "team_refresh", "D", "c", "pending")

    result = await db.task.mutate_dependency_graph(
        team_name="team_refresh",
        add_edges=[("downstream", "upstream")],
    )
    assert result.ok is True
    refreshed_ids = {t.task_id for t in result.refreshed_tasks}
    assert "downstream" in refreshed_ids

    downstream = await db.task.get_task("downstream")
    assert downstream.status == "blocked"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cancel_task_resolves_outgoing_edges_and_unblocks_downstream(db):
    """Cancelling A flips B (which depended on A) from BLOCKED to PENDING.

    Regression guard for the prior bug: cancel_task left dependent tasks
    stuck in BLOCKED forever because the outgoing edge was never resolved.
    """
    await db.team.create_team(team_name="team_cancel_unblock", display_name="T", leader_member_name="leader")
    await db.task.create_task("A", "team_cancel_unblock", "A", "c", "pending")
    await db.task.create_task("B", "team_cancel_unblock", "B", "c", "pending")
    await db.task.mutate_dependency_graph(team_name="team_cancel_unblock", add_edges=[("B", "A")])
    assert (await db.task.get_task("B")).status == "blocked"

    result = await db.task.cancel_task("A")
    assert result is not None
    unblocked_ids = {t.task_id for t in result["unblocked_tasks"]}
    assert "B" in unblocked_ids

    a = await db.task.get_task("A")
    b = await db.task.get_task("B")
    assert a.status == "cancelled"
    assert b.status == "pending"

    deps_b = await db.task.get_task_dependencies("B")
    assert all(d.resolved for d in deps_b)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cancel_all_tasks_does_not_resurrect_terminal_tasks(db):
    """Bulk cancel along a chain (A -> B -> C) leaves every task CANCELLED.

    Even though resolving A's outgoing edge would normally unblock B, B
    itself is also being cancelled in the same call. The refresh
    primitive is gated on PENDING/BLOCKED, so CANCELLED tasks are never
    rolled back to PENDING.
    """
    await db.team.create_team(team_name="team_cancel_chain", display_name="T", leader_member_name="leader")
    for tid in ("A", "B", "C"):
        await db.task.create_task(tid, "team_cancel_chain", tid, "c", "pending")
    await db.task.mutate_dependency_graph(
        team_name="team_cancel_chain",
        add_edges=[("B", "A"), ("C", "B")],
    )

    result = await db.task.cancel_all_tasks("team_cancel_chain")
    cancelled_ids = {t.task_id for t in result["cancelled_tasks"]}
    assert cancelled_ids == {"A", "B", "C"}

    for tid in ("A", "B", "C"):
        task = await db.task.get_task(tid)
        assert task.status == "cancelled"

    unblocked_ids = {t.task_id for t in result["unblocked_tasks"]}
    assert unblocked_ids.isdisjoint(cancelled_ids)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mutate_dependency_graph_rejects_terminal_target(db):
    """Adding an edge whose source is already CLAIMED is rejected.

    The CLAIMED task is mid-execution; silently re-blocking it would
    surprise the assignee. Same protection applies to terminal statuses.
    """
    await db.team.create_team(team_name="team_reject_terminal", display_name="T", leader_member_name="leader")
    await db.task.create_task("upstream", "team_reject_terminal", "U", "c", "pending")
    await db.task.create_task("claimed_task", "team_reject_terminal", "C", "c", "pending")
    await db.member.create_member(
        member_name="m1",
        team_name="team_reject_terminal",
        display_name="m1",
        agent_card="{}",
        status="ready",
    )
    await db.task.claim_task("claimed_task", "m1")

    result = await db.task.mutate_dependency_graph(
        team_name="team_reject_terminal",
        add_edges=[("claimed_task", "upstream")],
    )
    assert result.ok is False
    assert "claimed" in result.reason