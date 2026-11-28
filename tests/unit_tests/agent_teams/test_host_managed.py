"""Host-managed members have a durable identity and no internal execution runtime."""

from unittest.mock import AsyncMock

import pytest
from openjiuwen.agent_teams.context import set_session_id, reset_session_id
from openjiuwen.agent_teams.messager.inprocess import InProcessMessager, cleanup_inprocess_bus
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent, TeamTopic
from openjiuwen.agent_teams.schema.team import TeamRole, TeamMemberSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend


@pytest.mark.asyncio
async def test_host_managed_registers_and_shuts_down_without_an_internal_runtime():
    token = set_session_id("host-managed")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        backend = TeamBackend("host-team", "leader", True, database, AsyncMock(), enable_hitt=False)
        await backend.build_team(display_name="Team", desc="", leader_display_name="Leader", leader_desc="")
        result = await backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")
        assert result.ok
        member = await database.member.get_member("colleague", "host-team")
        assert (member.role, member.status) == ("host_managed", "ready")
        assert not await backend.is_human_agent("colleague")
        assert not (await backend.spawn_host_managed_member(
            member_name="colleague", display_name="Duplicate",
        )).ok
        receiver = AsyncMock()
        await backend.register_host_managed_inbound("colleague", receiver)
        assert (await backend.shutdown_member("colleague")).ok
        assert (await database.member.get_member("colleague", "host-team")).status == "shut_down"
    finally:
        await database.close()
        reset_session_id(token)


def test_host_managed_can_be_declared_without_an_agent_template():
    member = TeamMemberSpec(member_name="colleague", display_name="Colleague", desc="AI", prompt="",
                            role_type=TeamRole.HOST_MANAGED)
    assert member.model_dump(mode="json")["role_type"] == "host_managed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reserved", "duplicate", "database"])
async def test_build_team_with_failed_predefined_host_can_be_retried(tmp_path, monkeypatch, failure):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("predefined-host")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    first = TeamMemberSpec(member_name="first-host", display_name="First Host", role_type=TeamRole.HOST_MANAGED)
    second = TeamMemberSpec(member_name="second-host", display_name="Second Host", role_type=TeamRole.HOST_MANAGED)
    original_create_member = database.member.create_member

    async def fail_second_host(**kwargs):
        if kwargs["member_name"] == "second-host":
            return False
        return await original_create_member(**kwargs)

    on_built = AsyncMock()
    messager = AsyncMock()
    members = [first, second]
    if failure == "reserved":
        members[1] = second.model_copy(update={"member_name": "user"})
    elif failure == "duplicate":
        members[1] = second.model_copy(update={"member_name": "first-host"})
    backend = TeamBackend(
        "predefined-team", "leader", True, database, messager,
        predefined_members=members, on_team_built=on_built,
    )
    build_args = dict(display_name="Team", desc="", leader_display_name="Leader", leader_desc="")
    try:
        with monkeypatch.context() as patch:
            if failure == "database":
                patch.setattr(database.member, "create_member", fail_second_host)
            with pytest.raises(ValueError, match="(Reserved|Duplicate|Database rejected)"):
                await backend.build_team(**build_args)

        assert not await database.team.team_exists("predefined-team")
        assert await database.member.get_member("first-host", "predefined-team") is None
        assert await database.member.get_member("leader", "predefined-team") is None
        on_built.assert_not_awaited()
        messager.publish.assert_not_awaited()

        backend.predefined_members = [first, second]
        await backend.build_team(**build_args)
        for member in backend.predefined_members:
            row = await database.member.get_member(member.member_name, "predefined-team")
            assert row.role == "host_managed"
        on_built.assert_awaited_once()
        assert messager.publish.await_count == 3
    finally:
        await database.close()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)


@pytest.mark.asyncio
async def test_build_team_deferred_events_wait_for_flush(tmp_path):
    """``build_team(defer_event_publish=True)`` buffers instead of publishing.

    Framework bootstrap builds the team before the leader kernel has
    subscribed to its topics; the in-process bus drops subscriber-less
    publishes without replay, so the build-time events (member_spawned for
    identity-only members, then team_created) must stay buffered during
    ``build_team`` and reach subscribers only after the caller flushes
    them, in the same order an immediate publish would have used.
    """
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("defer-sess")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    messager = InProcessMessager()
    received: list[EventMessage] = []

    async def record(event: EventMessage) -> None:
        received.append(event)

    try:
        await database.initialize()
        topic = TeamTopic.TEAM.build("defer-sess", "defer-team")
        await messager.subscribe(topic, record)
        passive = TeamMemberSpec(member_name="watcher", display_name="Watcher", role_type=TeamRole.PASSIVE_HUMAN)
        host = TeamMemberSpec(member_name="ops-bot", display_name="Ops Bot", role_type=TeamRole.HOST_MANAGED)
        backend = TeamBackend(
            "defer-team", "leader", True, database, messager,
            predefined_members=[passive, host], enable_hitt=True,
        )

        await backend.build_team(
            display_name="Team", desc="", leader_display_name="Leader", leader_desc="",
            defer_event_publish=True,
        )

        # Nothing reached the bus while building: the subscriber was there,
        # so any publish would have been delivered immediately.
        assert received == []
        # The buffer parks on the backend in the immediate-publish order:
        # passive-human and host-managed member_spawned, then team_created.
        assert [event.event_type for _, event in backend._pending_build_events] == [
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.CREATED,
        ]
        # The deferral window is closed again — later publishes go straight out.
        assert backend._deferred_events is None

        await backend.flush_pending_build_events()

        assert [event.event_type for event in received] == [
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.CREATED,
        ]
        # The buffer is cleared for the whole pass: a second flush is a no-op,
        # so already-delivered notices are never duplicated.
        assert backend._pending_build_events == []
        await backend.flush_pending_build_events()
        assert len(received) == 3
    finally:
        cleanup_inprocess_bus()
        await database.close()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)


@pytest.mark.asyncio
async def test_flush_pending_build_events_publishes_each_event_best_effort():
    """A failing publish is logged and skipped; later events still go out."""
    token = set_session_id("defer-flaky")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    messager = AsyncMock()
    try:
        await database.initialize()
        backend = TeamBackend("flaky-team", "leader", True, database, messager)
        first = EventMessage(event_type=TeamEvent.MEMBER_SPAWNED, payload={})
        second = EventMessage(event_type=TeamEvent.CREATED, payload={})
        backend._pending_build_events = [("topic-a", first), ("topic-b", second)]
        messager.publish.side_effect = [RuntimeError("bus down"), None]

        await backend.flush_pending_build_events()

        assert messager.publish.await_count == 2
        # Cleared despite the failure — every event gets one attempt, no retry.
        assert backend._pending_build_events == []
    finally:
        await database.close()
        reset_session_id(token)
