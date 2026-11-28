# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SDK and tool boundaries: no mention means storage only, no free routing."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.schema.conversation import ConversationAppendResult
from openjiuwen.agent_teams.interaction.payload import GroupChatMessage
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.group_chat.tools import GroupSendMessageTool, group_chat_prompt
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
    # Mirror ``TeamRuntimeManager.activate``: session tables exist before pooling.
    await db.create_cur_session_tables()
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="group",
                         leader=LeaderSpec(member_name="leader"))
    backend = TeamBackend("group", "leader", True, db, AsyncMock())
    backend.conversation_mode = "group_chat"
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


async def post_group(runtime, content, *, team_name, session_id, client_message_id, mentions=()):
    result = await runtime.sdk.interact_agent_team(
        {"type": "group_chat", "body": content, "client_message_id": client_message_id,
         "mentions": list(mentions)}, team_name=team_name, session_id=session_id,
    )
    assert result.ok, result.reason
    return ConversationAppendResult.model_validate(result.data)


@pytest.mark.asyncio
async def test_sdk_messages_broadcast_without_unmentioned_model_input(runtime):
    result = await post_group(runtime, "Discussing", team_name="group", session_id="session",
                                                  client_message_id="m1")
    assert result.notified_members == []
    assert await asyncio.to_thread(Path(result.context_path).is_file)
    assert Path(result.context_path).name == "history.jsonl"
    log = await runtime.backend.group_conversation()
    assert not await asyncio.to_thread((log.path / ".notified.json").exists)
    runtime.backend.messager.publish.assert_awaited()
    runtime.agent.deliver_input.assert_not_awaited()
    runtime.agent.auto_start_all.assert_not_awaited()
    second = await post_group(runtime, "Act", team_name="group", session_id="session",
                                                  client_message_id="m2", mentions=["alice"])
    assert second.notified_members == ["alice"]
    event = runtime.backend.messager.publish.call_args.kwargs["message"]
    assert event.event_type == "broadcast"
    assert "Act" not in str(event.payload)
    runtime.agent.deliver_input.assert_not_awaited()
    messages = await runtime.db.message.get_messages("group", "alice", unread_only=True)
    assert messages == []
    broadcasts = await runtime.db.message.get_team_messages("group", broadcast=True)
    assert [m.content for m in broadcasts] == ["Discussing", "Act"]
    assert await runtime.db.message.get_broadcast_read_at("group", "alice") == 0
    async with runtime.db._sessions.read() as session:
        tables = (await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
    assert not any(name.startswith("group_conversation_") for name in tables)
    assert "team_reliable_delivery" not in tables


@pytest.mark.asyncio
async def test_group_input_requires_active_runtime(runtime):
    await runtime.manager.pool.remove("group")
    result = await runtime.sdk.interact_agent_team(
        GroupChatMessage("hello", "m1"), team_name="group", session_id="session",
    )
    assert not result.ok and result.reason == "not_active"
    assert not GroupConversationLog("group", "session").history_path.exists()


@pytest.mark.asyncio
async def test_group_invalid_mentions_do_not_fall_back_to_leader(runtime):
    result = await runtime.sdk.interact_agent_team(
        GroupChatMessage("hello", "m1", ("unknown",)), team_name="group", session_id="session",
    )
    assert not result.ok and result.reason == "invalid_group_chat"
    assert not GroupConversationLog("group", "session").history_path.exists()
    runtime.agent.deliver_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_malformed_input_is_rejected(runtime):
    result = await runtime.sdk.interact_agent_team(
        {"type": "group_chat", "body": "hello", "client_message_id": "m1", "sender": "alice"},
        team_name="group", session_id="session",
    )
    assert result.reason == "invalid_group_chat"


@pytest.mark.asyncio
async def test_group_input_to_dropped_session_tables_fails_without_recreating_them(runtime):
    await runtime.db.drop_cur_session_tables()
    result = await runtime.sdk.interact_agent_team(
        GroupChatMessage("hello", "m1"), team_name="group", session_id="session",
    )
    assert not result.ok and result.reason == "group_chat_delivery_failed"
    async with runtime.db._sessions.read() as session:
        tables = (await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
    assert not any(name.startswith("team_message_") for name in tables)
    assert not GroupConversationLog("group", "session").history_path.exists()
    runtime.agent.deliver_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_author_is_bound_and_rejects_arbitrary_routing(runtime):
    runtime.backend.member_name = "alice"
    sender = GroupSendMessageTool(runtime.backend, make_translator("cn"))
    rejected = await sender.invoke(dict(content="forged", client_message_id="x", sender="user"))
    assert not rejected.success
    rejected = await sender.invoke(dict(content="forged", client_message_id="x", team_name="other"))
    assert not rejected.success
    result = await sender.invoke(dict(content="my view", client_message_id="y"))
    assert result.success and result.data["message"]["sender"] == "alice"
    assert result.data["context_path"]


def test_group_chat_tools_and_role_hints_are_available():
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    restored = TeamAgentSpec.model_validate_json(spec.model_dump_json())
    assert "group_send_message" in group_chat_prompt(restored)
    assert "group_read_messages" not in group_chat_prompt(restored)


@pytest.mark.asyncio
async def test_explicit_cleanup_removes_only_selected_scope(runtime, tmp_path):
    result = await post_group(runtime, "remove", team_name="group", session_id="session",
                                                  client_message_id="m1", mentions=["alice"])
    other_backend = TeamBackend("group", "leader", True, runtime.db, AsyncMock())
    other_backend.group_chat_spec = runtime.spec
    other_backend.bind_group_session("other")
    other_token = set_session_id("other")
    try:
        await runtime.db.create_cur_session_tables()
    finally:
        reset_session_id(other_token)
    other_result = await other_backend.append_group_message("user", "keep", client_message_id="m2")
    other = GroupConversationLog("group", "other")
    await asyncio.to_thread(GroupConversationLog.delete_registered, "group", "session")
    assert not await asyncio.to_thread(Path(result.context_path).exists)
    assert [json.loads(line)["content"] for line in other.history_path.read_text().splitlines()] == ["keep"]
    assert await asyncio.to_thread(Path(other_result.context_path).is_file)
    await asyncio.to_thread(GroupConversationLog.delete_registered, "group")
    assert not await asyncio.to_thread(Path(other_result.context_path).exists)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("queue unavailable"), OSError("disk unavailable")])
