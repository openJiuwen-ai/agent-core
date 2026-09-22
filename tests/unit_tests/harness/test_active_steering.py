# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Strict steering receipts, admission boundaries and backwards compatibility."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.steering import SteeringInbox
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import (
    ActiveInteractionRound,
    InteractionPhase,
    RoundOutcome,
    RoundWorkItem,
)
from openjiuwen.agent_teams.inbound_render import drop_superseded_snapshots


def active_agent():
    agent = DeepAgent(AgentCard(name="steering-test", description="test"))
    agent._interaction_started = True
    agent._interaction_phase = InteractionPhase.RUNNING
    agent._interaction_session = SimpleNamespace(get_state=lambda key: None)
    work = RoundWorkItem.user(request_id="original", inputs={"query": "first"})
    agent._active_interaction_round = ActiveInteractionRound(work=work, task_id="task")
    queue = asyncio.Queue()
    agent._steering_inbox.open("original")
    agent._steering_inbox.bind(queue)
    return agent, queue


@pytest.mark.asyncio
async def test_fifo_dedup_conflict_capacity_and_metadata_after_context_write():
    agent, queue = active_agent()
    agent._steering_inbox.max_pending = 2
    first = await agent.steer_active(active_request_id="original", input_id="1", content="A")
    assert first["status"] == "accepted"
    assert await agent.steer_active(active_request_id="original", input_id="1", content="A") == first
    assert (await agent.steer_active(active_request_id="original", input_id="1", content="B"))[
        "reason"
    ] == "input_id_conflict"
    assert (await agent.steer_active(active_request_id="original", input_id="2", content="B"))["status"] == "accepted"
    assert (await agent.steer_active(active_request_id="original", input_id="3", content="C"))["reason"] == "queue_full"
    assert queue.qsize() == 2
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    ctx.fire = AsyncMock()
    ctx.bind_steering_queue(queue)
    entered, release = asyncio.Event(), asyncio.Event()

    async def write(*args, **kwargs):
        entered.set()
        await release.wait()

    emitted = []

    async def emit(chunk):
        assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "consumed"
        emitted.append(chunk)

    ctx.session = SimpleNamespace(write_stream=emit)
    ctx.extra["_streaming"] = True
    context = SimpleNamespace(add_messages=AsyncMock(side_effect=write))
    admission = asyncio.create_task(
        ReActAgent._admit_user_message(
            None, ctx, context, ctx.drain_steering(), source="steering", prefix="[STEERING] "
        )
    )
    await entered.wait()
    assert emitted == []
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "accepted"
    release.set()
    await admission
    message = context.add_messages.call_args.args[0]
    assert message.content == "[STEERING] A\nB"
    assert message.metadata["steering_input_ids"] == ["1", "2"]
    assert len(emitted) == 1
    assert emitted[0].type == "steering_consumed"
    assert emitted[0].payload == {"input_ids": ["1", "2"]}
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "consumed"
    assert (await agent.steer_active(active_request_id="original", input_id="1", content="A"))["status"] == "consumed"
    assert queue.empty()


