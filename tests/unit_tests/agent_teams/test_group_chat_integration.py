# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SDK and tool boundaries: no mention means storage only, no free routing."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.group_conversation import GroupConversationLog
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_group_chat import create_group_chat_tools, group_chat_prompt
from openjiuwen.core.runner.team_runner import _TeamRunnerMixin


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    home = set_task_openjiuwen_home(tmp_path / "home")
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("group", "Group", "leader")
    await db.member.create_member("leader", "group", "Leader", "{}", "ready", role="leader")
    await db.member.create_member("alice", "group", "Expert", "{}", "unstarted", role="teammate")
    token = set_session_id("session")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="group",
                         leader=LeaderSpec(member_name="leader"), enable_group_chat=True)
    backend = TeamBackend("group", "leader", True, db, AsyncMock())
    backend.group_chat_spec = spec
    backend.bind_group_session("session")
    manager = TeamRuntimeManager()
    agent = SimpleNamespace(team_backend=backend, deliver_input=AsyncMock(), auto_start_all=AsyncMock())
    await manager.pool.add(ActiveTeam(team_name="group", current_session_id="session",
                                     agent=agent, state=RuntimeState.RUNNING))
    sdk = _TeamRunnerMixin()
    sdk._team_runtime_manager = manager
    monkeypatch.setattr("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", lambda config: db)
    try:
        yield SimpleNamespace(sdk=sdk, manager=manager, backend=backend, agent=agent, db=db, spec=spec)
    finally:
        reset_session_id(token)
        await db.close()
        reset_task_openjiuwen_home(home)


@pytest.mark.asyncio
async def test_sdk_plain_messages_archive_without_model_or_broadcast(runtime):
    result = await runtime.sdk.post_group_message("Discussing", team_name="group", session_id="session",
                                                  client_message_id="m1")
    assert result.notified_members == []
    assert await asyncio.to_thread(Path(result.context_path).is_file)
    assert Path(result.context_path).name == "history.json"
    log = await runtime.backend.group_conversation()
    assert not await asyncio.to_thread((log.path / ".notified.json").exists)
    runtime.backend.messager.publish.assert_not_awaited()
    runtime.agent.deliver_input.assert_not_awaited()
    runtime.agent.auto_start_all.assert_not_awaited()
    second = await runtime.sdk.post_group_message("Act", team_name="group", session_id="session",
                                                  client_message_id="m2", mentions=["alice"])
    assert second.notified_members == ["alice"]
    event = runtime.backend.messager.publish.call_args.kwargs["message"]
    assert event.event_type == "message"
    assert "Act" not in str(event.payload)
    runtime.agent.deliver_input.assert_not_awaited()
    messages = await runtime.db.message.get_messages("group", "alice", unread_only=True)
    assert len(messages) == 1
    assert "Act" in messages[0].content
    assert second.context_path in messages[0].content
    assert not messages[0].broadcast
    assert messages[0].from_member_name == "user"
    async with runtime.db._sessions.read() as session:
        tables = (await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
    assert not any(name.startswith("group_conversation_") for name in tables)
    assert "team_reliable_delivery" not in tables


@pytest.mark.asyncio
async def test_offline_sdk_requires_storage_and_preserves_custom_workspace_and_session(runtime, tmp_path):
    await runtime.manager.pool.remove("group")
    with pytest.raises(ValueError, match="db_config"):
        await runtime.sdk.post_group_message("hello", team_name="group", session_id="new", client_message_id="m1")
    result = await runtime.sdk.post_group_message(
        "saved", team_name="group", session_id="new", client_message_id="m2",
        mentions=["alice"], db_config=runtime.db.config, workspace_path=str(tmp_path / "custom"),
    )
    assert Path(result.context_path).is_relative_to(tmp_path / "custom")
    target = GroupConversationLog("group", "new")
    assert [item.content for item in target.list_messages()] == ["saved"]
    assert GroupConversationLog("group", "session").list_messages() == []
    with runtime.sdk._bind_interact_team_session("new"):
        messages = await runtime.db.message.get_messages("group", "alice", unread_only=True)
    assert len(messages) == 1 and "saved" in messages[0].content
    assert result.context_path in messages[0].content
    with runtime.sdk._bind_interact_team_session("session"):
        await runtime.db.create_cur_session_tables()
        assert await runtime.db.message.get_messages("group", "alice") == []
    assert get_session_id() == "session"
    runtime.backend.messager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_author_is_bound_and_rejects_arbitrary_routing(runtime):
    runtime.backend.member_name = "alice"
    tools = {tool.card.name: tool for tool in create_group_chat_tools(runtime.backend, make_translator("cn"))}
    assert set(tools) == {"group_send_message"}
    sender = tools["group_send_message"]
    rejected = await sender.invoke(dict(content="forged", client_message_id="x", sender="user"))
    assert not rejected.success
    rejected = await sender.invoke(dict(content="forged", client_message_id="x", team_name="other"))
    assert not rejected.success
    result = await sender.invoke(dict(content="my view", client_message_id="y"))
    assert result.success and result.data["message"]["sender"] == "alice"
    assert result.data["context_path"]


def test_group_chat_config_controls_tools_and_role_hints():
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    restored = TeamAgentSpec.model_validate_json(spec.model_dump_json())
    assert not restored.enable_group_chat
    assert group_chat_prompt(restored) == ""
    assert create_group_chat_tools(SimpleNamespace(group_chat_spec=restored), make_translator("en")) == []
    restored.enable_group_chat = True
    assert "group_send_message" in group_chat_prompt(restored)
    assert "group_read_messages" not in group_chat_prompt(restored)


@pytest.mark.asyncio
async def test_explicit_cleanup_removes_only_selected_scope(runtime, tmp_path):
    result = await runtime.sdk.post_group_message("remove", team_name="group", session_id="session",
                                                  client_message_id="m1", mentions=["alice"])
    other_result = await runtime.sdk.post_group_message(
        "keep", team_name="group", session_id="other", client_message_id="m2",
        db_config=runtime.db.config, workspace_path=str(tmp_path / "custom"),
    )
    selected = await runtime.backend.group_conversation()
    other = GroupConversationLog("group", "other")
    assert selected.last_notified("alice") == result.message.timestamp
    await asyncio.to_thread(selected.delete_session)
    assert selected.list_messages() == []
    assert selected.last_notified("alice") == 0
    assert not await asyncio.to_thread(Path(result.context_path).exists)
    assert [message.content for message in other.list_messages()] == ["keep"]
    assert await asyncio.to_thread(Path(other_result.context_path).is_file)
    await asyncio.to_thread(GroupConversationLog.delete_registered, "group")
    assert other.list_messages() == []
    assert not await asyncio.to_thread(Path(other_result.context_path).exists)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("queue unavailable"), OSError("disk unavailable")])
async def test_group_tool_returns_failure_for_storage_errors(runtime, monkeypatch, failure):
    tool = create_group_chat_tools(runtime.backend, make_translator("cn"))[0]
    monkeypatch.setattr(runtime.backend, "append_group_message", AsyncMock(side_effect=failure))
    result = await tool.invoke(dict(content="message", client_message_id="failed"))
    assert not result.success and result.error == str(failure)
