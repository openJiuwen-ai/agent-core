# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""System tests for TaskPlanningRail model selection feature.

Uses MockLLMModel to simulate LLM responses without real API calls.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import OperationMode, SysOperationCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema import ModelUsageRecord, TodoItem, TodoStatus
from openjiuwen.harness.workspace.workspace import Workspace

logger = logging.getLogger(__name__)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_operation():
    card = SysOperationCard(id="test_sys_rail_op", mode=OperationMode.LOCAL)
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


from openjiuwen.core.foundation.llm import Model


def _make_mock_model(client_id: str) -> Model:
    """Create a mock Model with model_client_config.client_id."""
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-key",
            api_base="mock-base",
            client_id=client_id,
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )


def _make_rail_with_models(fast_model, smart_model) -> TaskPlanningRail:
    op = _make_operation()
    rail = TaskPlanningRail(
        model_selection={
            fast_model: "cheap model for simple tasks",
            smart_model: "premium model for complex tasks",
        }
    )
    rail.set_sys_operation(op)
    return rail


def _make_agent(workspace_path: str) -> DeepAgent:
    agent = DeepAgent(AgentCard(name="test-deep", description="test"))
    agent.configure(
        DeepAgentConfig(
            enable_task_loop=True,
            workspace=Workspace(root_path=workspace_path),
        )
    )
    return agent


def _make_todo(title: str, status: TodoStatus,
               selected_model_id: str = None) -> TodoItem:
    return TodoItem.create(
        content=title,
        status=status,
        selected_model_id=selected_model_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_selection_switches_model_for_in_progress_task() -> None:
    """before_model_call switches to the model matching the in_progress task's selected_model_id."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")

    with tempfile.TemporaryDirectory() as tmpdir:
        rail = _make_rail_with_models(fast_model, smart_model)
        agent = _make_agent(tmpdir)
        rail.init(agent)

        todos = [
            _make_todo("Translate document", TodoStatus.IN_PROGRESS, "fast"),
            _make_todo("Analyze architecture", TodoStatus.PENDING, "smart"),
        ]

        tool = rail._find_todo_tool()
        assert tool is not None
        tool.load_todos = AsyncMock(return_value=todos)

        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.session.get_session_id.return_value = "sys-test-session"
        ctx.agent.set_llm = MagicMock()
        ctx.agent._llm = MagicMock()
        rail.system_prompt_builder = MagicMock()
        rail.system_prompt_builder.language = "en"

        await rail.before_model_call(ctx)

        ctx.agent.set_llm.assert_called_once_with(fast_model)


@pytest.mark.asyncio
async def test_model_selection_switches_to_smart_model() -> None:
    """before_model_call switches to smart model when in_progress task uses it."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")

    with tempfile.TemporaryDirectory() as tmpdir:
        rail = _make_rail_with_models(fast_model, smart_model)
        agent = _make_agent(tmpdir)
        rail.init(agent)

        todos = [
            _make_todo("Translate document", TodoStatus.COMPLETED, "fast"),
            _make_todo("Analyze architecture", TodoStatus.IN_PROGRESS, "smart"),
        ]

        tool = rail._find_todo_tool()
        tool.load_todos = AsyncMock(return_value=todos)

        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.session.get_session_id.return_value = "sys-test-session-2"
        ctx.agent.set_llm = MagicMock()
        ctx.agent._llm = MagicMock()
        rail.system_prompt_builder = MagicMock()
        rail.system_prompt_builder.language = "en"

        await rail.before_model_call(ctx)

        ctx.agent.set_llm.assert_called_once_with(smart_model)


@pytest.mark.asyncio
async def test_usage_records_accumulated_across_calls() -> None:
    """after_model_call accumulates token usage across multiple calls."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")

    with tempfile.TemporaryDirectory() as tmpdir:
        rail = _make_rail_with_models(fast_model, smart_model)
        agent = _make_agent(tmpdir)
        rail.init(agent)

        def _make_ctx_with_usage(model_id: str, input_tok: int, output_tok: int):
            usage = MagicMock()
            usage.input_tokens = input_tok
            usage.output_tokens = output_tok
            response = MagicMock()
            response.usage_metadata = usage
            ctx = MagicMock()
            ctx.session = MagicMock()
            ctx.session.get_session_id.return_value = "sys-usage-session"
            ctx.inputs = MagicMock()
            ctx.inputs.response = response
            ctx.agent._llm = _make_mock_model(model_id)
            return ctx

        await rail.after_model_call(_make_ctx_with_usage("fast", 100, 50))
        await rail.after_model_call(_make_ctx_with_usage("fast", 200, 80))
        await rail.after_model_call(_make_ctx_with_usage("smart", 500, 300))

        assert rail._usage_records["fast"].input_tokens == 300
        assert rail._usage_records["fast"].output_tokens == 130
        assert rail._usage_records["smart"].input_tokens == 500
        assert rail._usage_records["smart"].output_tokens == 300


@pytest.mark.asyncio
async def test_after_invoke_logs_and_resets_usage() -> None:
    """after_invoke logs usage summary and resets _usage_records to empty."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")

    with tempfile.TemporaryDirectory() as tmpdir:
        rail = _make_rail_with_models(fast_model, smart_model)
        agent = _make_agent(tmpdir)
        rail.init(agent)

        rail._usage_records = {
            "fast": ModelUsageRecord(model_id="fast", input_tokens=300, output_tokens=130),
            "smart": ModelUsageRecord(model_id="smart", input_tokens=500, output_tokens=300),
        }

        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.session.get_session_id.return_value = "sys-invoke-session"

        with patch.object(logger.__class__, "info") as _mock_log:
            await rail.after_invoke(ctx)

        assert rail._usage_records == {}


@pytest.mark.asyncio
async def test_model_selection_system_prompt_includes_model_ids() -> None:
    """build_todo_section with model_selection includes model IDs in the prompt."""
    from openjiuwen.harness.prompts.sections.todo import build_todo_section

    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")
    model_selection = {
        fast_model: "cheap model",
        smart_model: "premium model",
    }

    section = build_todo_section(language="en", model_selection=model_selection)
    assert section is not None
    content = section.content.get("en", "")
    assert "fast" in content
    assert "smart" in content
    assert "Model Selection" in content


@pytest.mark.asyncio
async def test_no_model_switch_when_no_in_progress_task() -> None:
    """before_model_call does not crash when no task is in_progress."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")

    with tempfile.TemporaryDirectory() as tmpdir:
        rail = _make_rail_with_models(fast_model, smart_model)
        agent = _make_agent(tmpdir)
        rail.init(agent)

        todos = [
            _make_todo("task-a", TodoStatus.PENDING, "fast"),
        ]
        tool = rail._find_todo_tool()
        tool.load_todos = AsyncMock(return_value=todos)

        default_model = MagicMock()
        rail._default_llm = default_model

        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.session.get_session_id.return_value = "sys-no-inprogress"
        ctx.agent.set_llm = MagicMock()
        ctx.agent._llm = MagicMock()
        rail.system_prompt_builder = MagicMock()
        rail.system_prompt_builder.language = "en"

        await rail.before_model_call(ctx)

        ctx.agent.set_llm.assert_called_once_with(default_model)
