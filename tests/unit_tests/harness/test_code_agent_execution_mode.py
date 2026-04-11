# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for plan mode execution using mock LLM-driven invoke (no real API)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, cast
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


class _MockRuntimeModel:
    """Expose MockLLMModel through DeepAgent public model contract."""

    def __init__(self, client: MockLLMModel) -> None:
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args, **kwargs):
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args, **kwargs):
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_mock_runtime_model(mock_llm: MockLLMModel) -> Model:
    return cast(Model, _MockRuntimeModel(mock_llm))


class ToolTraceRail(AgentRail):
    """Record tool names seen in before_tool_call (same idea as execute_mode e2e)."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


@pytest_asyncio.fixture
async def runner():
    # Isolate from tests that leave Runner in distributed_mode / custom config.
    Runner.set_config(DEFAULT_RUNNER_CONFIG)
    await Runner.start()
    yield
    await Runner.stop()
    Runner.set_config(DEFAULT_RUNNER_CONFIG)


@pytest.mark.asyncio
async def test_plan_mode_mock_invoke_blocks_illegal_write_keeps_plan_write(
    tmp_path: Path,
    runner,
) -> None:
    """Mock：先 todo_list（硬拦），再 enter，非法写被拒，合法写 plan，exit。
    """
    _ = runner
    root = tmp_path.resolve()
    fixed_slug = "ut-mock-plan-slug-two"
    plan_path = root / ".plans" / f"{fixed_slug}.md"
    illegal_path = root / "illegal.txt"
    trace = ToolTraceRail()
    fs_rail = FileSystemRail()
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("todo_list", "{}", "call_todo_ut2"),
            create_tool_call_response("enter_plan_mode", "{}", "call_enter_ut2"),
            create_tool_call_response(
                "write_file",
                '{"file_path": "illegal.txt", "content": "SHOULD_NOT_WRITE"}',
                "call_write_bad_ut2",
            ),
            create_tool_call_response(
                "write_file",
                (
                    '{"file_path": "%s", "content": "# Plan\\n- ok"}'
                    % str(plan_path).replace("\\", "\\\\")
                ),
                "call_write_plan_ut2",
            ),
            create_tool_call_response("exit_plan_mode", "{}", "call_exit_ut2"),
            create_text_response("完成。"),
        ]
    )
    session = create_agent_session(session_id=f"plan_mock_{uuid.uuid4().hex}")
    agent = create_deep_agent(
        model=_build_mock_runtime_model(mock_llm),
        rails=[trace, fs_rail],
        enable_task_loop=True,
        max_iterations=20,
        workspace=str(root),
        enable_plan_mode=True,
        enable_task_planning=True,
    )
    with patch(
        "openjiuwen.harness.tools.plan_mode_tools.generate_word_slug",
        return_value=fixed_slug,
    ):
        agent.switch_mode(session, "plan")
        result = await agent.invoke({"query": "验证 plan 约束"}, session=session)
    assert result.get("result_type") == "answer"
    assert "todo_list" in trace.tool_calls
    assert "enter_plan_mode" in trace.tool_calls
    assert "exit_plan_mode" in trace.tool_calls
    assert not illegal_path.exists()
    assert plan_path.is_file()


@pytest.mark.asyncio
async def test_plan_mode_mock_enter_invokes_task_tool_registered(
    tmp_path: Path,
    runner,
) -> None:
    """enter_plan_mode 成功后 after_tool_call 应挂上 task_tool（内置子 agent 已注入）。"""
    _ = runner
    root = tmp_path.resolve()
    fixed_slug = "ut-mock-plan-slug-task"
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("enter_plan_mode", "{}", "call_enter_task"),
            create_text_response("done"),
        ]
    )
    session = create_agent_session(session_id=f"plan_mock_{uuid.uuid4().hex}")
    agent = create_deep_agent(
        model=_build_mock_runtime_model(mock_llm),
        enable_task_loop=True,
        max_iterations=8,
        workspace=str(root),
        enable_plan_mode=True,
    )
    with patch(
        "openjiuwen.harness.tools.plan_mode_tools.generate_word_slug",
        return_value=fixed_slug,
    ):
        agent.switch_mode(session, "plan")
        await agent.invoke({"query": "enter only"}, session=session)
    assert agent.ability_manager.get("task_tool") is not None
