# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group broadcasts use the existing DB watermark, never a second inbox row."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend


@pytest_asyncio.fixture
async def group(tmp_path, monkeypatch):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("session")
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("group", "Group", "leader")
    for name, role in (("leader", "leader"), ("alice", "teammate"), ("bob", "teammate")):
        await db.member.create_member(name, "group", name, "{}", "unstarted", role=role)
    backend = TeamBackend("group", "leader", True, db, SimpleNamespace(publish=AsyncMock()))
    backend.group_chat_spec = SimpleNamespace(
        language="cn", workspace=None,
    )
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
        yield SimpleNamespace(backend=backend, db=db, clock=clock, host=host, handler=handler)
    finally:
        reset_session_id(token)
        await db.close()
        reset_task_openjiuwen_home(home)


async def post(group, content, mentions=()):
    return await group.backend.append_group_message("user", content, client_message_id=content, mentions=mentions)


@pytest.mark.asyncio
async def test_dispatcher_selects_only_one_mailbox_handler(group, monkeypatch):
    from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
    from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType

    ordinary = SimpleNamespace(on_poll_mailbox=AsyncMock())
    routed = SimpleNamespace(message=ordinary, group_message=group.handler)
    poll = InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX)
    group_poll = AsyncMock(wraps=group.handler.on_poll_mailbox)
    monkeypatch.setattr(group.handler, "on_poll_mailbox", group_poll)
    await post(group, "public only")
    await EventDispatcher._dispatch_mailbox(routed, poll)
    ordinary.on_poll_mailbox.assert_awaited_once()
    group_poll.assert_not_awaited()
    group.clock["timestamp"] = 20
    await post(group, "review now", ["alice"])
    await EventDispatcher._dispatch_mailbox(routed, poll)
    group_poll.assert_awaited_once()
    ordinary.on_poll_mailbox.assert_awaited_once()
    group.host.deliver_input.assert_awaited_once()
    await EventDispatcher._dispatch_mailbox(routed, poll)
    assert ordinary.on_poll_mailbox.await_count == 2
    group_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_history_jsonl_keeps_multiline_content_in_one_record(group):
    contents = ['第一行\n第二行 "引用"', '另一条\u2028消息']
    for content in contents:
        await post(group, content)
        group.clock["timestamp"] += 10
    await post(group, contents[0])
    log = await group.backend.group_conversation()
    lines = log.history_path.read_text().split("\n")
    assert log.history_path.name == "history.jsonl"
    assert lines[-1] == ""
    assert len(lines[:-1]) == 2
    assert [json.loads(line)["content"] for line in lines[:-1]] == contents


@pytest.mark.asyncio
async def test_no_mentions_broadcasts_and_archives_without_consuming(group):
    result = await post(group, "discussion")
    group.backend.messager.publish.assert_awaited_once()
    assert group.backend.messager.publish.call_args.kwargs["message"].event_type == "broadcast"
    await group.handler.on_poll_mailbox(None)
    group.host.deliver_input.assert_not_awaited()
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 0
    assert await group.db.message.get_messages("group", "alice") == []
    log = await group.backend.group_conversation()
    records = [json.loads(line) for line in log.history_path.read_text().splitlines()]
    assert records[0]["content"] == "discussion"
    assert result.message.message_id == records[0]["message_id"]
    assert not (log.path / ".notified.json").exists()


@pytest.mark.asyncio
async def test_excerpt_and_watermark_advance_only_after_delivery(group):
    for timestamp, content in ((1, "old"), (5, "two"), (10, "three"), (15, "four"), (20, "recent"), (30, "trigger")):
        group.clock["timestamp"] = timestamp
        await post(group, content, ["alice"] if timestamp == 30 else [])
    await group.handler.on_poll_mailbox(None)
    text = group.host.deliver_input.call_args.args[0]
    assert text.count('"content":') == 5
    assert '"content": "old"' not in text
    assert '"content": "recent"' in text and '"content": "trigger"' in text
    assert "history.jsonl" in text
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 30
    assert await group.db.message.get_broadcast_read_at("group", "bob") == 0
    group.clock["timestamp"] = 40
    await post(group, "next", ["alice", "bob"])
    await group.handler.on_poll_mailbox(None)
    text = group.host.deliver_input.call_args.args[0]
    assert '"content": "trigger"' not in text and '"content": "next"' in text
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 40
    assert len(await group.backend.message_manager.get_broadcast_messages("bob", unread_only=True)) == 1
    await group.handler.on_poll_mailbox(None)
    assert group.host.deliver_input.await_count == 2


@pytest.mark.asyncio
async def test_failed_delivery_keeps_trigger_pending_and_ordered(group):
    await post(group, "first", ["alice"])
    group.clock["timestamp"] = 20
    await post(group, "second", ["alice"])
    group.host.deliver_input.side_effect = [None, RuntimeError("harness unavailable")]
    with pytest.raises(RuntimeError, match="harness"):
        await group.handler.on_poll_mailbox(None)
    assert '"content": "first"' in group.host.deliver_input.call_args_list[0].args[0]
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 10
    pending = await group.backend.message_manager.get_broadcast_messages("alice", unread_only=True)
    assert [m.content for m in pending] == ["second"]
    group.host.deliver_input.side_effect = None
    await group.handler.on_poll_mailbox(None)
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 20


