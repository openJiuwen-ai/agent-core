"""Steer merge: actual mailbox persistence and supervisor FIFO behavior."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.follow_up_merge import TeamFollowUpMerger
from openjiuwen.agent_teams.harness import NativeHarness, HarnessState
from openjiuwen.agent_teams.harness.state import InboxMessage
from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.core.runner import Runner
from tests.unit_tests.agent_teams.tools.test_tool_message import db, _leader_send, _backend, _send_tool, DEV_1, DEV_2, TEAM_NAME
from tests.unit_tests.agent_teams.harness.fixtures import make_spec, start_harness, wait_invoke_running, wait_for_state, drain_outputs
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools


def recipient(db, name=DEV_1):
    backend = _backend(db, name, False)
    tool = _send_tool(create_team_tools(role="teammate", agent_team=backend, lang="cn"))
    return backend, tool


async def queued(db):
    send = _leader_send(db)
    old = (await send.invoke({"to": DEV_1, "content": "Write docs AFTER implementation", "mode": "follow_up"})).data["message_id"]
    current = (await send.invoke({"to": DEV_1, "content": "Correct the API now", "mode": "steer"})).data["message_id"]
    return send, old, current


@pytest.mark.asyncio
async def test_snapshot_keeps_later_input_without_a_second_task_queue(db):
    send, old, current = await queued(db)
    backend, receiver = recipient(db)
    merger = TeamFollowUpMerger(backend.message_manager)
    batch = await merger.prepare("Correct API", current, [])
    later = (await send.invoke({"to": DEV_1, "content": "Later request", "mode": "follow_up"})).data["message_id"]
    context = json.loads(batch.content)
    assert [m["message_id"] for m in context["follow_up_context"]] == [old]
    assert context["follow_up_context"][0]["sender"] == send.message_manager.member_name
    assert "AFTER" in context["follow_up_context"][0]["content"]
    await merger.finish(batch, accepted=True)
    unread = await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)
    assert {m.message_id for m in unread} == {current, later}
    status = await send.invoke({"action": "status", "message_id": old})
    assert status.data["recipients"][DEV_1]["state"] == "merged"
    assert "obligation" not in status.data["recipients"][DEV_1]
    assert set(receiver.card.input_params["properties"]["action"]["enum"]) == {"send", "status", "withdraw"}
    assert not (await receiver.invoke({"action": "pending"})).success
    # The delivery receipt does not keep the member busy after the queue drains.
    await db.message.mark_message_read(current, DEV_1)
    await db.message.mark_message_read(later, DEV_1)
    assert DEV_1 not in await db.message.members_with_unread(TEAM_NAME, [DEV_1])


@pytest.mark.asyncio
async def test_failed_injection_retains_mailbox_and_runtime_followups(db):
    _, old, current = await queued(db)
    backend, _ = recipient(db)
    merger = TeamFollowUpMerger(backend.message_manager)
    runtime = InboxMessage(seq=10, content="runtime result", immediate=False)
    batch = await merger.prepare("steer", current, [runtime])
    await merger.finish(batch, accepted=False)
    rows = await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)
    assert old in {m.message_id for m in rows}
    assert not hasattr(backend.message_manager, "pending_obligations")


@pytest.mark.asyncio
async def test_abandoned_merge_is_retryable_without_creating_a_task_queue(db, monkeypatch):
    _, old, current = await queued(db)
    backend, _ = recipient(db)
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.message_dao.get_current_time", lambda: 100)
    await TeamFollowUpMerger(backend.message_manager).prepare("steer", current, [])
    assert not await db.message.claim_delivery(old, DEV_1)
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.message_dao.get_current_time", lambda: 30_101)
    assert await db.message.claim_delivery(old, DEV_1)


@pytest.mark.asyncio
@pytest.mark.parametrize("injection_succeeds", [True, False])
async def test_native_supervisor_merges_runtime_fifo_and_mailbox_after_acceptance(db, injection_succeeds):
    _, old, current = await queued(db)
    backend, _ = recipient(db)
    await Runner.start()
    harness = NativeHarness(make_spec())
    fake = await start_harness(harness, sleep_seconds=5)
    harness.follow_up_merger = TeamFollowUpMerger(backend.message_manager)
    consumer = asyncio.create_task(drain_outputs(harness, []))
    try:
        await harness.send("work")
        await wait_invoke_running(fake)
        await harness.send("async result to handle after work", immediate=False)
        if not injection_succeeds:
            original = harness._push_steer
            harness._push_steer = lambda *args, **kwargs: False
            with pytest.raises(RuntimeError, match="queue is unavailable"):
                await harness.send("failed steer", immediate=True, delivery_id=current)
            assert len(harness._st.pending_queue) == 1
            assert not hasattr(backend.message_manager, "pending_obligations")
            assert old in {row.message_id for row in await db.message.get_messages(TEAM_NAME, DEV_1, unread_only=True)}
            harness._push_steer = original
        await harness.send("steer", immediate=True, delivery_id=current)
        assert not harness._st.pending_queue
        rows = await backend.message_manager.get_team_messages(TEAM_NAME)
        merged = [row for row in rows if json.loads(row.meta or "{}").get("delivery_feedback", {}).get(DEV_1, {}).get("state") == "merged"]
        assert len(merged) == 2
        assert old in {row.message_id for row in merged}
        assert all("obligation" not in json.loads(row.meta)["delivery_feedback"][DEV_1] for row in merged)
        entry = harness.event_handler.interaction_queues.steering.get_nowait()
        context = json.loads(entry.content)
        assert len(context["follow_up_context"]) == 2
        # A later arrival remains an ordinary queued follow-up.
        await harness.send("arrived after steer", immediate=False)
        assert len(harness._st.pending_queue) == 1
    finally:
        await harness.stop()
        await consumer
        await Runner.stop()
