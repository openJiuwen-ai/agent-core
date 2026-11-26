# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A notification addresses a persisted group message to a member without changing its mentions."""
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.database import engine
from openjiuwen.agent_teams.tools.team import TeamBackend


@pytest_asyncio.fixture
async def group(tmp_path, monkeypatch):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("session")
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("group", "Group", "leader")
    members = (("leader", "leader", "ready"), ("alice", "teammate", "ready"), ("bob", "teammate", "unstarted"),
               ("human", "passive_human", "ready"), ("colleague", "host_managed", "ready"))
    for name, role, status in members:
        await db.member.create_member(name, "group", name, "{}", status, role=role)
    messager = SimpleNamespace(publish=AsyncMock())
    backend = TeamBackend("group", "leader", True, db, messager)
    backend.group_chat_spec = SimpleNamespace(language="cn", workspace=None)
    backend.conversation_mode = "group_chat"
    backend.bind_group_session("session")
    clock = {"timestamp": 10}
    monkeypatch.setattr(
        "openjiuwen.agent_teams.tools.database.message_dao.get_current_time", lambda: clock["timestamp"],
    )
    host = SimpleNamespace(deliver_input=AsyncMock(), has_pending_interrupt=lambda: False,
                           auto_start_member=AsyncMock())
    blueprint = SimpleNamespace(member_name="alice", role=TeamRole.TEAMMATE, team_spec=backend.group_chat_spec)
    infra = SimpleNamespace(team_backend=backend, message_manager=backend.message_manager)
    handler = GroupMessageHandler(host, blueprint, infra, AsyncMock())
    try:
        yield SimpleNamespace(backend=backend, db=db, clock=clock, host=host, handler=handler, messager=messager)
    finally:
        reset_session_id(token)
        await db.close()
        reset_task_openjiuwen_home(home)


async def post(group, content, mentions=(), sender="user"):
    result = await group.backend.append_group_message(sender, content, client_message_id=content, mentions=mentions)
    group.clock["timestamp"] += 10
    return result.message.message_id


async def unread(group, member="alice"):
    rows = await group.db.message.get_broadcast_messages("group", member, unread_only=True)
    return [row.message_id for row in rows]


@pytest.mark.asyncio
async def test_notified_member_reads_the_unmentioned_message_as_group_input(group):
    message_id = await post(group, "who can review this?")
    log = await group.backend.group_conversation()
    history = log.history_path.read_bytes()
    assert await unread(group) == []
    published = group.messager.publish.await_count

    result = await group.backend.notify_group_member("alice", message_id, notification_id="n-1")

    assert (result.created, result.duplicate) == (True, False)
    assert group.messager.publish.await_count == published + 1
    assert await unread(group) == [message_id]
    assert await unread(group, "bob") == []
    stored = await group.db.message.get_message(message_id)
    assert '"mentions": []' in stored.meta
    assert log.history_path.read_bytes() == history

    await group.handler.on_poll_mailbox(SimpleNamespace(event_type="poll"))
    group.host.deliver_input.assert_awaited_once()
    assert "who can review this?" in group.host.deliver_input.await_args.args[0]
    assert await unread(group) == []


@pytest.mark.asyncio
async def test_notification_of_an_older_message_survives_the_read_watermark(group):
    earlier = await post(group, "earlier question")
    later = await post(group, "later mention", ["alice"])
    await group.db.message.mark_message_read(later, "alice")
    watermark = await group.db.message.get_broadcast_read_at("group", "alice")
    assert await unread(group) == []

    await group.backend.notify_group_member("alice", earlier, notification_id="n-older")
    assert await unread(group) == [earlier]

    await group.handler.on_poll_mailbox(SimpleNamespace(event_type="poll"))
    body = group.host.deliver_input.await_args.args[0]
    assert "earlier question" in body and "later mention" not in body
    assert await unread(group) == []
    assert await group.db.message.get_broadcast_read_at("group", "alice") == watermark


@pytest.mark.asyncio
async def test_leader_starts_an_unstarted_notified_member(group):
    message_id = await post(group, "needs bob")
    assert await group.db.message.get_unread_group_members("group") == []
    await group.backend.notify_group_member("bob", message_id, notification_id="n-bob")
    assert await group.db.message.get_unread_group_members("group") == ["bob"]


@pytest.mark.asyncio
async def test_repeated_identity_is_a_noop_even_after_consumption(group):
    message_id = await post(group, "once")
    await group.backend.notify_group_member("alice", message_id, notification_id="n-1")
    repeated = await group.backend.notify_group_member("alice", message_id, notification_id="n-1")
    assert (repeated.created, repeated.duplicate) == (False, True)
    await group.db.message.mark_message_read(message_id, "alice")
    assert await unread(group) == []
    again = await group.backend.notify_group_member("alice", message_id, notification_id="n-1")
    assert again.duplicate
    assert await unread(group) == []


