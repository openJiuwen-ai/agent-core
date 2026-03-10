# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgentEventHandler."""
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
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.deep_agent_event_handler import (
    DeepAgentEventHandler,
)
from openjiuwen.deepagents.deep_agent_event_executor import (
    DEEP_TASK_TYPE,
)
from openjiuwen.deepagents.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.deepagents.schema.config import (
    DeepAgentConfig,
)
from openjiuwen.deepagents.schema.state import (
    DeepAgentState,
)
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)


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
    agent._loop_coordinator = LoopCoordinator()
    agent._loop_coordinator.reset()
    return agent


@pytest.mark.asyncio
async def test_handle_input_creates_task() -> None:
    """handle_input creates a core Task via
    TaskManager instead of direct invoke."""
    agent = _make_agent()
    handler = DeepAgentEventHandler(agent)

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

    # Simulate completion signal in background
    async def _signal_completion():
        await asyncio.sleep(0.05)
        handler._completion_result = {
            "output": "done:hello world",
        }
        handler._completion_event.set()

    task = asyncio.create_task(
        _signal_completion()
    )

    result = await handler.handle_input(inputs)
    await task

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

    coord = agent.loop_coordinator
    assert coord is not None
    assert coord.current_iteration == 0


@pytest.mark.asyncio
async def test_handle_input_no_coordinator() -> None:
    """handle_input returns None without
    coordinator."""
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

    handler = DeepAgentEventHandler(agent)
    event = InputEvent.from_user_input("test")
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    result = await handler.handle_input(inputs)
    assert result is None


@pytest.mark.asyncio
async def test_handle_task_interaction() -> None:
    """handle_task_interaction returns ack."""
    agent = _make_agent()
    handler = DeepAgentEventHandler(agent)

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
    assert result["status"] == "steer_acknowledged"
    assert "change plan" in result["msg"]


@pytest.mark.asyncio
async def test_handle_task_completion_signals() \
        -> None:
    """handle_task_completion sets the completion
    event."""
    agent = _make_agent()
    handler = DeepAgentEventHandler(agent)

    event = TaskFailedEvent(
        error_message="timeout",
        metadata={"task_id": "t2"},
    )
    # Use a completion event instead
    from openjiuwen.core.controller.schema.event import (
        TaskCompletionEvent,
    )
    comp_event = TaskCompletionEvent(
        task_result=[],
        metadata={"task_id": "t1"},
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
    assert handler._completion_event.is_set()


@pytest.mark.asyncio
async def test_handle_task_failed_signals() -> None:
    """handle_task_failed sets the completion event
    and updates TaskPlan."""
    agent = _make_agent()
    handler = DeepAgentEventHandler(agent)

    plan = TaskPlan(
        goal="test",
        tasks=[TaskItem(id="t2", title="step 2")],
    )
    session = FakeSession()
    from openjiuwen.deepagents.schema.state import (
        _write_runtime_state,
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    event = TaskFailedEvent(
        error_message="timeout",
        metadata={"task_id": "t2"},
    )
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    result = await handler.handle_task_failed(
        inputs
    )
    assert result is not None
    assert result["status"] == "failed"
    assert handler._completion_event.is_set()
    assert (
        handler._completion_result["error"]
        == "timeout"
    )

    task = plan.get_task("t2")
    assert task is not None
    assert task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_handle_input_waits_for_completion() \
        -> None:
    """handle_input blocks until completion event
    is signalled."""
    agent = _make_agent()
    handler = DeepAgentEventHandler(agent)
    handler._task_manager = FakeTaskManager()

    event = InputEvent.from_user_input("wait test")
    session = FakeSession()
    inputs = EventHandlerInput.model_construct(
        event=event, session=session
    )

    completed = False

    async def _delayed_signal():
        nonlocal completed
        await asyncio.sleep(0.1)
        handler._completion_result = {
            "output": "waited",
        }
        handler._completion_event.set()
        completed = True

    signal_task = asyncio.create_task(
        _delayed_signal()
    )

    result = await handler.handle_input(inputs)
    await signal_task

    assert completed is True
    assert result["output"] == "waited"
