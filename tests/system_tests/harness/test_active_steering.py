# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Active steering over the actual task loop and ReAct engine, using fake I/O."""

import asyncio
import uuid

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.schema.interaction import SendInputRequest
from tests.system_tests.harness.test_steer_inner_loop import (
    _BlockingTool,
    _ModelCallObserver,
    _build_mock_model,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


@pytest.mark.parametrize("boundary_failure", [False, True])
@pytest.mark.asyncio
async def test_two_active_inputs_during_tool_are_consumed_in_same_run_without_orphan_tools(
    monkeypatch, boundary_failure,
):
    if boundary_failure:
        original_write = Session.write_stream

        async def write_with_failed_display(self, chunk, *args, **kwargs):
            if getattr(chunk, "type", None) == "steering_consumed":
                raise RuntimeError("display boundary unavailable")
            return await original_write(self, chunk, *args, **kwargs)

        monkeypatch.setattr(Session, "write_stream", write_with_failed_display)
    await Runner.start()
    agent = None
    consumer = None
    tool = _BlockingTool()
    try:
        observer = _ModelCallObserver()
        model = MockLLMModel()
        model.set_responses(
            [
                create_tool_call_response("blocking_tool", "{}", tool_call_id="tool-1"),
                create_text_response("updated final answer"),
            ]
        )
        agent = create_deep_agent(
            model=_build_mock_model(model),
            tools=[tool],
            rails=[observer],
            system_prompt="Test",
            enable_task_loop=True,
            max_iterations=6,
        )
        await agent.start(session=Session(session_id=f"active-steering-{uuid.uuid4().hex}"))
        stream = await agent.attach_output()
        chunks = []

        async def consume():
            async for chunk in stream:
                chunks.append(chunk)

        consumer = asyncio.create_task(consume())
        await agent.send_input(SendInputRequest(request_id="original", inputs={"query": "start"}))
        await asyncio.wait_for(tool.entered.wait(), 10)
        original_task = agent.active_round.task_id
        for key, text in [("1", "Only east region"), ("2", "Reply in Chinese")]:
            result = await agent.steer_active(active_request_id="original", input_id=key, content=text)
            assert result["status"] == "accepted"
        assert agent.active_round.task_id == original_task
        assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "accepted"
        assert model.call_count == 1
        tool.gate.set()
        await asyncio.wait_for(consumer, 15)
        assert model.call_count == 2
        assert tool.call_count == 1
        assert (await agent.get_steering_status(active_request_id="original", input_id="1"))["status"] == "consumed"
        assert (await agent.get_steering_status(active_request_id="original", input_id="2"))["status"] == "consumed"
        messages = model.call_history[1]
        steer_index = next(
            i for i, message in enumerate(messages) if "Only east region" in str(getattr(message, "content", ""))
        )
        assert messages[steer_index].content == "[STEERING] Only east region\nReply in Chinese"
        tool_index = next(i for i, message in enumerate(messages) if getattr(message, "role", "") == "tool")
        assert tool_index < steer_index
        assert getattr(messages[tool_index], "tool_call_id", None) == "tool-1"
        assert messages[steer_index].metadata["steering_input_ids"] == ["1", "2"]
        assert chunks
        assert (await agent.steer_active(active_request_id="original", input_id="3", content="late"))[
            "reason"
        ] == "not_active"
    finally:
        tool.gate.set()
        if agent is not None:
            await agent.stop()
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()


@pytest.mark.asyncio
async def test_input_after_final_empty_check_is_rejected_while_context_save_is_blocked(monkeypatch):
    await Runner.start()
    agent = None
    consumer = None
    released = asyncio.Event()
    entered = asyncio.Event()
    try:
        model = MockLLMModel()
        model.set_responses([create_text_response("final")])
        agent = create_deep_agent(model=_build_mock_model(model), system_prompt="Test", enable_task_loop=True)
        await agent.start(session=Session(session_id=f"finish-steering-{uuid.uuid4().hex}"))
        engine = agent.react_agent.context_engine
        original_save = engine.save_contexts

        async def save(*args, **kwargs):
            if model.call_count == 1 and not agent._steering_inbox.accepting:
                entered.set()
                await released.wait()
            return await original_save(*args, **kwargs)

        monkeypatch.setattr(engine, "save_contexts", save)
        stream = await agent.attach_output()

        async def consume():
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        await agent.send_input(SendInputRequest(request_id="original", inputs={"query": "start"}))
        await asyncio.wait_for(entered.wait(), 10)
        assert agent.active_round is not None
        result = await agent.steer_active(active_request_id="original", input_id="late", content="change direction")
        assert result["status"] == "not_applied"
        assert result["reason"] == "not_active"
        assert not agent._event_manager.has_pending_work()
        released.set()
        await asyncio.wait_for(consumer, 15)
        assert model.call_count == 1
    finally:
        released.set()
        if agent is not None:
            await agent.stop()
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()


@pytest.mark.parametrize("native_team", [False, True])
@pytest.mark.asyncio
async def test_input_during_model_forces_next_iteration_for_single_and_native_team(native_team):
    from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
    from tests.unit_tests.agent_teams.harness.fixtures import make_spec, wait_for_state

    await Runner.start()
    agent = None
    consumer = None
    entered, released = asyncio.Event(), asyncio.Event()
    try:
        model = MockLLMModel()
        model.set_responses([create_text_response("old answer"), create_text_response("corrected answer")])
        original_stream = model.stream

        async def blocked_stream(*args, **kwargs):
            if model.call_count == 0:
                entered.set()
                await released.wait()
            async for chunk in original_stream(*args, **kwargs):
                yield chunk

        model.stream = blocked_stream
        if native_team:
            agent = NativeHarness(make_spec())
            await agent.start()
            agent.react_agent.set_llm(_build_mock_model(model))
            stream = agent.outputs()
        else:
            agent = create_deep_agent(model=_build_mock_model(model), system_prompt="Test", enable_task_loop=True)
            await agent.start(session=Session(session_id=f"model-steering-{uuid.uuid4().hex}"))
            stream = await agent.attach_output()

        chunks = []
        corrected = asyncio.Event()

        async def consume():
            async for chunk in stream:
                chunks.append(chunk)
                if chunk.type == "llm_output" and chunk.payload.get("content") == "corrected answer":
                    corrected.set()

        consumer = asyncio.create_task(consume())
        if native_team:
            await agent.send("original")
        else:
            await agent.send_input(SendInputRequest(request_id="original", inputs={"query": "start"}))
        await asyncio.wait_for(entered.wait(), 10)
        handle = agent.get_active_steering_request_id()
        assert (
            await agent.steer_active(active_request_id=handle, input_id="1", content="# @expert literal correction")
        )["status"] == "accepted"
        assert not any(chunk.type == "steering_consumed" for chunk in chunks)
        assert (await agent.get_steering_status(active_request_id=handle, input_id="1"))["status"] == "accepted"
        released.set()
        if native_team:
            assert await wait_for_state(agent, HarnessState.IDLE, timeout=10)
        else:
            await asyncio.wait_for(consumer, 10)
        await asyncio.wait_for(corrected.wait(), 10)
        assert model.call_count == 2
        events = [(chunk.type, chunk.payload) for chunk in chunks]
        old_index = next(i for i, (kind, payload) in enumerate(events)
                         if kind == "llm_output" and payload.get("content") == "old answer")
        consumed_index = next(i for i, (kind, payload) in enumerate(events)
                              if kind == "steering_consumed" and payload == {"input_ids": ["1"]})
        new_index = next(i for i, (kind, payload) in enumerate(events)
                         if kind == "llm_output" and payload.get("content") == "corrected answer")
        assert old_index < consumed_index < new_index
        messages = model.call_history[1]
        assert any(getattr(message, "content", "") == "[STEERING] # @expert literal correction" for message in messages)
        assert (await agent.get_steering_status(active_request_id=handle, input_id="1"))["status"] == "consumed"
    finally:
        released.set()
        if agent is not None:
            await agent.stop()
        if consumer is not None:
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()


@pytest.mark.asyncio
async def test_same_request_steering_reopens_for_next_planned_outer_round():
    from tests.system_tests.harness.test_steer_inner_loop import _seed_plan

    await Runner.start()
    agent = None
    consumer = None
    entered, released = asyncio.Event(), asyncio.Event()
    try:
        session = Session(session_id=f"planned-steering-{uuid.uuid4().hex}")
        _seed_plan(session)
        model = MockLLMModel()
        model.set_responses(
            [
                create_text_response("step one"),
                create_text_response("step two"),
                create_text_response("updated step two"),
            ]
        )
        original_stream = model.stream
        task_ids = []

        async def blocked_stream(*args, **kwargs):
            before_call = model.call_count
            task_ids.append(agent.active_round.task_id)
            if before_call == 1:
                entered.set()
                await released.wait()
            async for chunk in original_stream(*args, **kwargs):
                yield chunk
            state = agent.load_state(session)
            state.task_plan.mark_completed("t1" if before_call == 0 else "t2", "done")
            agent.save_state(session, state)

        model.stream = blocked_stream
        agent = create_deep_agent(
            model=_build_mock_model(model), system_prompt="Test", enable_task_loop=True, max_iterations=6
        )
        await agent.start(session=session)
        stream = await agent.attach_output()

        async def consume():
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        await agent.send_input(SendInputRequest(request_id="original", inputs={"query": "two tasks"}))
        await asyncio.wait_for(entered.wait(), 10)
        assert task_ids[0] != task_ids[1]
        assert agent.get_active_steering_request_id() == "original"
        result = await agent.steer_active(
            active_request_id="original", input_id="late-task", content="correct step two"
        )
        assert result["status"] == "accepted"
        released.set()
        await asyncio.wait_for(consumer, 10)
        assert model.call_count == 3
        assert task_ids[1] == task_ids[2]
        assert (await agent.get_steering_status(active_request_id="original", input_id="late-task"))[
            "status"
        ] == "consumed"
    finally:
        released.set()
        if agent is not None:
            await agent.stop()
        if consumer is not None:
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()
