"""Merge through the existing mailbox and native supervisor queues."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.harness import NativeHarness
from openjiuwen.agent_teams.message_template import ExpandedMessage
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.run_context import RunContext
from openjiuwen.core.runner import Runner
from tests.unit_tests.agent_teams.tools.test_tool_message import db, _backend, _leader_send, DEV_1, TEAM_NAME
from tests.unit_tests.agent_teams.harness.fixtures import make_spec, start_harness, wait_invoke_running, drain_outputs


@pytest_asyncio.fixture
async def running_native():
    await Runner.start()
    harness = NativeHarness(make_spec())
    fake = await start_harness(harness, sleep_seconds=5)
    harness._merge_follow_ups = True
    consumer = asyncio.create_task(drain_outputs(harness, []))
    try:
        await harness.send("working")
        await wait_invoke_running(fake)
        yield harness
    finally:
        await harness.stop()
        await consumer
        await Runner.stop()


@pytest.mark.asyncio
async def test_native_fifo_merge_retries_and_keeps_late_inputs(running_native):
    harness = running_native
    await harness.send("AFTER work, write an abstract", immediate=False)
    state = harness.load_state(harness._session)
    state.pending_follow_ups.append("persisted follow-up")
    harness.save_state(harness._session, state)
    push = harness._push_steer
    harness._push_steer = lambda *args, **kwargs: False
    with pytest.raises(RuntimeError, match="queue is unavailable"):
        await harness.send("rejected correction", immediate=True)
    assert len(harness._st.pending_queue) == 2
    harness._push_steer = push
    await harness.send("correct current API", immediate=True)
    context = json.loads(harness.event_handler.interaction_queues.steering.get_nowait().content)
    assert context["follow_up_context"] == ["AFTER work, write an abstract", "persisted follow-up"]
    assert context["current_steer"] == "correct current API"
    assert not harness._st.pending_queue
    await harness.send("later arrival", immediate=False)
    assert [item.content for item in harness._st.pending_queue] == ["later arrival"]


@pytest.mark.asyncio
async def test_native_merge_preserves_different_context_fifo(running_native):
    harness = running_native
    await harness.send("other context", run_context=RunContext(session_id="other"))
    await harness.send("same context")
    await harness.send("steer", immediate=True)
    assert [item.content for item in harness._st.pending_queue] == ["other context", "same context"]
    assert harness.event_handler.interaction_queues.steering.get_nowait().content == "steer"


@pytest.mark.asyncio
async def test_native_steer_only_merges_runtime_fifo_not_mailbox(running_native, db):
    harness = running_native
    send = _leader_send(db)
    old = await send.message_manager.send_message("Unrelated mailbox request", DEV_1,
        meta={"private_delivery_mode": "follow_up", "target_tasks": {"A": 1}})
    await harness.send("Runtime follow-up", immediate=False)
    await harness.send("Automatic task-board update", immediate=True)
    context = json.loads(harness.event_handler.interaction_queues.steering.get_nowait().content)
    assert context["follow_up_context"] == ["Runtime follow-up"]
    assert context["current_steer"] == "Automatic task-board update"
    assert [row.message_id for row in await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)] == [old]


@pytest.mark.asyncio
async def test_mailbox_merge_preserves_sources_retries_and_later_messages(db):
    send = _leader_send(db)
    old = await send.message_manager.send_message("Write docs AFTER implementation", DEV_1,
        meta={"private_delivery_mode": "follow_up", "target_tasks": {"A": 1}})
    current = (await send.invoke({"to": DEV_1, "content": "Correct the API now", "mode": "steer"})).data["message_id"]
    backend = _backend(db, DEV_1, False)
    host = NS(has_in_flight_round=lambda: True, has_pending_interrupt=lambda: False,
              active_input_snapshot=lambda: (host, "working"), deliver_input=AsyncMock())
    blueprint = NS(role=TeamRole.TEAMMATE, member_name=DEV_1, team_spec=NS(language="en"))
    handler = MessageHandler(host, blueprint, NS(message_manager=backend.message_manager,
        team_backend=backend, task_manager=backend.task_manager), None)
    handler._active_task_versions = AsyncMock(return_value={"A": 1})
    handler._expand = AsyncMock(side_effect=lambda row: ExpandedMessage(body=row.content, is_template=False))
    host.deliver_input.side_effect = RuntimeError("admission failed")
    with pytest.raises(RuntimeError, match="admission failed"):
        await handler._process_unread_messages(DEV_1)
    assert {row.message_id for row in await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)} == {old, current}
    host.deliver_input.reset_mock(side_effect=True)
    later = []
    async def accept(*args, **kwargs):
        if not later:
            later.append(await send.message_manager.send_message("Later request", DEV_1,
                meta={"private_delivery_mode": "follow_up", "target_tasks": {"A": 1}}))
    host.deliver_input.side_effect = accept
    await handler._process_unread_messages(DEV_1)
    assert host.deliver_input.await_count == 1
    text = host.deliver_input.await_args.args[0]
    assert old in text and "AFTER implementation" in text and "Correct the API" in text
    assert "team_leader" in text
    assert [row.message_id for row in await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)] == later
    assert (await db.message.get_message(old)).is_read