async def test_group_tool_returns_failure_for_storage_errors(runtime, monkeypatch, failure):
    tool = GroupSendMessageTool(runtime.backend, make_translator("cn"))
    monkeypatch.setattr(runtime.backend, "append_group_message", AsyncMock(side_effect=failure))
    result = await tool.invoke(dict(content="message", client_message_id="failed"))
    assert not result.success and result.error == str(failure)


@pytest.mark.asyncio
async def test_initial_group_input_uses_same_dispatch_and_emits_acceptance(runtime):
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamRole

    agent = runtime.agent
    agent.role = TeamRole.LEADER
    agent._stream_controller = SimpleNamespace(stream_queue=asyncio.Queue())
    agent._member_name = lambda: "leader"
    payloads = TeamAgent._initial_leader_route_payloads(
        agent, {"query": {"type": "group_chat", "body": "first", "client_message_id": "first"}},
    )
    await TeamAgent._dispatch_initial_leader_route(agent, payloads)
    event = await agent._stream_controller.stream_queue.get()
    assert event.payload["event_type"] == "team.group_message.accepted"
    assert event.payload["notified_members"] == []
    history = GroupConversationLog("group", "session").history_path
    assert [json.loads(line)["content"] for line in history.read_text().splitlines()] == ["first"]
    agent.deliver_input.assert_not_awaited()
    runtime.backend.messager.publish.assert_awaited()