@pytest.mark.asyncio
async def test_retry_repairs_history_without_duplicate_db_or_input(group, monkeypatch):
    log = await group.backend.group_conversation()
    original = log.sync
    def fail(_messages):
        raise OSError("history unavailable")
    monkeypatch.setattr(log, "sync", fail)
    with pytest.raises(OSError):
        await post(group, "retry", ["alice"])
    group.backend.messager.publish.assert_not_awaited()
    assert len(await group.db.message.get_team_messages("group", broadcast=True)) == 1
    monkeypatch.setattr(log, "sync", original)
    result = await post(group, "retry", ["alice"])
    assert result.duplicate
    assert len(log.history_path.read_text().splitlines()) == 1
    await group.handler.on_poll_mailbox(None)
    await post(group, "retry", ["alice"])
    await group.handler.on_poll_mailbox(None)
    group.host.deliver_input.assert_awaited_once()
    with pytest.raises(ValueError, match="different"):
        await group.backend.append_group_message("user", "conflict", client_message_id="retry", mentions=["alice"])


@pytest.mark.asyncio
async def test_history_can_be_rebuilt_from_db_and_sessions_are_isolated(group):
    await post(group, "stored", ["alice"])
    log = await group.backend.group_conversation()
    log.history_path.unlink()
    await group.handler.on_poll_mailbox(None)
    assert json.loads(log.history_path.read_text().splitlines()[0])["content"] == "stored"
    token = set_session_id("other")
    try:
        await group.db.create_cur_session_tables()
        assert await group.db.message.get_broadcast_read_at("group", "alice") == 0
        assert await group.db.message.get_team_messages("group", broadcast=True) == []
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_unmentioned_broadcasts_cannot_advance_group_watermark(group):
    await post(group, "hidden until mention")
    group.clock["timestamp"] = 20
    await post(group, "another public message")
    await group.handler.on_poll_mailbox(None)
    group.host.deliver_input.assert_not_awaited()
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 0
    group.clock["timestamp"] = 30
    await post(group, "ask", ["alice"])
    await group.handler.on_poll_mailbox(None)
    assert '"content": "hidden until mention"' in group.host.deliver_input.call_args.args[0]


@pytest.mark.asyncio
async def test_startup_scan_only_starts_mentioned_members(group):
    group.handler._blueprint.role = TeamRole.LEADER
    group.handler._blueprint.member_name = "leader"
    await post(group, "public")
    await group.handler.start_mentioned_members()
    group.host.auto_start_member.assert_not_awaited()
    group.clock["timestamp"] = 20
    await post(group, "wake alice", ["alice"])
    await group.handler.start_mentioned_members()
    group.host.auto_start_member.assert_awaited_once_with("alice")


@pytest.mark.asyncio
async def test_leader_poll_starts_mentions_without_leader_input(group):
    from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
    from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
    from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler

    group.handler._blueprint.role = TeamRole.LEADER
    group.handler._blueprint.member_name = "leader"
    await group.db.member.create_member("failed", "group", "Failed", "{}", "error", role="teammate")
    await post(group, "start experts", ["alice", "failed"])
    ordinary = SimpleNamespace(on_poll_mailbox=AsyncMock())
    routed = SimpleNamespace(message=ordinary, group_message=group.handler)
    await EventDispatcher._dispatch_mailbox(routed, InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX))
    assert {call.args[0] for call in group.host.auto_start_member.await_args_list} == {"alice", "failed"}
    ordinary.on_poll_mailbox.assert_awaited_once()
    group.host.deliver_input.assert_not_awaited()
    assert not hasattr(MessageHandler, "_start_unread_members")


@pytest.mark.asyncio
async def test_ordinary_team_broadcast_behavior_is_unchanged(group):
    await group.db.create_cur_session_tables()
    await group.backend.message_manager.broadcast_message("normal broadcast")
    group.handler._expand = AsyncMock(return_value=SimpleNamespace(body="normal broadcast", is_template=False))
    await group.handler.on_poll_mailbox(None)
    assert "normal broadcast" in group.host.deliver_input.call_args.args[0]
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 10


@pytest.mark.asyncio
async def test_completion_only_waits_for_mentioned_broadcasts(group, monkeypatch):
    monkeypatch.setattr(group.backend.task_manager, "list_tasks", AsyncMock(
        return_value=[SimpleNamespace(status="completed")],
    ))
    monkeypatch.setattr(group.db.member, "get_team_members", AsyncMock(
        return_value=[SimpleNamespace(member_name="alice", role="teammate", status="ready")],
    ))
    await post(group, "public discussion")
    assert await group.backend.is_team_completed() is not None
    group.clock["timestamp"] = 20
    await post(group, "review", ["alice"])
    assert await group.backend.is_team_completed() is None
    await group.handler.on_poll_mailbox(None)
    assert await group.backend.is_team_completed() is not None


@pytest.mark.asyncio
async def test_mixed_broadcasts_do_not_skip_a_failed_mention(group):
    await post(group, "please review", ["alice"])
    group.clock["timestamp"] = 20
    await group.backend.message_manager.broadcast_message("normal announcement")
    group.host.deliver_input.side_effect = RuntimeError("delivery failed")
    with pytest.raises(RuntimeError, match="delivery failed"):
        await group.handler.on_poll_mailbox(None)
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 0
    group.host.deliver_input.side_effect = None
    await group.handler.on_poll_mailbox(None)
    assert "please review" in group.host.deliver_input.call_args_list[-2].args[0]
    assert "normal announcement" in group.host.deliver_input.call_args_list[-1].args[0]
    assert await group.db.message.get_broadcast_read_at("group", "alice") == 20
