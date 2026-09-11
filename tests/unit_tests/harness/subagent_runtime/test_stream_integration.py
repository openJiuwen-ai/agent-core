# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for subagent stream execution with real DeepAgent and checkpointer."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.core.session.stream.base import OutputSchema, StreamMode
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.control import SubagentControl
from openjiuwen.harness.subagent_runtime.ids import new_task_id
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind, UserInputOp
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from tests.unit_tests.harness.test_deep_agent import FakeReactAgent


def _create_dummy_model() -> Model:
    model_client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="test-key",
        api_base="http://test-base",
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model="test-model")
    return Model(model_client_config=model_client_config, model_config=model_config)


class CheckpointStreamingReactAgent(FakeReactAgent):
    """Fake ReActAgent that follows the agent-session stream + commit path."""

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.invoke_calls.append({"inputs": inputs, "session": session})
        turn = len(self.invoke_calls)
        query = inputs.get("query", "")
        return {
            "output": f"turn-{turn}:{query}",
            "result_type": "answer",
        }

    async def stream(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        self.stream_calls.append(
            {
                "inputs": inputs,
                "session": session,
                "stream_modes": stream_modes,
            }
        )

        async def stream_process() -> None:
            try:
                result = await self.invoke(inputs, session=session)
                await self.write_invoke_result_to_stream(result, session)
            finally:
                if session is not None:
                    await session.close_stream()
                    await session.commit()

        if session is not None and hasattr(session, "stream_iterator"):
            task = asyncio.create_task(stream_process())
            async for result in session.stream_iterator():
                yield result
            await task
            return

        result = await self.invoke(inputs, session=session)
        yield OutputSchema(
            type="answer",
            index=0,
            payload={
                "output": result.get("output", ""),
                "result_type": result.get("result_type", "answer"),
            },
        )


class ReactInjectingParent:
    """Wrap a parent DeepAgent and inject a streaming react agent on create_subagent."""

    def __init__(self, parent: Any, react_agent: FakeReactAgent) -> None:
        self._parent = parent
        self._react_agent = react_agent

    def create_subagent(
        self,
        subagent_type: str,
        subsession_id: str,
        browser_capabilities: list[str] | None = None,
    ) -> Any:
        subagent = self._parent.create_subagent(
            subagent_type,
            subsession_id,
            browser_capabilities,
        )
        subagent.set_react_agent(self._react_agent, initialized=True)
        return subagent

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parent, name)


@pytest.fixture
def isolated_checkpointer():
    original = CheckpointerFactory.get_checkpointer()
    checkpointer = InMemoryCheckpointer()
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    try:
        yield checkpointer
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


@pytest_asyncio.fixture
async def runner_lifecycle():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


def _minimal_subagent_config() -> SubAgentConfig:
    return SubAgentConfig(
        agent_card=AgentCard(name="worker", description="integration worker"),
        system_prompt="You are a worker.",
        enable_task_loop=False,
    )


async def _build_parent(tmp_path, react_agent: CheckpointStreamingReactAgent) -> ReactInjectingParent:
    parent = create_deep_agent(
        model=_create_dummy_model(),
        card=AgentCard(name="parent", description="integration parent"),
        system_prompt="parent prompt",
        subagents=[_minimal_subagent_config()],
        workspace=str(tmp_path),
        enable_task_loop=False,
    )
    return ReactInjectingParent(parent, react_agent)


async def _run_turn(instance, *, query: str) -> None:
    task_id = new_task_id()
    await instance.enqueue(UserInputOp(query=query, task_id=task_id))
    deadline = time.monotonic() + 5.0
    while True:
        status = instance.agent_status()
        if instance.last_task_id == task_id and status.is_final():
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"turn did not finish: {status}")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_deep_agent_stream_turn_via_session_manager(
    tmp_path,
    isolated_checkpointer,
    runner_lifecycle,
) -> None:
    react = CheckpointStreamingReactAgent()
    parent = await _build_parent(tmp_path, react)
    manager = SubagentSessionManager(
        parent,
        SubagentRuntimeConfig(),
        asyncio.Semaphore(5),
    )
    subagent_id = "parent_sub_worker"

    instance = await manager.create(
        subagent_type="worker",
        subagent_id=subagent_id,
        parent_session_id="parent_sess",
        display_name="Worker",
        role="worker",
    )
    try:
        await _run_turn(instance, query="hello")

        assert instance.agent_status().kind is SubagentStatusKind.COMPLETED
        assert instance.last_output == "turn-1:hello"
        assert len(react.stream_calls) == 1
        assert react.stream_calls[0]["inputs"]["query"] == "hello"
        assert react.stream_calls[0]["inputs"]["conversation_id"] == subagent_id
        assert await isolated_checkpointer.session_exists(subagent_id) is True
    finally:
        await manager.remove(subagent_id, reason="test_cleanup")


@pytest.mark.asyncio
async def test_deep_agent_stream_multi_turn_new_session_same_id(
    tmp_path,
    isolated_checkpointer,
    runner_lifecycle,
) -> None:
    react = CheckpointStreamingReactAgent()
    parent = await _build_parent(tmp_path, react)
    manager = SubagentSessionManager(
        parent,
        SubagentRuntimeConfig(),
        asyncio.Semaphore(5),
    )
    subagent_id = "parent_sub_worker"

    instance = await manager.create(
        subagent_type="worker",
        subagent_id=subagent_id,
        parent_session_id="parent_sess",
        display_name="Worker",
        role="worker",
    )
    try:
        await _run_turn(instance, query="first")
        assert instance.last_output == "turn-1:first"
        assert await isolated_checkpointer.session_exists(subagent_id) is True

        await _run_turn(instance, query="second")
        assert instance.last_output == "turn-2:second"
        assert len(react.stream_calls) == 2
        assert len(react.invoke_calls) == 2

        first_session = react.stream_calls[0]["session"]
        second_session = react.stream_calls[1]["session"]
        assert first_session is not second_session
        assert first_session.get_session_id() == subagent_id
        assert second_session.get_session_id() == subagent_id
    finally:
        await manager.remove(subagent_id, reason="test_cleanup")


@pytest.mark.asyncio
async def test_control_spawn_wait_uses_deep_agent_stream(
    tmp_path,
    isolated_checkpointer,
    runner_lifecycle,
) -> None:
    react = CheckpointStreamingReactAgent()
    parent = await _build_parent(tmp_path, react)
    control = SubagentControl(
        parent,
        "parent_sess",
        config=SubagentRuntimeConfig(),
    )

    with patch(
        "openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN",
        100,
    ):
        try:
            spawn_result = await control.spawn("worker", "via-control")
            wait_result = await control.wait([spawn_result.subagent_id], timeout_ms=5000)

            assert wait_result.timed_out is False
            status = wait_result.statuses[spawn_result.subagent_id]
            assert status.kind is SubagentStatusKind.COMPLETED
            assert status.message == "turn-1:via-control"
            assert wait_result.results[spawn_result.subagent_id] == "turn-1:via-control"
            assert len(react.stream_calls) == 1
            assert await isolated_checkpointer.session_exists(spawn_result.subagent_id) is True
        finally:
            for sid in list(control._manager.list_ids()):
                await control._manager.remove(sid, reason="test_cleanup")
                control._registry.release(sid)
