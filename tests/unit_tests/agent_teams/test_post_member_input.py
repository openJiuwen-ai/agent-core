# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host inputs use the existing mailbox and preserve explicit recipient scope."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.runner.team_runner import _TeamRunnerMixin


@pytest_asyncio.fixture
async def runtime():
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("experts", "Experts", "leader")
    await db.member.create_member("leader", "experts", "Leader", "{}", "ready", role="leader")
    await db.member.create_member("gone", "experts", "Former member", "{}", "shut_down")
    backend = TeamBackend("experts", "leader", True, db, AsyncMock())
    agent = SimpleNamespace(team_backend=backend, deliver_input=AsyncMock(), auto_start_all=AsyncMock())
    manager = TeamRuntimeManager()
    await manager.pool.add(ActiveTeam(team_name="experts", current_session_id="child-session",
                                     agent=agent, state=RuntimeState.RUNNING))
    sdk = _TeamRunnerMixin()
    sdk._team_runtime_manager = manager
    token = set_session_id("ambient-parent")
    try:
        yield SimpleNamespace(sdk=sdk, manager=manager, backend=backend, agent=agent, db=db)
    finally:
        reset_session_id(token)
        await db.close()


def input_args():
    return dict(team_name="experts", session_id="child-session", member_name="leader")


@pytest.mark.asyncio
async def test_host_input_uses_ordinary_mailbox_and_message_event(runtime):
    result = await runtime.sdk.post_member_input("Research task", **input_args())
    assert result["status"] == "queued"
    assert get_session_id() == "ambient-parent"
    token = set_session_id("child-session")
    try:
        row = await runtime.db.message.get_message(result["message_id"])
        assert row.content == "Research task" and not row.is_read
        assert row.to_member_name == "leader" and row.team_name == "experts"
    finally:
        reset_session_id(token)
    runtime.agent.deliver_input.assert_not_awaited()
    event = runtime.backend.messager.publish.call_args.kwargs["message"]
    assert event.event_type == "message" and "Research task" not in str(event.payload)


@pytest.mark.asyncio
async def test_other_session_stores_offline_without_waking_current_runtime(runtime, monkeypatch):
    args = {**input_args(), "session_id": "offline-session"}
    with pytest.raises(ValueError, match="db_config"):
        await runtime.sdk.post_member_input("Return result", **args)
    monkeypatch.setattr("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", lambda _: runtime.db)
    result = await runtime.sdk.post_member_input("Return result", **args, db_config=runtime.db.config)
    assert result["status"] == "queued"
    token = set_session_id("offline-session")
    try:
        row = await runtime.db.message.get_message(result["message_id"])
        assert row.content == "Return result" and row.to_member_name == "leader"
    finally:
        reset_session_id(token)
    runtime.backend.messager.publish.assert_not_awaited()
    runtime.agent.deliver_input.assert_not_awaited()
    assert get_session_id() == "ambient-parent"


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["missing", "gone"])
async def test_missing_or_departed_recipient_is_rejected(runtime, member):
    with pytest.raises(ValueError, match="Unknown or departed"):
        await runtime.sdk.post_member_input("Task", **{**input_args(), "member_name": member})
    runtime.backend.messager.publish.assert_not_awaited()
    assert get_session_id() == "ambient-parent"


@pytest.mark.asyncio
async def test_mailbox_save_failure_is_not_reported_as_queued(runtime, monkeypatch):
    monkeypatch.setattr(runtime.backend.message_manager, "send_message", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="Could not queue"):
        await runtime.sdk.post_member_input("Task", **input_args())