@pytest.mark.asyncio
async def test_first_group_message_registers_team_without_leader_input(runtime):
    await runtime.db.team.delete_team("group")
    result = await runtime.sdk.interact_agent_team(
        GroupChatMessage("new group", "new"), team_name="group", session_id="session",
    )
    assert result.ok, result.reason
    assert await runtime.db.team.get_team("group") is not None
    runtime.agent.deliver_input.assert_not_awaited()
    runtime.agent.auto_start_all.assert_not_awaited()


@pytest.mark.parametrize("mode,role,excluded,expected", [
    ("team", "leader", None, False),
    ("group_chat", "leader", None, True),
    ("group_chat", "teammate", None, True),
    ("group_chat", "human_agent", None, False),
    ("group_chat", "leader", {"group_send_message"}, False),
])
@pytest.mark.asyncio
async def test_group_tool_is_assembled_once_for_runtime_mode(runtime, mode, role, excluded, expected):
    from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
    from openjiuwen.agent_teams.rails.team_tool_rail import TeamToolRail
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

    runtime.backend.conversation_mode = mode
    tools = create_team_tools(role=role, agent_team=runtime.backend, exclude_tools=excluded)
    assert ("group_send_message" in {tool.card.name for tool in tools}) == expected
    callbacks = TeamToolRail(team_backend=runtime.backend, role=role).get_callbacks()
    assert AgentCallbackEvent.ON_USER_MESSAGE not in callbacks
    assert AgentCallbackEvent.BEFORE_MODEL_CALL not in callbacks
    if mode == "team":
        result = await GroupSendMessageTool(runtime.backend, make_translator("cn")).invoke(
            {"content": "wrong mode", "client_message_id": "x"})
        assert not result.success and "requires a group_chat runtime" in result.error


@pytest.mark.asyncio
async def test_group_runtime_rejects_ordinary_input_without_switching_mode(runtime):
    result = await runtime.manager.interact("ordinary", team_name="group", session_id="session")
    assert not result.ok and result.reason == "conversation_mode_mismatch"
    assert runtime.backend.conversation_mode == "group_chat"
    runtime.agent.deliver_input.assert_not_awaited()
    runtime.backend.conversation_mode = "team"
    result = await runtime.manager.interact(GroupChatMessage("hello", "x"),
                                            team_name="group", session_id="session")
    assert not result.ok and result.reason == "conversation_mode_mismatch"
    assert not GroupConversationLog("group", "session").history_path.exists()


@pytest.mark.asyncio
async def test_group_runtime_delivers_explicit_god_view_privately_to_leader(runtime):
    from openjiuwen.agent_teams.interaction.payload import GodViewMessage

    result = await runtime.manager.interact(
        GodViewMessage(body="heartbeat: check progress"), team_name="group", session_id="session",
    )
    assert result.ok, result.reason
    runtime.agent.deliver_input.assert_awaited_once()
    assert runtime.agent.deliver_input.await_args.args[0] == "heartbeat: check progress"
    assert runtime.backend.conversation_mode == "group_chat"
    # A private leader wake is never projected into the public conversation.
    assert await runtime.db.message.get_team_messages("group", broadcast=True) == []
    assert not GroupConversationLog("group", "session").history_path.exists()

@pytest.mark.parametrize("incoming,expected", [
    ({"query": {"type": "group_chat", "body": "hello", "client_message_id": "m1"}}, "group_chat"),
    (GroupChatMessage("hello", "m1"), "group_chat"),
    ('<team-inbound type="group_chat">ordinary</team-inbound>', "team"),
    ({"query": "ordinary"}, "team"),
    ({"query": ""}, None),
    (None, None),
])
def test_runtime_mode_is_selected_only_at_entry(incoming, expected):
    assert TeamRuntimeManager._conversation_mode(incoming) == expected


