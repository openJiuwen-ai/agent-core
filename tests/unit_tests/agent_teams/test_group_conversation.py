# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public workspace history and mention notifications over the existing mailbox."""

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.conversation import ConversationMessage
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.group_conversation import GroupConversationLog
from openjiuwen.agent_teams.tools.team import TeamBackend


def message(log, identity, *, timestamp=10, content="hello"):
    return ConversationMessage(
        message_id=str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            [log.team_name, log.session_id, identity], ensure_ascii=False))),
        team_name=log.team_name, session_id=log.session_id, client_message_id=identity,
        sender="user", sender_name="user", content=content, timestamp=timestamp,
    )


def read_archive(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def log(tmp_path):
    token = set_task_openjiuwen_home(tmp_path / "home")
    try:
        yield GroupConversationLog("group", "session", workspace_path=tmp_path / "workspace")
    finally:
        reset_task_openjiuwen_home(token)


def test_log_persists_original_text_and_idempotency_across_instances(log):
    original = message(log, "same", content="前文" * 3000)
    original.attachments = [{"name": "brief.txt", "path": "/shared/brief.txt"}]
    stored, duplicate = log.append(original)
    assert stored == original and not duplicate
    reopened = GroupConversationLog("group", "session")
    assert reopened.path == log.path
    assert reopened.history_path == log.history_path == log.path / "history.json"
    retry = original.model_copy(update={"timestamp": 20})
    stored_again, duplicate = reopened.append(retry)
    assert duplicate and stored_again == stored
    assert reopened.list_messages() == [stored]
    assert read_archive(log.history_path)[0]["content"] == original.content
    with pytest.raises(ValueError, match="different|conflict"):
        reopened.append(original.model_copy(update={"content": "changed"}))
    assert reopened.list_messages() == [stored]


def test_log_concurrent_instances_preserve_complete_records(log, tmp_path):
    other = GroupConversationLog("group", "session", workspace_path=tmp_path / "workspace")
    def append(index):
        target = log if index % 2 else other
        return target.append(message(target, str(index % 12), content=f"message {index % 12}"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(append, range(48)))
    assert sum(not duplicate for _, duplicate in results) == 12
    records = read_archive(log.history_path)
    assert len(records) == 12
    assert {record["client_message_id"] for record in records} == {str(n) for n in range(12)}


def test_log_time_range_tail_and_same_timestamp_trigger(log):
    for number in range(1, 7):
        log.append(message(log, str(number), timestamp=number * 10, content=str(number)))
    assert [m.content for m in log.list_messages(after_timestamp=20, through_timestamp=50)] == ["3", "4", "5"]
    assert [m.content for m in log.list_messages(limit=2, latest=True)] == ["5", "6"]
    for latest, identity in ((True, "1"), (False, "6")):
        trigger_id = message(log, identity).message_id
        selected = log.list_messages(
            after_timestamp=20, through_timestamp=50, limit=2, latest=latest, trigger_message_id=trigger_id,
        )
        assert len(selected) == 2 and trigger_id in {item.message_id for item in selected}
    trigger = message(log, "same-ms", timestamp=60, content="same millisecond")
    log.append(trigger)
    assert log.list_messages(after_timestamp=60) == []
    assert log.list_messages(after_timestamp=60, through_timestamp=60,
                             trigger_message_id=trigger.message_id) == [trigger]
    # A clock rollback must not drop the message which explicitly mentions the member.
    assert log.list_messages(after_timestamp=70, through_timestamp=60,
                             trigger_message_id=trigger.message_id) == [trigger]


def test_log_cleanup_isolated_by_session_and_team(log):
    sessions = [GroupConversationLog("group", session) for session in ("a/b", "a_b")]
    other = GroupConversationLog("other-group", "session")
    for current in [log, *sessions, other]:
        current.append(message(current, "same"))
    assert len({str(current.path) for current in [log, *sessions, other]}) == 4
    sessions[0].delete_session()
    assert sessions[0].list_messages() == []
    assert len(sessions[1].list_messages()) == 1
    GroupConversationLog.delete_registered("group")
    assert log.list_messages() == []
    assert sessions[1].list_messages() == []
    assert len(other.list_messages()) == 1


@pytest_asyncio.fixture
async def group(tmp_path, monkeypatch):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("session")
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("group", "Group", "team_leader")
    for name, role in (("team_leader", "leader"), ("alice", "teammate"),
                       ("bob", "teammate"), ("human", "passive_human")):
        await db.member.create_member(name, "group", name, "{}", "ready", role=role)
    backend = TeamBackend("group", "alice", False, db, SimpleNamespace(publish=AsyncMock()))
    backend.group_chat_spec = SimpleNamespace(
        enable_group_chat=True, language="cn", group_context_tail=5, workspace=None,
    )
    backend.bind_group_session("session")
    clock = {"timestamp": 10}
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.engine.get_current_time", lambda: clock["timestamp"])
    try:
        yield SimpleNamespace(backend=backend, db=db, clock=clock)
    finally:
        await db.close()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)


@pytest.mark.asyncio
async def test_mentions_send_latest_delta_and_full_history_path(group):
    backend = group.backend
    for number in range(1, 51):
        group.clock["timestamp"] = number
        first = await backend.append_group_message(
            "user", f"[message {number}]", client_message_id=str(number),
            mentions=["alice"] if number == 50 else [],
        )
        if number < 50:
            assert first.notified_members == []
    log = await backend.group_conversation()
    rows = await group.db.message.get_messages("group", "alice", unread_only=True)
    assert len(rows) == 1 and first.notified_members == ["alice"]
    assert str(first.context_path) in rows[0].content and "read_file" in rows[0].content
    assert "(0, 50]" in rows[0].content and "[message 45]" not in rows[0].content
    for number in range(46, 51):
        assert f"[message {number}]" in rows[0].content
    assert log.last_notified("alice") == 50
    for number in range(51, 101):
        group.clock["timestamp"] = number
        second = await backend.append_group_message(
            "user", f"[message {number}]", client_message_id=str(number),
            mentions=["alice"] if number == 100 else [],
        )
    rows = await group.db.message.get_messages("group", "alice", unread_only=True)
    assert len(rows) == 2 and "(50, 100]" in rows[-1].content
    assert "[message 50]" not in rows[-1].content
    for number in range(96, 101):
        assert f"[message {number}]" in rows[-1].content
    assert log.last_notified("alice") == 100
    assert first.context_path == second.context_path == str(log.history_path)
    archived = await asyncio.to_thread(read_archive, second.context_path)
    assert [m["timestamp"] for m in archived] == list(range(1, 101))
    files = await asyncio.to_thread(lambda: {item.name for item in log.path.glob("*.json")})
    assert files == {"history.json", ".notified.json"}


@pytest.mark.asyncio
async def test_duplicate_public_messages_validate_members_and_skip_passive_humans(group):
    backend = group.backend
    results = await asyncio.gather(*(
        backend.append_group_message("user", "hello", client_message_id="same", mentions=["alice", "alice", "human"])
        for _ in range(8)
    ))
    assert sum(not result.duplicate for result in results) == 1
    assert sum(result.notified_members == ["alice"] for result in results) == 1
    assert results[0].message.mentions == ["alice", "human"]
    assert len(await group.db.message.get_messages("group", "alice")) == 1
    assert await group.db.message.get_messages("group", "human") == []
    with pytest.raises(ValueError, match="different|conflict"):
        await backend.append_group_message("user", "changed", client_message_id="same", mentions=["alice", "human"])
    for sender, mentions in (("stranger", []), ("user", ["stranger"])):
        with pytest.raises(ValueError, match="Unknown"):
            await backend.append_group_message(sender, "invalid", client_message_id="invalid", mentions=mentions)
    assert len((await backend.group_conversation()).list_messages()) == 1


@pytest.mark.asyncio
async def test_independent_member_watermarks_survive_reopening(group):
    await group.backend.append_group_message("user", "first", client_message_id="1", mentions=["alice"])
    group.clock["timestamp"] = 20
    await group.backend.append_group_message("user", "both", client_message_id="2", mentions=["alice", "bob"])
    alice = (await group.db.message.get_messages("group", "alice"))[-1].content
    bob = (await group.db.message.get_messages("group", "bob"))[-1].content
    assert "(10, 20]" in alice and "(0, 20]" in bob
    log = GroupConversationLog("group", "session")
    assert log.last_notified("alice") == log.last_notified("bob") == 20
    assert log.last_notified("team_leader") == 0


@pytest.mark.asyncio
async def test_same_timestamp_trigger_and_bounded_excerpts(group):
    backend = group.backend
    await backend.append_group_message("user", "first mention", client_message_id="first", mentions=["alice"])
    await backend.append_group_message("user", "late ordinary message", client_message_id="late")
    content = "前文" * 3000
    await backend.append_group_message("user", content, client_message_id="second", mentions=["alice"])
    rows = await group.db.message.get_messages("group", "alice")
    notice = next(row.content for row in rows if content[:2000] in row.content)
    assert "(10, 10]" in notice and "late ordinary message" not in notice
    assert content not in notice
    assert any(m.content == content for m in (await backend.group_conversation()).list_messages())


@pytest.mark.asyncio
async def test_queue_failure_preserves_archive_without_advancing_cursor(group, monkeypatch):
    monkeypatch.setattr(group.backend.message_manager, "send_message", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="Could not queue"):
        await group.backend.append_group_message("user", "saved", client_message_id="failed", mentions=["alice"])
    log = await group.backend.group_conversation()
    assert [m.content for m in log.list_messages()] == ["saved"]
    assert log.last_notified("alice") == 0


@pytest.mark.parametrize("corruption", ["invalid_json", "non_array", "foreign_message"])
def test_invalid_history_is_not_overwritten(log, corruption):
    log.append(message(log, "original"))
    if corruption == "invalid_json":
        content = "["
    elif corruption == "non_array":
        content = "{}"
    else:
        foreign = message(log, "foreign").model_dump()
        foreign["team_name"] = "other-group"
        content = json.dumps([foreign])
    log.history_path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        log.list_messages()
    with pytest.raises(ValueError):
        log.append(message(log, "new"))
    assert log.history_path.read_text(encoding="utf-8") == content


def test_legacy_message_files_are_preserved_until_explicit_conversion(log):
    original = message(log, "legacy")
    log.append(original)
    log.history_path.unlink()
    legacy_path = log.path / f"{original.timestamp:020d}_{original.message_id}.json"
    legacy_path.write_text(original.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="Legacy per-message history"):
        log.list_messages()
    with pytest.raises(ValueError, match="Legacy per-message history"):
        log.append(message(log, "new"))
    assert legacy_path.read_text(encoding="utf-8") == original.model_dump_json()
    assert not log.history_path.exists()


def test_history_path_cannot_escape_archive(log, tmp_path):
    log.append(message(log, "original"))
    outside = tmp_path / "outside-history.json"
    outside.write_text("[]", encoding="utf-8")
    history_path = log.history_path
    history_path.unlink()
    history_path.symlink_to(outside)
    with pytest.raises(ValueError, match="escape"):
        log.list_messages()
    with pytest.raises(ValueError, match="escape"):
        log.append(message(log, "new"))
    assert outside.read_text(encoding="utf-8") == "[]"


def test_watermark_path_cannot_escape_archive(log, tmp_path):
    log.append(message(log, "m1"))
    other = tmp_path / "outside.json"
    other.write_text('{"alice": 99}')
    (log.path / ".notified.json").symlink_to(other)
    with pytest.raises(ValueError, match="escape"):
        log.last_notified("alice")
    with pytest.raises(ValueError, match="escape"):
        log.mark_notified("alice", 100)
    assert other.read_text() == '{"alice": 99}'


def test_registered_cleanup_skips_ordinary_sessions_and_preserves_other_group_scopes(log):
    log.append(message(log, "keep"))
    GroupConversationLog.delete_registered("group", "legacy-session-" + "x" * 300)
    GroupConversationLog.delete_registered("ordinary-team", "x" * 300)
    assert len(log.list_messages()) == 1
    GroupConversationLog.delete_registered("group", "session")
    assert log.list_messages() == []
