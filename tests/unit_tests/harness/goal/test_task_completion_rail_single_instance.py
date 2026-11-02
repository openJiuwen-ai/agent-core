# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""start() must reuse the queued TaskCompletionRail instead of registering a second one."""
from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail


def _model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )


def _task_completion_rails(agent) -> list[TaskCompletionRail]:
    return [rail for rail in agent.configured_rails() if isinstance(rail, TaskCompletionRail)]


@pytest.mark.asyncio
async def test_start_reuses_the_queued_task_completion_rail() -> None:
    agent = create_deep_agent(model=_model(), auto_create_workspace=False, enable_task_loop=True)
    queued = _task_completion_rails(agent)
    assert len(queued) == 1

    await agent.start()
    try:
        await agent._ensure_initialized()
        rails = _task_completion_rails(agent)
        assert rails == queued
        assert agent._task_completion_rail is queued[0]
        assert queued[0]._goal_manager is agent.goal_manager
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_start_keeps_a_user_provided_task_completion_rail() -> None:
    agent = create_deep_agent(model=_model(), auto_create_workspace=False, enable_task_loop=True)
    custom = TaskCompletionRail(goal_language="en")
    agent.add_rail(custom)

    await agent.start()
    try:
        await agent._ensure_initialized()
        assert _task_completion_rails(agent) == [custom]
        assert agent._task_completion_rail is custom
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_start_without_task_loop_still_registers_a_task_completion_rail() -> None:
    agent = create_deep_agent(model=_model(), auto_create_workspace=False)
    assert not _task_completion_rails(agent)

    await agent.start()
    try:
        await agent._ensure_initialized()
        rails = _task_completion_rails(agent)
        assert len(rails) == 1
        assert agent._task_completion_rail is rails[0]
    finally:
        await agent.stop()
