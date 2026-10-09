# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public group chat projection, mentions, and passive-human guards."""

from __future__ import annotations

import json
import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
from openjiuwen.agent_teams.group_chat.handler import post_message, render_context
from openjiuwen.agent_teams.group_chat.meta import group_addressed, stable_message_id
from openjiuwen.agent_teams.interaction.payload import GroupChatMessage
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.agent_teams.schema.conversation import ConversationMessage
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager


def _message(
    message_id: str,
    timestamp: int,
    content: str = "body",
    session_id: str = "sess-1",
) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        team_name="team-a",
        session_id=session_id,
        client_message_id=message_id,
        sender="user",
        sender_name="user",
        content=content,
        timestamp=timestamp,
    )


class TestProjection:
    """history.jsonl is a regenerable projection, split by session."""

    @pytest.mark.level0
    def test_merge_delete_and_session_isolation(self, tmp_path):
        """Same message id merges, a deleted file regenerates, and sessions stay apart."""
        configure_openjiuwen_home(tmp_path)
        try:
            first = GroupConversationLog("team-a", "sess-1")
            second = GroupConversationLog("team-a", "sess-2")
            first.sync([_message("m1", 1, "one"), _message("m2", 2, "two")])
            first.sync([_message("m2", 2, "two-revised")])
            rows = first.read_messages()
            assert [row.message_id for row in rows] == ["m1", "m2"]
            assert rows[1].content == "two-revised"
            first.history_path.unlink()
            first.sync([_message("m1", 1, "one")])
            assert first.history_path.read_text(encoding="utf-8").count("\n") == 1
            second.sync([_message("other", 3, "elsewhere", session_id="sess-2")])
            assert first.history_path != second.history_path
            assert "elsewhere" not in first.history_path.read_text(encoding="utf-8")
        finally:
            reset_openjiuwen_home()

    @pytest.mark.level0
    def test_symlink_history_is_rejected(self, tmp_path):
        """A symlinked history file is not a place to write the projection."""
        configure_openjiuwen_home(tmp_path)
        try:
            log = GroupConversationLog("team-a", "sess-1")
            log.path.mkdir(parents=True)
            target = tmp_path / "outside.txt"
            target.write_text("secret", encoding="utf-8")
            try:
                log.history_path.symlink_to(target)
            except OSError:
                pytest.skip("this environment cannot create a symlink")
            with pytest.raises(ValueError, match="symlink"):
                log.sync([_message("m1", 1)])
        finally:
            reset_openjiuwen_home()


class TestWireAndExcerpt:
    """Host payload parsing and the fixed five-message window."""

    @pytest.mark.level0
    def test_unknown_wire_field_is_rejected(self):
        """A caller cannot set the author or add an unknown field."""
        with pytest.raises(ValueError):
            GroupChatMessage.from_wire(
                {
                    "type": "group_chat",
                    "body": "hello",
                    "client_message_id": "msg-1",
                    "sender": "leader",
                }
            )
        parsed = GroupChatMessage.from_wire({"query": "plain"})
        assert parsed is None

    @pytest.mark.level0
    def test_trigger_is_appended_not_substituted(self):
        """The mention is added after the prior window instead of replacing its edge."""
        messages = [_message(f"m{index}", index, "x" * 20) for index in range(1, 7)]
        rendered = render_context(messages, "m6", after=0, path="/tmp/history.jsonl")
        assert "m6" in rendered
        assert "m2" in rendered
        assert "m1" not in rendered
        assert "/tmp/history.jsonl" in rendered

    @pytest.mark.level0
    def test_group_addressing_ignores_passive_and_other_sessions(self):
        """A mention wakes only a non-passive member in the current session."""
        meta = {"type": "group_chat", "session_id": "sess-1", "mentions": ["writer", "guest"]}
        assert group_addressed(
            member_name="writer",
            from_member_name="user",
            meta=meta,
            role="teammate",
            session_id="sess-1",
        )
        assert not group_addressed(
            member_name="guest",
            from_member_name="user",
            meta=meta,
            role="passive_human",
            session_id="sess-1",
        )
        assert not group_addressed(
            member_name="writer",
            from_member_name="user",
            meta=meta,
            role="teammate",
            session_id="sess-2",
        )


class _Bus:
    async def publish(self, topic_id, message):
        return None


class _Backend:
    def __init__(self, database: TeamDatabase, session_id: str):
        self.team_name = "team-a"
        self.member_name = "leader"
        self.db = database
        self.group_session_id = session_id
        self.group_chat_spec = None
        self._group_log = None
        self.message_manager = TeamMessageManager(self.team_name, self.member_name, database, _Bus())

    def group_conversation(self) -> GroupConversationLog:
        if self._group_log is None:
            self._group_log = GroupConversationLog(self.team_name, self.group_session_id)
        return self._group_log