@pytest.mark.asyncio
async def test_group_runtime_context_and_cli_policy_keep_fixed_mode(runtime):
    from openjiuwen.agent_teams.schema.team import TeamSpec, TeamRuntimeContext, TeamRole, TeamMemberSpec
    from openjiuwen.agent_teams.spawn.external_cli_spawn import _build_member_system_prompt
    from openjiuwen.agent_teams.agent.payload import SpawnPayloadBuilder

    team = TeamSpec(team_name="group", display_name="Group", conversation_mode="group_chat")
    ctx = TeamRuntimeContext(team_spec=team, role=TeamRole.LEADER, member_name="leader")
    builder = SpawnPayloadBuilder(runtime.spec, ctx)
    member = builder.build_member_context(TeamMemberSpec(member_name="alice", display_name="Alice"))
    restored = TeamRuntimeContext.model_validate_json(member.model_dump_json())
    assert restored.team_spec.conversation_mode == "group_chat"
    prompt = await _build_member_system_prompt(runtime.spec, restored, "alice", hitt_enabled=False)
    assert group_chat_prompt(runtime.spec) in prompt


@pytest.mark.parametrize("mode,role,hitt,excluded,expected", [
    ("team", "leader", True, None, False),
    ("group_chat", "leader", True, None, True),
    ("group_chat", "leader", False, None, False),
    ("group_chat", "teammate", True, None, False),
    ("group_chat", "human_agent", True, None, False),
    ("group_chat", "leader", True, {"spawn_passive_human"}, False),
])
@pytest.mark.asyncio
async def test_passive_human_tool_is_group_only(runtime, mode, role, hitt, excluded, expected):
    from openjiuwen.agent_teams.tools.tool_factory import create_team_tools

    runtime.backend.conversation_mode = mode
    runtime.backend._enable_hitt = hitt
    names = {tool.card.name for tool in create_team_tools(
        role=role, agent_team=runtime.backend, exclude_tools=excluded,
    )}
    assert ("spawn_passive_human" in names) == expected
    if role == "leader" and hitt:
        assert "spawn_human_agent" in names


@pytest.mark.parametrize("mode,expected", [("team", False), ("group_chat", True)])
@pytest.mark.asyncio
async def test_passive_human_tool_checks_runtime_mode(runtime, mode, expected):
    from openjiuwen.agent_teams.tools.tool_member import SpawnPassiveHumanTool

    runtime.backend.conversation_mode = mode
    runtime.backend._enable_hitt = True
    tool = SpawnPassiveHumanTool(runtime.backend, make_translator("cn"))
    result = await tool.invoke({"member_name": "passive", "display_name": "Human", "desc": ""})
    assert result.success == expected
    member = await runtime.db.member.get_member("passive", "group")
    assert (member is not None) == expected
    if not expected:
        assert "requires a group_chat runtime" in result.error


