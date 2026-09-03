# coding: utf-8

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.message_service import OrgMessageService
from openjiuwen.agent_teams.organization.schema import OrgTaskCreator
from openjiuwen.agent_teams.organization.tools import create_org_leader_tools
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.tool_permissions import LEADER_ONLY_TOOLS


@pytest_asyncio.fixture
async def message_service():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    manager = TeamOrganizationManager(
        organization_id="org-1",
        db=db,
        session_id="session-1",
    )
    await manager.initialize(metadata={"owner_team_id": "team-a"})
    for team_id in ("team-a", "team-b", "team-c"):
        await manager.register_leader(team_id=team_id, leader_id=f"leader-{team_id[-1]}")
    yield manager.message_service
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
async def test_get_and_ack_message_are_recipient_scoped(message_service: OrgMessageService):
    sent = await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="Please review the contract.",
    )
    message_id = sent.data["message_id"]

    message = await message_service.get_leader_message(message_id=message_id, team_id="team-b")
    assert message is not None
    assert message["content"] == "Please review the contract."
    assert message["handled_at"] is None
    assert await message_service.get_leader_message(message_id=message_id, team_id="team-c") is None

    first = await message_service.ack_leader_message(
        message_id=message_id,
        team_id="team-b",
        leader_id="leader-b",
        handling_result="reviewed",
    )
    second = await message_service.ack_leader_message(
        message_id=message_id,
        team_id="team-b",
        leader_id="leader-b",
    )
    assert first.ok and second.ok
    assert first.data["handled_at"] == second.data["handled_at"]
    assert second.data["already_handled"] is True
    assert not (
        await message_service.ack_leader_message(
            message_id=message_id,
            team_id="team-c",
            leader_id="leader-c",
        )
    ).ok


@pytest.mark.asyncio
async def test_broadcast_ack_is_independent_per_team(message_service: OrgMessageService):
    sent = await message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        content="broadcast to all",
    )
    message_id = sent.data["message_id"]

    assert len(await message_service.list_leader_messages(team_id="team-b", unread_only=True)) == 1
    assert len(await message_service.list_leader_messages(team_id="team-c", unread_only=True)) == 1

    await message_service.ack_leader_message(
        message_id=message_id,
        team_id="team-b",
        leader_id="leader-b",
    )

    assert await message_service.list_leader_messages(team_id="team-b", unread_only=True) == []
    assert len(await message_service.list_leader_messages(team_id="team-c", unread_only=True)) == 1


@pytest.mark.asyncio
async def test_leader_inbox_tools_get_list_and_ack(org_facade: TeamOrganizationManager):
    await org_facade.initialize(metadata={"owner_team_id": "team-a"})
    await org_facade.register_leader(team_id="team-a", leader_id="leader-a")
    await org_facade.register_leader(team_id="team-b", leader_id="leader-b")
    sent = await org_facade.message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="Please confirm the API.",
    )
    tools = {
        tool.card.name: tool
        for tool in create_org_leader_tools(
            manager=org_facade.task_pool,
            message_service=org_facade.message_service,
            team_id="team-b",
            leader_id="leader-b",
        )
    }
    assert {
        "org_get_leader_message",
        "org_list_leader_messages",
        "org_ack_leader_message",
    }.issubset(LEADER_ONLY_TOOLS)

    listed = await tools["org_list_leader_messages"].invoke({"unread_only": True})
    fetched = await tools["org_get_leader_message"].invoke({"message_id": sent.data["message_id"]})
    acked = await tools["org_ack_leader_message"].invoke(
        {"message_id": sent.data["message_id"], "handling_result": "confirmed"}
    )

    assert listed.success and len(listed.data["messages"]) == 1
    assert fetched.success and fetched.data["content"] == "Please confirm the API."
    assert acked.success and acked.data["handling_result"] == "confirmed"
    assert await org_facade.message_service.list_leader_messages(
        team_id="team-b", unread_only=True
    ) == []


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
    await org_facade.register_leader(team_id="team-b", leader_id="leader-b")
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
