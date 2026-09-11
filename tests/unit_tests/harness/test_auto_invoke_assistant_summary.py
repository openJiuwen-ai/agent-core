# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for spawn completion auto-invoke behavior."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
from openjiuwen.core.controller.schema.event import TaskCompletionEvent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE
from openjiuwen.harness.tools.subagent.session_tools import SessionToolkit


def _make_agent_for_auto_invoke() -> DeepAgent:
    agent = DeepAgent.__new__(DeepAgent)
    agent._invoke_active = False
    agent._loop_session = SimpleNamespace(get_session_id=lambda: "parent-session")
    agent._auto_invoke_scheduled = False
    agent.invoke = AsyncMock(return_value={"output": "summary"})
    return agent


class _IdleParentStub:
    def __init__(self) -> None:
        self.deep_config = SimpleNamespace(language="cn")
        self._auto_invoke_scheduled = False
        self._invoke_active = False
        self.schedule_auto_invoke_on_spawn_done = AsyncMock()

    @property
    def is_invoke_active(self) -> bool:
        return self._invoke_active

    @property
    def is_auto_invoke_scheduled(self) -> bool:
        return self._auto_invoke_scheduled

    def set_auto_invoke_scheduled(self, is_scheduled: bool) -> None:
        self._auto_invoke_scheduled = is_scheduled


@pytest.mark.asyncio
async def test_schedule_auto_invoke_on_spawn_done_invokes_when_idle() -> None:
    agent = _make_agent_for_auto_invoke()

    await agent.schedule_auto_invoke_on_spawn_done("summary prompt", delay=0)

    agent.invoke.assert_awaited_once()
    invoke_inputs = agent.invoke.await_args.args[0]
    assert invoke_inputs["query"] == "summary prompt"


@pytest.mark.asyncio
async def test_schedule_auto_invoke_skips_when_invoke_active() -> None:
    agent = _make_agent_for_auto_invoke()
    agent._invoke_active = True

    await agent.schedule_auto_invoke_on_spawn_done("summary prompt", delay=0)

    agent.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_session_spawn_schedules_auto_invoke_when_idle() -> None:
    toolkit = SessionToolkit()
    toolkit.upsert_running("t1", "sub-session", "task A")

    agent = _IdleParentStub()
    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)

    event = TaskCompletionEvent(
        task_result=[JsonDataFrame(data={"output": "result A"})],
        metadata={
            "task_id": "t1",
            "task_type": SESSION_SPAWN_TASK_TYPE,
            "task_description": "task A",
        },
    )
    await handler._complete_session_spawn(
        "t1",
        EventHandlerInput.model_construct(event=event, session=MagicMock()),
        is_error=False,
    )

    agent.schedule_auto_invoke_on_spawn_done.assert_called_once()
    steer_text = agent.schedule_auto_invoke_on_spawn_done.call_args.args[0]
    assert "result A" in steer_text
    assert toolkit.get("t1") is not None
    assert toolkit.get("t1").status == "completed"
