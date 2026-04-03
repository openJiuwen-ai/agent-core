# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskLoopEventExecutor."""
# pylint: disable=protected-access
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from openjiuwen.core.controller.config import (
    ControllerConfig,
)
from openjiuwen.core.controller.modules.event_queue import (
    EventQueue,
)
from openjiuwen.core.controller.modules.task_manager import (
    TaskManager,
)
from openjiuwen.core.controller.modules.task_scheduler import (
    TaskExecutorDependencies,
)
from openjiuwen.core.controller.schema.event import (
    EventType,
)
from openjiuwen.core.controller.schema.task import (
    Task as CoreTask,
    TaskStatus as CoreTaskStatus,
)
from openjiuwen.core.context_engine import (
    ContextEngine,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.harness.deep_agent import (
    DeepAgent,
)
from openjiuwen.harness.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
    TaskLoopEventExecutor,
    build_deep_executor,
)
from openjiuwen.harness.task_loop.task_loop_event_handler import (
    TaskLoopEventHandler,
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


class FakeSession:
    """Minimal session stub."""

    def __init__(
        self, sid: str = "s1"
    ) -> None:
        self._state: Dict[str, Any] = {}
        self._sid = sid

    def get_session_id(self) -> str:
        return self._sid

    def get_state(
        self, key: Any = None
    ) -> Any:
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(
        self, data: dict
    ) -> None:
        self._state.update(data)


class FakeReactAgent:
    """Tracks invoke calls."""

    def __init__(
        self, fail: bool = False
    ) -> None:
        self.invoke_calls: List[
            Dict[str, Any]
        ] = []
        self.agent_callback_manager = (
            FakeCallbackManager()
        )
        self._fail = fail

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.invoke_calls.append(inputs)
        if self._fail:
            raise RuntimeError("invoke failed")
        return {
            "output": (
                f"done:{inputs['query']}"
            ),
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


def _make_deps(
    config: Optional[ControllerConfig] = None,
) -> Tuple[
    TaskExecutorDependencies, TaskManager
]:
    """Build dependencies with a real
    TaskManager."""
    cfg = config or ControllerConfig()
    tm = TaskManager(config=cfg)
    eq = EventQueue(cfg)
    ce = ContextEngine()

    class FakeAbilityManager:
        pass

    deps = TaskExecutorDependencies(
        config=cfg,
        ability_manager=FakeAbilityManager(),
        context_engine=ce,
        task_manager=tm,
        event_queue=eq,
    )
    return deps, tm


def _make_agent(
    fail: bool = False,
) -> DeepAgent:
    """Build a DeepAgent with fake react agent."""
    agent = DeepAgent(
        AgentCard(name="test", description="t")
    )
    agent.configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    fake = FakeReactAgent(fail=fail)
    agent.set_react_agent(
        fake, initialized=True
    )
    coordinator = LoopCoordinator()
    coordinator.reset()
    handler = TaskLoopEventHandler(agent)
    handler.interaction_queues = LoopQueues()

    class FakeController:
        """Minimal Controller stub."""
        def __init__(self) -> None:
            self.event_handler = handler
            self.event_queue = None

        async def stop(self) -> None:
            pass

    agent._loop_coordinator = coordinator
    agent._loop_controller = FakeController()
    agent._loop_session = None
    return agent


@pytest.mark.asyncio
async def test_executor_init_with_deps() -> None:
    """Executor correctly calls super().__init__ with dependencies."""
    agent = _make_agent()
    deps, tm = _make_deps()

    executor = TaskLoopEventExecutor(
        deps, agent
    )

    assert executor._deep_agent is agent
    assert executor._task_manager is tm
    assert executor._config is deps.config


@pytest.mark.asyncio
async def test_execute_ability_yields_completion() \
        -> None:
    """Normal execution yields a TASK_COMPLETION chunk."""
    agent = _make_agent()
    deps, tm = _make_deps()

    # Add a core task so executor can find it
    session = FakeSession()
    core_task = CoreTask(
        session_id="s1",
        task_id="t1",
        task_type=DEEP_TASK_TYPE,
        description="hello world",
        status=CoreTaskStatus.SUBMITTED,
    )
    await tm.add_task(core_task)

    executor = TaskLoopEventExecutor(
        deps, agent
    )

    chunks = []
    async for chunk in executor.execute_ability(
        "t1", session
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert (
        chunks[0].payload.type
        == EventType.TASK_COMPLETION
    )
    meta = chunks[0].payload.metadata
    assert meta["task_id"] == "t1"
    result_data = chunks[0].payload.data[0].data
    assert "done:hello world" in (
        result_data["output"]
    )

    coord = agent.loop_coordinator
    assert coord is not None


@pytest.mark.asyncio
async def test_execute_ability_yields_failure() \
        -> None:
    """Exception in invoke yields a TASK_FAILED chunk."""
    agent = _make_agent(fail=True)
    deps, tm = _make_deps()

    session = FakeSession()
    core_task = CoreTask(
        session_id="s1",
        task_id="t2",
        task_type=DEEP_TASK_TYPE,
        description="will fail",
        status=CoreTaskStatus.SUBMITTED,
    )
    await tm.add_task(core_task)

    executor = TaskLoopEventExecutor(
        deps, agent
    )

    chunks = []
    async for chunk in executor.execute_ability(
        "t2", session
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert (
        chunks[0].payload.type
        == EventType.TASK_FAILED
    )
    meta = chunks[0].payload.metadata
    assert meta["task_id"] == "t2"
    error_text = chunks[0].payload.data[0].text
    assert "invoke failed" in error_text


@pytest.mark.asyncio
async def test_cancel_marks_failed_and_aborts() \
        -> None:
    """cancel() marks TaskPlan failed and requests abort."""
    agent = _make_agent()
    deps, _ = _make_deps()

    executor = TaskLoopEventExecutor(
        deps, agent
    )
    session = FakeSession()

    result = await executor.cancel(
        "t1", session
    )
    assert result is True
    assert agent.loop_coordinator.is_aborted


@pytest.mark.asyncio
async def test_build_deep_executor_factory() \
        -> None:
    """build_deep_executor returns a callable that creates executors."""
    agent = _make_agent()
    deps, _ = _make_deps()

    builder = build_deep_executor(agent)
    executor = builder(deps)

    assert isinstance(
        executor, TaskLoopEventExecutor
    )
    assert executor._deep_agent is agent
