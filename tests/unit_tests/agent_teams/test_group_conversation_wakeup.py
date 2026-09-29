# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group mentions reuse ordinary mailbox wakeups, including transport self echoes."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.messager.inprocess import InProcessMessager
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent, TeamTopic
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend


@asynccontextmanager
async def running_coordination(tmp_path, role):
    """Keep transport, kernel filtering, dispatcher, handlers and storage real."""
    home_token = set_task_openjiuwen_home(tmp_path)
    session_token = set_session_id("wakeup-session")
    name = "team_leader" if role == TeamRole.LEADER else "avatar"
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("wakeup-team", "Wakeup", "team_leader")
    for member, member_role, status in (
        ("team_leader", "leader", "ready"), ("expert", "teammate", "unstarted"),
        ("avatar", role.value, "ready"),
    ):
        await db.member.create_member(member, "wakeup-team", member, "{}", status, role=member_role)
    messager = InProcessMessager(config=MessagerTransportConfig(node_id=name))
    backend = TeamBackend("wakeup-team", name, role == TeamRole.LEADER, db, messager)
    spec = SimpleNamespace(
        enable_group_chat=True,
        workspace=None,
        group_context_tail=5,
        language="cn",
        dispatch_mode="autonomous",
        reliability=None,
        stale_claim_idle_timeout=600,
        stale_pending_idle_timeout=600,
    )
    backend.group_chat_spec = spec
    backend.bind_group_session("wakeup-session")
    listener = AsyncMock()
    host = SimpleNamespace(
        member_name=name,
        role=role,
        team_name=backend.team_name,
        blueprint=SimpleNamespace(role=role, member_name=name, spec=spec, team_spec=None),
        infra=SimpleNamespace(messager=messager, team_backend=backend, message_manager=backend.message_manager),
        state=SimpleNamespace(event_listeners=[listener]),
        is_agent_ready=lambda: True,
        has_pending_interrupt=lambda: False,
        deliver_input=AsyncMock(),
        auto_start_member=AsyncMock(return_value=True),
    )
    kernel = CoordinationKernel(host)
    kernel.setup(role=role)
    bus = kernel.event_bus
    seen = []
    dispatch = kernel._build_wake_callback()

    async def wake(event):
        seen.append(event)
        await dispatch(event)

    try:
        await kernel.subscribe_transport(backend.team_name)
        await bus.start(wake_callback=wake)
        await bus.pause_polls()
        yield SimpleNamespace(backend=backend, host=host, bus=bus, seen=seen, listener=listener)
    finally:
        await kernel.unsubscribe_transport()
        await bus.stop()
        await db.close()
        reset_session_id(session_token)
        reset_task_openjiuwen_home(home_token)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,target", [
    (TeamRole.LEADER, "team_leader"),
    (TeamRole.LEADER, "expert"),
    (TeamRole.TEAMMATE, "avatar"),
    (TeamRole.HUMAN_AGENT, "avatar"),
])
async def test_self_published_mention_uses_mailbox_and_resumes_polls(tmp_path, role, target):
    async with running_coordination(tmp_path, role) as state:
        result = await state.backend.append_group_message(
            "user", "please help", client_message_id="mention", mentions=[target],
        )
        await asyncio.wait_for(state.bus._event_queue.join(), timeout=2)
        assert result.notified_members == [target]
        assert [event.event_type for event in state.seen] == [TeamEvent.MESSAGE]
        assert state.seen[0].sender_id == state.host.member_name
        assert not state.bus.polls_paused
        if target == "expert":
            state.host.auto_start_member.assert_awaited_once_with("expert")
            state.host.deliver_input.assert_not_awaited()
        else:
            state.host.deliver_input.assert_awaited_once()
            assert "please help" in state.host.deliver_input.await_args.args[0]
            unread = await state.backend.message_manager.get_messages(target, unread_only=True)
            assert unread == []
        if role == TeamRole.HUMAN_AGENT:
            assert state.bus._mailbox_poll_task is None
            assert state.bus._task_poll_task is None


@pytest.mark.asyncio
async def test_plain_chat_has_no_wakeup_and_other_self_events_remain_filtered(tmp_path):
    async with running_coordination(tmp_path, TeamRole.LEADER) as state:
        result = await state.backend.append_group_message(
            "user", "just chatting", client_message_id="no-mention",
        )
        await asyncio.wait_for(state.bus._event_queue.join(), timeout=2)
        assert result.notified_members == []
        assert state.seen == []
        state.listener.assert_not_awaited()
        state.host.deliver_input.assert_not_awaited()
        assert state.bus.polls_paused
        await state.backend.messager.publish(
            TeamTopic.TEAM.build("wakeup-session", "wakeup-team"),
            EventMessage(event_type=TeamEvent.CLEANED, payload={"team_name": "wakeup-team"}),
        )
        await asyncio.wait_for(state.bus._event_queue.join(), timeout=2)
        state.listener.assert_awaited_once()
        assert state.listener.call_args.args[0].sender_id == "team_leader"
        assert state.seen == []
        state.host.deliver_input.assert_not_awaited()
