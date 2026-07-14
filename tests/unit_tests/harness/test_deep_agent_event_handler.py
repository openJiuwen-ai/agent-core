# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskLoopEventHandler."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from openjiuwen.core.controller.modules.event_handler import (
    EventHandlerInput,
)
from openjiuwen.core.controller.schema.event import (
    InputEvent,
    TaskFailedEvent,
    TaskInteractionEvent,
)
from openjiuwen.core.controller.schema.dataframe import (
    TextDataFrame,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.task_loop.task_loop_event_handler import (
    TaskLoopEventHandler,
)
from openjiuwen.harness.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
)
from openjiuwen.harness.task_loop.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.harness.schema.config import (
    DeepAgentConfig,
)
from openjiuwen.harness.task_loop.loop_queues import (
    LoopQueues,
)


class FakeController:
    """Minimal Controller stub."""

    def __init__(self) -> None:
        self.event_handler = None
        self.event_queue = None

    async def stop(self) -> None:
        pass


class FakeSession:
    """Minimal session stub."""

    def __init__(self, sid: str = "s1") -> None:
        self._state: Dict[str, Any] = {}
        self._sid = sid

    def get_session_id(self) -> str:
        return self._sid

    def get_state(self, key: Any = None) -> Any:
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict) -> None:
        self._state.update(data)


class FakeReactAgent:
    """Tracks invoke calls."""

    def __init__(self) -> None:
        self.invoke_calls: List[Dict[str, Any]] = []
        self.agent_callback_manager = (
            FakeCallbackManager()
        )

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Any = None,
    ) -> Dict[str, Any]:
        self.invoke_calls.append(inputs)
        return {
            "output": f"done:{inputs['query']}",
        }

    async def register_callback(
        self, *args: Any, **kwargs: Any
    ) -> None:
        pass


class FakeCallbackManager:
    """Minimal callback manager stub."""

    async def execute(
        self,
        event: AgentCallbackEvent,
        ctx: Any,
    ) -> None:
        pass

    async def unregister_rail(
        self, rail: Any, agent: Any
    ) -> None:
        pass


class FakeTaskManager:
    """Tracks add_task calls."""

    def __init__(self) -> None:
        self.added_tasks: List[Any] = []

    async def add_task(self, task: Any) -> None:
        self.added_tasks.append(task)


def _make_agent() -> DeepAgent:
    """Build a DeepAgent with fake react agent."""
    agent = DeepAgent(
        AgentCard(name="test", description="t")
    )
    agent.configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    fake = FakeReactAgent()
    agent.set_react_agent(fake, initialized=True)
    coordinator = LoopCoordinator()
    coordinator.reset()
    fake_ctrl = FakeController()
    fake_ctrl.event_handler = None
    agent._loop_coordinator = coordinator
    agent._loop_controller = fake_ctrl
    agent._loop_session = None
    return agent


@pytest.mark.asyncio
async def test_handle_input_creates_task() -> None:
    """handle_input creates a core Task via TaskManager instead of direct invoke."""
    agent = _make_agent()
    handler = TaskLoopEventHandler(agent)

    # Inject fake task_manager
    fake_tm = FakeTaskManager()
    handler._task_manager = fake_tm

    event = InputEvent.from_user_input(
        "hello world"
    )
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    # Prepare a round so handle_input has a Future
    round_id = handler.prepare_round()
    event.metadata = event.metadata or {}
    event.metadata["_handler_round_id"] = round_id

    # handle_input is now fire-and-forget (returns ack)
    ack = await handler.handle_input(inputs)

    # Simulate completion signal in background
    handler._resolve_future(
        {"output": "done:hello world"}, round_id,
    )

    result = await handler.wait_completion(timeout=1.0)

    # Verify task was created
    assert len(fake_tm.added_tasks) == 1
    core_task = fake_tm.added_tasks[0]
    assert core_task.task_type == DEEP_TASK_TYPE
    assert core_task.description == "hello world"
    assert core_task.status.value == "submitted"

    # Verify result
    assert result is not None
    assert result["output"] == "done:hello world"
    assert handler.last_result is result
    assert ack["status"] == "submitted"

    coord = agent.loop_coordinator
    assert coord is not None
    assert coord.current_iteration == 0


