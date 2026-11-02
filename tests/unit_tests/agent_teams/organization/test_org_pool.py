# coding: utf-8

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.messager.inprocess import InProcessMessager
from openjiuwen.agent_teams.organization.events import OrgTopic
from openjiuwen.agent_teams.organization.pool import (
    clear_process_org_managers,
    get_process_org_manager,
)
from openjiuwen.agent_teams.organization.schema import OrgTaskCreator
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest.fixture(autouse=True)
def _reset_org_manager_registry():
    clear_process_org_managers()
    yield
    clear_process_org_managers()


@pytest.mark.asyncio
async def test_get_process_org_manager_reuses_manager_for_same_db_key():
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=":memory:org-pool-stable-key",
    )
    db_one = TeamDatabase(config)
    db_two = TeamDatabase(config)

    manager_one = get_process_org_manager(
        organization_id="org-stable",
        db=db_one,
        session_id="session-1",
    )
    manager_two = get_process_org_manager(
        organization_id="org-stable",
        db=db_two,
        session_id="session-1",
    )

    assert manager_one is manager_two

    await db_one.close()
    await db_two.close()


@pytest.mark.asyncio
async def test_db_close_clears_process_org_managers_for_db_key():
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=":memory:org-pool-close-key",
    )
    db = TeamDatabase(config)
    manager_before_close = get_process_org_manager(
        organization_id="org-close",
        db=db,
        session_id="session-1",
    )

    await db.close()

    db_reopened = TeamDatabase(config)
    manager_after_close = get_process_org_manager(
        organization_id="org-close",
        db=db_reopened,
        session_id="session-1",
    )

    assert manager_before_close is not manager_after_close

    await db_reopened.close()


@pytest.mark.asyncio
async def test_cached_read_manager_attaches_messager_to_existing_services():
    config = DatabaseConfig(connection_string=":memory:org-pool-attach")
    read_db = TeamDatabase(config)
    runtime_db = TeamDatabase(config)
    reader = get_process_org_manager(organization_id="org", db=read_db, session_id="session")
    task_pool = reader.task_pool
    message_service = reader.message_service
    messager = Mock()
    try:
        restored = get_process_org_manager(
            organization_id="org", db=runtime_db, session_id="session", messager=messager
        )
        assert restored is reader
        assert restored.task_pool is task_pool
        assert restored.message_service is message_service
        assert restored.messager is messager
        assert task_pool.messager is messager
        assert message_service.messager is messager
    finally:
        await read_db.close()
        await runtime_db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_cached_live_manager_keeps_its_messager(read_only):
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:org-pool-live"))
    messager = Mock()
    manager = get_process_org_manager(
        organization_id="org", db=db, session_id="session", messager=messager
    )
    try:
        cached = get_process_org_manager(
            organization_id="org", db=db, session_id="session", messager=None if read_only else Mock()
        )
        assert cached is manager
        assert cached.messager is messager
        assert cached.task_pool.messager is messager
        assert cached.message_service.messager is messager
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("different_key", ["database", "organization", "session"])
async def test_attaching_messager_does_not_affect_other_registry_keys(different_key):
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:org-pool-isolation"))
    other_db = TeamDatabase(DatabaseConfig(connection_string=":memory:org-pool-other"))
    reader = get_process_org_manager(organization_id="org", db=db, session_id="session")
    messager = Mock()
    try:
        runtime = get_process_org_manager(
            organization_id="other" if different_key == "organization" else "org",
            db=other_db if different_key == "database" else db,
            session_id="other" if different_key == "session" else "session",
            messager=messager,
        )
        assert runtime is not reader
        assert runtime.messager is messager
        assert reader.messager is None
        assert reader.task_pool.messager is None
        assert reader.message_service.messager is None
    finally:
        await db.close()
        await other_db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_team", ["tech", "architecture"])
async def test_upgraded_read_manager_delivers_between_teams(first_team):
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    reader = get_process_org_manager(organization_id="org", db=db, session_id="session")
    task_pool = reader.task_pool
    message_service = reader.message_service
    second_team = "architecture" if first_team == "tech" else "tech"
    transports = {
        name: InProcessMessager(config=MessagerTransportConfig(node_id=f"review-{name}"))
        for name in (first_team, second_team)
    }
    subscriptions = []
    try:
        await reader.initialize()
        inboxes = {}
        for team_id, transport in transports.items():
            await reader.register_leader(team_id=team_id, leader_id=f"leader-{team_id}")
            topic = OrgTopic.TEAM_INBOX.build("session", "org", team_id)
            inboxes[team_id] = AsyncMock()
            await transport.subscribe(topic, inboxes[team_id])
            subscriptions.append((transport, topic))
            restored = get_process_org_manager(
                organization_id="org", db=db, session_id="session", messager=transport
            )
            assert restored is reader
            assert restored.task_pool is task_pool
            assert restored.message_service is message_service
        for sender, recipient in ((first_team, second_team), (second_team, first_team)):
            result = await message_service.send_leader_message(
                from_team_id=sender, from_leader_id=f"leader-{sender}", to_team_id=recipient, content="Ready"
            )
            assert result.ok
            assert result.data["delivered_to"] == [recipient]
            inboxes[recipient].assert_awaited_once()
            assert inboxes[recipient].await_args.args[0].payload["message_id"] == result.data["message_id"]
        events = AsyncMock()
        topic = OrgTopic.TASK.build("session", "org")
        await transports[second_team].subscribe(topic, events)
        subscriptions.append((transports[second_team], topic))
        created = await task_pool.create_task(
            task_id="restored-task", title="Analysis", description="Brief analysis",
            created_by=OrgTaskCreator(creator_id="client", organization_id="org", team_id=first_team),
            required_capabilities=["analysis"],
        )
        assert created.ok
        events.assert_awaited_once()
        assert events.await_args.args[0].payload["task_id"] == "restored-task"
    finally:
        for transport, topic in subscriptions:
            await transport.unsubscribe(topic)
        await db.close()