@pytest.mark.asyncio
async def test_host_managed_member_does_not_hold_unread_completion(runtime):
    messages = runtime.backend.message_manager
    assert await messages.broadcast_message("Before the colleague joins")
    assert (await runtime.backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")).ok
    assert await messages.broadcast_message("After the colleague joins")
    assert await runtime.db.message.create_message(
        "legacy-direct", "group", "leader", "Legacy private note", to_member_name="colleague",
    )
    assert await messages.members_with_unread(["alice", "colleague"]) == {"alice"}
    assert not await messages.has_unread_messages(include_broadcast=False)

    assert await runtime.db.member.update_member_status("alice", "group", "shut_down")
    assert not await messages.has_unread_messages()


@pytest.mark.asyncio
async def test_slow_host_notification_does_not_block_an_expert_or_the_next_message(runtime, monkeypatch):
    from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
    from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler
    from openjiuwen.agent_teams.schema.events import BroadcastEvent, EventMessage
    from openjiuwen.agent_teams.schema.team import TeamRole

    assert (await runtime.backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")).ok
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_host(message):
        assert message.mentions == ["alice", "colleague"]
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    await runtime.backend.register_host_managed_inbound("colleague", slow_host)
    host = SimpleNamespace(auto_start_member=AsyncMock())
    blueprint = SimpleNamespace(role=TeamRole.LEADER, member_name="leader")
    handler = GroupMessageHandler(host, blueprint, SimpleNamespace(team_backend=runtime.backend), None)
    normal_delivery = AsyncMock()
    monkeypatch.setattr(MessageHandler, "on_message_or_broadcast", normal_delivery)

    async def deliver(client_id, mentions):
        posted = await runtime.backend.append_group_message(
            "user", "Message", client_message_id=client_id, mentions=mentions,
        )
        event = EventMessage.from_event(BroadcastEvent(
            team_name="group", message_id=posted.message.message_id, from_member_name="user",
        ))
        await asyncio.wait_for(handler.on_message_or_broadcast(event), timeout=1)

    try:
        await deliver("mixed-mention", ["alice", "colleague"])
        await asyncio.wait_for(entered.wait(), timeout=1)
        host.auto_start_member.assert_awaited_once_with("alice")
        await deliver("next-message", [])
        assert normal_delivery.await_count == 2
        assert not cancelled.is_set()
    finally:
        await handler.cancel_host_notifications()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert not handler._host_notifications
    assert await runtime.db.member.get_member("alice", "group") is not None
    assert len(await runtime.db.message.get_team_messages("group", broadcast=True)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["read_lock", "cursor"])
@pytest.mark.parametrize("timeout", [False, True])
async def test_host_notification_cancellation_preserves_in_flight_read(runtime, monkeypatch, boundary, timeout):
    from aiosqlite import Cursor
    from openjiuwen.agent_teams.group_chat import message_handler as module
    from openjiuwen.agent_teams.schema.team import TeamRole

    posted = await runtime.backend.append_group_message("user", "Retain me", client_message_id="cancel-read")
    handler = module.GroupMessageHandler(
        SimpleNamespace(), SimpleNamespace(role=TeamRole.LEADER),
        SimpleNamespace(team_backend=runtime.backend), None,
    )
    callback = AsyncMock()
    monkeypatch.setattr(runtime.backend, "notify_host_managed_inbound", callback)
    entered, release = asyncio.Event(), asyncio.Event()
    rows = []
    get_message = runtime.db.message.get_message

    async def read(message_id):
        if boundary == "read_lock":
            entered.set()
        row = await get_message(message_id)
        rows.append(row)
        return row

    execute = Cursor.execute

    async def blocked_execute(cursor, sql, parameters=None):
        if "FROM team_message_" in sql:
            entered.set()
            await release.wait()
        return await execute(cursor, sql, parameters)

    monkeypatch.setattr(runtime.db.message, "get_message", read)
    if boundary == "read_lock":
        await runtime.db._sessions._write_lock.acquire()
    else:
        monkeypatch.setattr(Cursor, "execute", blocked_execute)
    if timeout:
        monkeypatch.setattr(module, "_HOST_NOTIFICATION_TIMEOUT", 0)

    drains = []
    try:
        handler._schedule_host_notification(runtime.backend, posted.message.message_id)
        await asyncio.wait_for(entered.wait(), timeout=1)
        notification = next(iter(handler._host_notifications.values()))
        drains = [asyncio.create_task(handler.cancel_host_notifications()) for _ in range(2)]
        # Yield to both drains, then cancel the notification and its owner again.
        await asyncio.sleep(0)
        for _ in range(2):
            drains[0].cancel()
            notification.cancel()
            await asyncio.sleep(0)
        assert not rows
        assert all(not task.done() for task in drains)
    finally:
        if boundary == "read_lock":
            runtime.db._sessions._write_lock.release()
        release.set()
        outcomes = await asyncio.gather(*drains, return_exceptions=True)
        await handler.cancel_host_notifications()

    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert outcomes[1] is None
    assert len(rows) == 1 and rows[0].message_id == posted.message.message_id
    callback.assert_not_awaited()
    assert not handler._host_notifications
    assert await runtime.db.member.get_member("alice", "group") is not None
    assert (await get_message(posted.message.message_id)).content == "Retain me"


@pytest.mark.asyncio
@pytest.mark.parametrize("callbacks", [1, 2])
async def test_host_callback_can_stop_its_own_team(runtime, callbacks, monkeypatch):
    from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
    from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler
    from tests.unit_tests.agent_teams.test_coordination_lifecycle import _make_kernel_host

    assert (await runtime.backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")).ok
    host = _make_kernel_host()
    host.team_name = "group"
    host.resources.harness = None
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    handler = GroupMessageHandler(host, SimpleNamespace(), SimpleNamespace(), None)
    kernel._dispatcher = SimpleNamespace(group_message=handler)
    runtime.agent.stop_coordination = kernel.stop
    entered = 0
    ready, release = asyncio.Event(), asyncio.Event()
    outcomes = []
    cancellation_sources = {}
    drain = handler.cancel_host_notifications

    async def observe_drain():
        caller = asyncio.current_task()
        peers = {task: task.cancelling() for task in tasks if task is not caller}
        try:
            await drain()
        finally:
            if caller in tasks:
                for task, before in peers.items():
                    if task.cancelling() > before:
                        cancellation_sources[task] = caller

    monkeypatch.setattr(handler, "cancel_host_notifications", observe_drain)

    async def callback(message):
        nonlocal entered
        task = asyncio.current_task()
        result, pool_absent, error = None, None, None
        try:
            entered += 1
            if entered == callbacks:
                ready.set()
            await release.wait()
            result = await runtime.manager.stop_team(team_name="group", session_id="session")
            if result is False:
                pool_absent = await runtime.manager.pool.get("group") is None
        except asyncio.CancelledError as exc:
            error = exc
            raise
        except Exception as exc:
            error = exc
            raise
        finally:
            outcomes.append((message.message_id, task, result, pool_absent, error))

    await runtime.backend.register_host_managed_inbound("colleague", callback)
    message_ids = set()
    for index in range(callbacks):
        posted = await runtime.backend.append_group_message(
            "user", "Stop", client_message_id=f"stop-{index}", mentions=["colleague"],
        )
        message_ids.add(posted.message.message_id)
        handler._schedule_host_notification(runtime.backend, posted.message.message_id)
    tasks = tuple(handler._host_notifications.values())
    try:
        await asyncio.wait_for(ready.wait(), timeout=1)
        release.set()
        task_results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
    finally:
        release.set()
        await handler.cancel_host_notifications()
    assert len(outcomes) == callbacks
    assert {row[0] for row in outcomes} == message_ids
    successful = {task for _, task, result, _, error in outcomes if result is True and error is None}
    assert successful
    results_by_task = dict(zip(tasks, task_results))
    for _, task, result, pool_absent, error in outcomes:
        if isinstance(error, asyncio.CancelledError):
            assert cancellation_sources.get(task) in successful
            assert isinstance(results_by_task[task], asyncio.CancelledError)
        else:
            assert error is None, repr(error)
            assert result is True or result is False
            if result is False:
                assert pool_absent
            assert results_by_task[task] is None
    host.stream_controller.close_stream.assert_called()
    host.session_manager.release_session.assert_called()
    assert await runtime.manager.pool.get("group") is None
    assert not handler._host_message_reads
    assert await runtime.db.member.get_member("alice", "group") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_host_notification_query_failure_is_drained(cancelled):
    from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler

    handler = GroupMessageHandler(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), None)
    callback = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def failing_read(message_id):
        entered.set()
        await release.wait()
        raise ValueError("read failed")

    backend = SimpleNamespace(
        db=SimpleNamespace(message=SimpleNamespace(get_message=failing_read)),
        notify_host_managed_inbound=callback,
    )
    handler._schedule_host_notification(backend, "missing")
    notification = next(iter(handler._host_notifications.values()))
    drain = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        drain = asyncio.create_task(handler.cancel_host_notifications()) if cancelled else None
        await asyncio.sleep(0)
    finally:
        release.set()
        outcome, = await asyncio.gather(notification, return_exceptions=True)
        if drain is not None:
            await drain
    assert isinstance(outcome, asyncio.CancelledError) if cancelled else outcome is None
    callback.assert_not_awaited()
    assert not handler._host_notifications


@pytest.mark.asyncio
async def test_direct_messages_and_tasks_reject_host_managed_members(runtime):
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
    from openjiuwen.agent_teams.tools.tool_message import SendMessageTool

    backend = runtime.backend
    assert (await backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")).ok
    messages = backend.message_manager
    assert await messages.send_message("Private note", "colleague") is None
    assert await messages.multicast_message("Batch note", ["alice", "colleague"]) == []
    assert await runtime.db.message.get_team_messages("group", broadcast=False) == []

    tool = SendMessageTool(messages, make_translator("en"), backend)
    direct = await tool.invoke({"to": "colleague", "content": "Private note"})
    assert not direct.success and "public group mentions" in direct.error
    multicast = await tool.invoke({"targets": ["alice", "colleague"], "content": "Batch note"})
    assert not multicast.success
    assert multicast.data["delivered"] == ["alice"]
    assert multicast.data["failed"][0]["to"] == "colleague"

    tasks = backend.task_manager
    result = await tasks.add_graph([TaskGraphSpec(title="Assigned", content="Work", assignee="colleague")])
    assert not result.ok and "cannot be assigned" in result.reason
    created = await tasks.add(title="Unassigned", content="Work")
    assert created.ok
    assigned = await tasks.assign(created.task_id, "colleague")
    assert not assigned.ok and "cannot be assigned" in assigned.reason

    colleague_tasks = TeamTaskManager("group", "colleague", runtime.db, backend.messager)
    claimed = await colleague_tasks.claim(created.task_id)
    assert not claimed.ok and "cannot claim" in claimed.reason
    assert not (await tasks.get(created.task_id)).assignee


@pytest.mark.asyncio
async def test_host_delivery_deduplicates_in_flight_and_retries_only_failed_receiver(runtime):
    backend = runtime.backend
    for name in ("colleague", "other-host"):
        assert (await backend.spawn_host_managed_member(member_name=name, display_name=name)).ok

    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(message):
        entered.set()
        await release.wait()

    first = AsyncMock(side_effect=wait_for_release)
    second = AsyncMock(side_effect=[RuntimeError("temporary failure"), None])
    await backend.register_host_managed_inbound("colleague", first)
    await backend.register_host_managed_inbound("other-host", second)

    kwargs = {"client_message_id": "host-retry", "mentions": ["colleague", "other-host"]}
    posted = await backend.append_group_message("user", "Message", **kwargs)
    repeated = await backend.append_group_message("user", "Message", **kwargs)
    assert repeated.duplicate
    assert posted.message.message_id == repeated.message.message_id

    pending = asyncio.create_task(backend.notify_host_managed_inbound(posted.message))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await backend.notify_host_managed_inbound(repeated.message)
        first.assert_awaited_once()
    finally:
        release.set()
        await pending

    await backend.notify_host_managed_inbound(repeated.message)
    first.assert_awaited_once()
    assert second.await_count == 2


@pytest.mark.asyncio
async def test_sync_host_callback_runs_off_the_coordination_loop(runtime):
    from threading import get_ident

    assert (await runtime.backend.spawn_host_managed_member(member_name="colleague", display_name="Colleague")).ok
    loop_thread = get_ident()
    callback_threads = []

    def sync_host(message):
        callback_threads.append(get_ident())

    await runtime.backend.register_host_managed_inbound("colleague", sync_host)
    posted = await runtime.backend.append_group_message(
        "user", "Message", client_message_id="sync-host", mentions=["colleague"],
    )
    await runtime.backend.notify_host_managed_inbound(posted.message)
    assert len(callback_threads) == 1
    assert callback_threads[0] != loop_thread