def test_notification_dao_imports_without_sqlite_extended_constants():
    script = """
import sqlite3
import sys
from types import ModuleType

replacement = ModuleType("pysqlite3")
replacement.__dict__.update(vars(sqlite3))
replacement.__name__ = "pysqlite3"
if hasattr(replacement, "SQLITE_CONSTRAINT_PRIMARYKEY"):
    del replacement.SQLITE_CONSTRAINT_PRIMARYKEY
sys.modules["pysqlite3"] = replacement
sys.modules["sqlite3"] = replacement

from openjiuwen.agent_teams.tools.database.message_dao import MessageDao
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_notification_insert_race_reports_duplicate(group, monkeypatch):
    message_id = await post(group, "concurrent notification")
    await group.backend.notify_group_member("alice", message_id, notification_id="n-race")

    real_get = AsyncSession.get
    missed = False

    async def missed_concurrent_insert(self, entity, ident, **kwargs):
        nonlocal missed
        if ident == "n-race" and not missed:
            missed = True
            return None
        return await real_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", missed_concurrent_insert)
    result = await group.backend.notify_group_member("alice", message_id, notification_id="n-race")
    assert (result.created, result.duplicate) == (False, True)
    assert await unread(group) == [message_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    OperationalError(None, None, RuntimeError("database is locked")),
    SATimeoutError("connection pool exhausted"),
])
async def test_notification_write_retries_raise_without_publishing(group, monkeypatch, error):
    message_id = await post(group, "write unavailable")
    published = group.messager.publish.await_count
    attempts = 0

    @asynccontextmanager
    async def unavailable_write(self):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise error
        yield

    monkeypatch.setattr(engine.DbSessions, "write", unavailable_write)
    monkeypatch.setattr(engine, "_DB_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(engine, "_DB_RETRY_BASE_DELAY", 0)

    with pytest.raises(type(error)) as exc:
        await group.backend.notify_group_member("alice", message_id, notification_id="n-failed")
    assert exc.value is error
    assert attempts == 2
    assert group.messager.publish.await_count == published
    assert await unread(group) == []


@pytest.mark.asyncio
async def test_notification_foreign_key_failure_is_not_duplicate(group):
    message_id = await post(group, "foreign key error")
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        await group.db.message.create_group_notification("n-fk", "missing-group", "alice", message_id)
    result = await group.backend.notify_group_member("alice", message_id, notification_id="n-fk")
    assert result.created


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["human", "colleague", "nobody"])
async def test_members_without_an_agent_runtime_cannot_be_notified(group, member):
    message_id = await post(group, "hello")
    with pytest.raises(ValueError):
        await group.backend.notify_group_member(member, message_id, notification_id="n-x")
    assert await group.db.message.members_with_unread("group", ["human", "colleague", "alice"]) == set()


@pytest.mark.asyncio
async def test_invalid_targets_leave_no_notification(group):
    own = await post(group, "from alice", sender="alice")
    with pytest.raises(ValueError):
        await group.backend.notify_group_member("alice", own, notification_id="n-own")
    with pytest.raises(ValueError):
        await group.backend.notify_group_member("alice", "missing", notification_id="n-missing")
    ordinary = await group.backend.message_manager.broadcast_message("ordinary", from_member_name="leader")
    with pytest.raises(ValueError):
        await group.backend.notify_group_member("bob", ordinary, notification_id="n-ordinary")
    await group.db.member.update_member_status("alice", "group", "shut_down")
    message_id = await post(group, "after leaving")
    with pytest.raises(ValueError):
        await group.backend.notify_group_member("alice", message_id, notification_id="n-left")
    group.backend.conversation_mode = "team"
    with pytest.raises(ValueError):
        await group.backend.notify_group_member("bob", message_id, notification_id="n-team")
    assert await group.db.message.get_unread_group_members("group") == []


@pytest.mark.asyncio
async def test_pending_notification_counts_as_unread_until_consumed(group):
    first = await post(group, "first")
    second = await post(group, "second")
    assert not await group.db.message.has_unread_messages("group")
    await group.backend.notify_group_member("alice", first, notification_id="n-1")
    await group.backend.notify_group_member("alice", second, notification_id="n-2")
    assert await group.db.message.members_with_unread("group", ["alice", "bob"]) == {"alice"}

    await group.db.message.mark_message_read(first, "alice")
    assert await unread(group) == [second]
    await group.db.message.mark_message_read(second, "alice")
    assert await unread(group) == []
    assert not await group.db.message.has_unread_messages("group")


@pytest.mark.asyncio
async def test_notification_table_follows_the_session_tables(group):
    from sqlalchemy import inspect

    from openjiuwen.agent_teams.tools.database.engine import drop_session_tables_by_id
    from openjiuwen.agent_teams.tools.models import _get_group_notification_model

    table = _get_group_notification_model().__tablename__

    async def tables():
        async with group.db._sessions.read() as session:
            conn = await session.connection()
            return set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))

    assert table in await tables()
    await group.db.drop_cur_session_tables()
    assert table not in await tables()
    assert await group.db.create_cur_session_tables()
    assert table in await tables()
    assert table in await drop_session_tables_by_id(group.db._sessions, "session")
    assert table not in await tables()
