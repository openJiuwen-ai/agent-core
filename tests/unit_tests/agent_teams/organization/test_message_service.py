# coding: utf-8

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.message_service import OrgMessageService
from openjiuwen.agent_teams.organization.schema import OrgTaskCreator
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest_asyncio.fixture
async def message_service():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    service = OrgMessageService(db=db, organization_id="org-1", session_id="session-1")
    yield service
    await db.close()


@pytest_asyncio.fixture
async def org_facade():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    manager = TeamOrganizationManager(
        organization_id="org-1",
        db=db,
        session_id="session-1",
    )
    yield manager
    await db.close()


@pytest.mark.asyncio
async def test_send_persists_without_side_channel(message_service: OrgMessageService):
    result = await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="Please take the API compatibility slice.",
    )
    assert result.ok
    assert result.data is not None
    assert result.data["message_id"].startswith("org-msg-")

    messages = await message_service.list_leader_messages(team_id="team-b")
    assert len(messages) == 1
    assert messages[0]["content"] == "Please take the API compatibility slice."
    assert messages[0]["message_id"] == result.data["message_id"]


@pytest.mark.asyncio
async def test_list_includes_broadcast_for_recipient(message_service: OrgMessageService):
    await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        content="broadcast to all",
    )
    await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-c",
        content="only for team-c",
    )
    for_b = await message_service.list_leader_messages(team_id="team-b")
    assert [m["content"] for m in for_b] == ["broadcast to all"]
    for_c = await message_service.list_leader_messages(team_id="team-c", include_broadcast=False)
    assert [m["content"] for m in for_c] == ["only for team-c"]


@pytest.mark.asyncio
async def test_purge_organization_removes_messages(message_service: OrgMessageService):
    await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="temp",
    )
    deleted = await message_service.purge_organization()
    assert deleted == 1
    assert await message_service.list_leader_messages(team_id="team-b") == []


@pytest.mark.asyncio
async def test_manager_dissolve_purges_messages_and_tasks(org_facade: TeamOrganizationManager):
    await org_facade.initialize(metadata={"owner_team_id": "team-a"})
    await org_facade.register_leader(team_id="team-a", leader_id="leader-a")
    await org_facade.message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="cleanup me",
    )
    created = await org_facade.task_pool.create_task(
        title="t",
        description="d",
        required_capabilities=["x"],
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-a",
            organization_id="org-1",
            team_id="team-a",
        ),
    )
    assert created.ok

    deleted = await org_facade.dissolve_organization()
    assert deleted["leader_messages"] >= 1
    assert deleted["tasks"] >= 1
    assert await org_facade.message_service.list_leader_messages(team_id="team-b") == []
    assert await org_facade.get_organization() is None