@pytest.mark.asyncio
async def test_failed_context_write_never_claims_consumed():
    agent, queue = active_agent()
    await agent.steer_active(active_request_id="original", input_id="1", content="A")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    ctx.fire = AsyncMock()
    context = SimpleNamespace(add_messages=AsyncMock(side_effect=RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        await ReActAgent._admit_user_message(None, ctx, context, [queue.get_nowait()], source="steering")
    agent._steering_inbox.finish()
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "unknown"


@pytest.mark.asyncio
async def test_filtered_user_input_is_not_consumed():
    agent, queue = active_agent()
    await agent.steer_active(active_request_id="original", input_id="1", content="A")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    async def filter_input(event):
        ctx.inputs.parts.clear()

    ctx.fire = filter_input
    context = SimpleNamespace(add_messages=AsyncMock())
    await ReActAgent._admit_user_message(None, ctx, context, [queue.get_nowait()], source="steering")
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "not_applied"
    context.add_messages.assert_not_called()


@pytest.mark.asyncio
async def test_final_empty_check_closes_window_before_async_persistence():
    agent, queue = active_agent()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    ctx.bind_steering_queue(queue)
    await agent.steer_active(active_request_id="original", input_id="1", content="A")
    assert ctx.close_steering_if_empty() is False
    queued = ctx.drain_steering()
    queued[0].settle("consumed")
    assert ctx.close_steering_if_empty() is True
    # Model/context persistence may now yield indefinitely; admission stays shut.
    result = await agent.steer_active(active_request_id="original", input_id="2", content="late")
    assert result["reason"] == "not_active"
    assert queue.empty()


@pytest.mark.asyncio
async def test_outer_round_boundary_rejects_and_settles_unconsumed_inputs():
    agent, queue = active_agent()
    original = agent._active_interaction_round.work
    agent._interaction_output.has_consumer = lambda: True
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(*args):
        agent._steering_inbox.bind(queue)
        assert (await agent.steer_active(active_request_id="original", input_id="1", content="pending"))[
            "status"
        ] == "accepted"
        return RoundOutcome()

    async def boundary(*args):
        entered.set()
        await release.wait()
        return True

    agent.run_one_round = run
    agent._emit_round_boundary = boundary
    task = asyncio.create_task(agent._execute_round(original))
    await entered.wait()
    assert (await agent.steer_active(active_request_id="original", input_id="2", content="late"))[
        "reason"
    ] == "not_active"
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "not_applied"
    assert queue.empty()
    release.set()
    await task
    assert not agent._event_manager.has_pending_work()


@pytest.mark.asyncio
async def test_wrong_execution_idle_and_pending_approval_never_start_work():
    agent, queue = active_agent()
    assert (await agent.steer_active(active_request_id="other", input_id="1", content="A"))["reason"] == "not_active"
    agent._interaction_session = SimpleNamespace(get_state=lambda key: {"interrupt": True})
    assert (await agent.get_steering_capability(active_request_id="original"))["reason"] == "waiting_input"
    assert (await agent.steer_active(active_request_id="original", input_id="1", content="A"))[
        "reason"
    ] == "waiting_input"
    agent._interaction_phase = InteractionPhase.IDLE
    agent._active_interaction_round = None
    assert (await agent.steer_active(active_request_id="original", input_id="1", content="A"))["reason"] == "not_active"
    assert queue.empty()
    assert not agent._event_manager.has_pending_work()


def test_retained_dedup_is_bounded_without_eviction_of_current_execution():
    inbox = SteeringInbox(max_records=2)
    queue = asyncio.Queue()
    inbox.open("first")
    inbox.bind(queue)
    for input_id in ["1", "2"]:
        assert inbox.accept("first", input_id, input_id)["status"] == "accepted"
        queue.get_nowait().settle("consumed")
    assert inbox.accept("first", "3", "3")["reason"] == "queue_full"
    assert inbox.accept("first", "1", "1")["status"] == "consumed"
    inbox.open("second")
    inbox.bind(queue)
    assert inbox.accept("second", "3", "3")["status"] == "accepted"
    assert len(inbox._records) == 2


def test_user_text_is_not_dropped_as_superseded_team_snapshot():
    inbox = SteeringInbox()
    queue = asyncio.Queue()
    inbox.open("team")
    inbox.bind(queue)
    text = '<team-event kind="task-board">user text</team-event>'
    inbox.accept("team", "1", text)
    user = queue.get_nowait()
    parts = drop_superseded_snapshots([user, '<team-event kind="task-board">new</team-event>'])
    assert any(part is user for part in parts)


def test_context_write_in_flight_is_unknown_when_execution_is_cancelled():
    from copy import deepcopy

    inbox = SteeringInbox()
    queue = asyncio.Queue()
    inbox.open("request")
    inbox.bind(queue)
    inbox.accept("request", "1", "text")
    value = queue.get_nowait()
    assert deepcopy(value) is value
    value.begin_context_write()
    inbox.finish("execution_cancelled")
    assert inbox.lookup("request", "1")["status"] == "unknown"


def test_old_loop_cannot_close_or_consume_new_execution_inputs():
    inbox = SteeringInbox()
    shared_queue = asyncio.Queue()
    old_window = inbox.open("old")
    inbox.bind(shared_queue)
    old_context = AgentCallbackContext(agent=None, inputs=None, session=None)
    old_context.bind_steering_queue(shared_queue)
    inbox.open("new")
    inbox.bind(shared_queue)
    new_context = AgentCallbackContext(agent=None, inputs=None, session=None)
    new_context.bind_steering_queue(shared_queue)
    assert inbox.accept("new", "1", "new direction")["status"] == "accepted"
    old_window.finish()
    assert old_context.drain_steering() == []
    assert old_context.close_steering_if_empty()
    assert inbox.accepting
    assert inbox.lookup("new", "1")["status"] == "accepted"
    assert new_context.has_pending_steering()
    assert new_context.drain_steering() == ["new direction"]


@pytest.mark.asyncio
async def test_display_boundary_failure_does_not_fail_consumed_input():
    agent, queue = active_agent()
    await agent.steer_active(active_request_id="original", input_id="1", content="new requirement")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=SimpleNamespace(
        write_stream=AsyncMock(side_effect=RuntimeError("stream display failed")),
    ))
    ctx.extra["_streaming"] = True
    ctx.fire = AsyncMock()
    context = SimpleNamespace(add_messages=AsyncMock())
    await ReActAgent._admit_user_message(None, ctx, context, [queue.get_nowait()], source="steering")
    assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "consumed"
    context.add_messages.assert_awaited_once()
    ctx.session.write_stream.assert_awaited_once()