class TestArchive:
    """The database row is canonical. A missing team is not built here."""

    @pytest.mark.level0
    @pytest.mark.asyncio
    async def test_post_message_is_idempotent_and_session_scoped(self, tmp_path):
        """Same client id republishes one row; another session does not enter the file."""
        configure_openjiuwen_home(tmp_path)
        token = set_session_id("sess-1")
        database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
        try:
            await database.initialize()
            await database.create_cur_session_tables()
            await database.team.create_team("team-a", "Team", "leader")
            await database.member.create_member(
                "leader", "team-a", "Leader", "{}", "ready", role="leader"
            )
            await database.member.create_member(
                "writer", "team-a", "Writer", "{}", "unstarted", role="teammate"
            )
            await database.member.create_member(
                "guest", "team-a", "Guest", "{}", "ready", role="passive_human"
            )
            backend = _Backend(database, "sess-1")
            first = await post_message(
                backend,
                sender="user",
                sender_name="user",
                content="look here",
                client_message_id="msg-1",
                mentions=["writer", "guest", "writer"],
            )
            assert first.notified_members == ["writer"]
            assert first.duplicate is False
            again = await post_message(
                backend,
                sender="user",
                sender_name="user",
                content="look here",
                client_message_id="msg-1",
                mentions=["writer", "guest"],
            )
            assert again.duplicate is True
            assert again.message.message_id == first.message.message_id
            rows = await database.message.get_team_messages("team-a", broadcast=True)
            assert len(rows) == 1
            assert stable_message_id("team-a", "sess-1", "msg-1") == first.message.message_id
            with pytest.raises(ValueError):
                await post_message(
                    backend,
                    sender="user",
                    sender_name="user",
                    content="different",
                    client_message_id="msg-1",
                    mentions=["writer"],
                )
            with pytest.raises(ValueError):
                await post_message(
                    backend,
                    sender="user",
                    sender_name="user",
                    content="nope",
                    client_message_id="msg-2",
                    mentions=["missing"],
                )
            visible = await database.message.get_broadcast_messages("team-a", "writer", unread_only=True)
            assert [row.message_id for row in visible] == [first.message.message_id]
            leader_visible = await database.message.get_broadcast_messages("team-a", "leader", unread_only=True)
            assert leader_visible == []
            assert await database.message.has_unread_messages("team-a") is True
            quiet = await post_message(
                backend,
                sender="user",
                sender_name="user",
                content="archive only",
                client_message_id="msg-3",
                mentions=[],
            )
            assert quiet.notified_members == []
            assert await database.message.get_unread_group_members("team-a") == ["writer"]
            history = json.loads(backend.group_conversation().history_path.read_text(encoding="utf-8").splitlines()[0])
            assert history["session_id"] == "sess-1"
        finally:
            await database.close()
            reset_session_id(token)
            reset_openjiuwen_home()

    @pytest.mark.level0
    @pytest.mark.asyncio
    async def test_missing_team_does_not_build(self):
        """Group delivery fails when the team row is absent."""
        token = set_session_id("sess-1")
        database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
        try:
            await database.initialize()
            await database.create_cur_session_tables()
            backend = _Backend(database, "sess-1")
            with pytest.raises(ValueError, match="does not exist"):
                await post_message(
                    backend,
                    sender="user",
                    sender_name="user",
                    content="hello",
                    client_message_id="msg-1",
                )
        finally:
            await database.close()
            reset_session_id(token)

    @pytest.mark.level0
    @pytest.mark.asyncio
    async def test_ordinary_broadcast_still_counts_as_unread(self):
        """A normal broadcast is unread for every other non-passive member."""
        token = set_session_id("sess-1")
        database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
        try:
            await database.initialize()
            await database.create_cur_session_tables()
            await database.team.create_team("team-a", "Team", "leader")
            await database.member.create_member("leader", "team-a", "Leader", "{}", "ready", role="leader")
            await database.member.create_member("writer", "team-a", "Writer", "{}", "ready", role="teammate")
            created = await database.message.create_message(
                message_id="ordinary-1",
                team_name="team-a",
                from_member_name="leader",
                content="standup",
                broadcast=True,
            )
            assert created is True
            assert await database.message.has_unread_messages("team-a") is True
            visible = await database.message.get_broadcast_messages("team-a", "writer", unread_only=True)
            assert [row.message_id for row in visible] == ["ordinary-1"]
        finally:
            await database.close()
            reset_session_id(token)