@pytest.mark.asyncio
async def test_handle_input_no_coordinator() -> None:
    """handle_input returns None without coordinator."""
    agent = DeepAgent(
        AgentCard(name="test", description="t")
    )
    agent.configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    agent.set_react_agent(
        FakeReactAgent(), initialized=True
    )
    # No loop_coordinator set

    handler = TaskLoopEventHandler(agent)
    event = InputEvent.from_user_input("test")
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    result = await handler.handle_input(inputs)
    assert result is not None
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_handle_task_interaction() -> None:
    """handle_task_interaction pushes to steering queue."""
    agent = _make_agent()
    handler = TaskLoopEventHandler(agent)
    queues = LoopQueues()
    handler.interaction_queues = queues

    event = TaskInteractionEvent(
        interaction=[
            TextDataFrame(text="change plan")
        ]
    )
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    result = await handler.handle_task_interaction(
        inputs
    )
    assert result is not None
    assert result["status"] == "steer_injected"
    assert "change plan" in result["msg"]

    # Verify message was pushed to steering queue
    msgs = queues.drain_steering()
    assert len(msgs) == 1
    assert msgs[0] == "change plan"


@pytest.mark.asyncio
async def test_handle_task_completion_signals() \
        -> None:
    """handle_task_completion resolves the per-round Future."""
    agent = _make_agent()
    handler = TaskLoopEventHandler(agent)

    round_id = handler.prepare_round()

    from openjiuwen.core.controller.schema.event import (
        TaskCompletionEvent,
    )
    comp_event = TaskCompletionEvent(
        task_result=[],
        metadata={
            "task_id": "t1",
            "_handler_round_id": round_id,
        },
    )
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=comp_event, session=session
    )

    result = await handler.handle_task_completion(
        inputs
    )
    assert result is not None
    assert result["status"] == "completed"

    # Future should be resolved
    fut_result = await handler.wait_completion(
        timeout=1.0
    )
    assert fut_result == {"status": "completed"}


@pytest.mark.asyncio
async def test_handle_task_failed_signals() -> None:
    """handle_task_failed resolves the per-round
    Future with error.

    Note: handler no longer writes TaskPlan state
    (that is now solely the executor's job).
    """
    agent = _make_agent()
    handler = TaskLoopEventHandler(agent)

    round_id = handler.prepare_round()

    event = TaskFailedEvent(
        error_message="timeout",
        metadata={
            "task_id": "t2",
            "_handler_round_id": round_id,
        },
    )
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    result = await handler.handle_task_failed(
        inputs
    )
    assert result is not None
    assert result["status"] == "failed"

    # Future should be resolved with error
    fut_result = await handler.wait_completion(
        timeout=1.0
    )
    assert fut_result["error"] == "timeout"


@pytest.mark.asyncio
async def test_handle_input_waits_for_completion() \
        -> None:
    """handle_input submits task; wait_completion blocks until Future is resolved."""
    agent = _make_agent()
    handler = TaskLoopEventHandler(agent)
    handler._task_manager = FakeTaskManager()

    event = InputEvent.from_user_input("wait test")
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    round_id = handler.prepare_round()
    event.metadata = event.metadata or {}
    event.metadata["_handler_round_id"] = round_id

    completed = False

    async def _delayed_signal():
        nonlocal completed
        await asyncio.sleep(0.1)
        handler._resolve_future(
            {"output": "waited"}, round_id,
        )
        completed = True

    signal_task = asyncio.create_task(
        _delayed_signal()
    )

    ack = await handler.handle_input(inputs)
    assert ack["status"] == "submitted"

    result = await handler.wait_completion(
        timeout=2.0
    )
    await signal_task

    assert completed is True
    assert result["output"] == "waited"
