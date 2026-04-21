# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Unit tests for TaskPlanningRail init/uninit."""
# pylint: disable=protected-access
from __future__ import annotations

from typing import Any, List
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    ToolCallInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import (
    SysOperationCard,
    OperationMode,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.prompts.sections.todo import (
    build_progress_reminder_user_prompt,
    build_todo_system_prompt, build_todo_section,
)
from openjiuwen.harness.rails.task_planning_rail import (
    TaskPlanningRail,
)
from openjiuwen.harness.schema.config import (
    DeepAgentConfig,
)
from openjiuwen.harness.schema.state import (
    DeepAgentState,
)
from openjiuwen.harness.schema.task import (
    ModelUsageRecord,
    TaskPlan,
    TodoItem,
    TodoStatus,
)


def _make_operation():
    """Create a SysOperation for tests."""
    card_id = "test_rail_op"
    card = SysOperationCard(
        id=card_id, mode=OperationMode.LOCAL
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(
        card.id
    )


def _make_rail() -> TaskPlanningRail:
    """Build a TaskPlanningRail with test defaults."""
    op = _make_operation()
    tpr = TaskPlanningRail()
    tpr.set_sys_operation(op)
    return tpr


def _make_agent(workspace: str = None) -> DeepAgent:
    """Build a DeepAgent with optional workspace."""
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    )
    workspace_obj = Workspace(root_path=workspace) if workspace else None
    agent.configure(
        DeepAgentConfig(
            enable_task_loop=True,
            workspace=workspace_obj,
        )
    )
    return agent


def test_init_registers_tools_with_workspace() -> None:
    """init registers todo tools when workspace is set."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/test_ws")
    rail.init(agent)

    assert rail.tools is not None
    assert len(rail.tools) > 0
    assert rail.workspace is not None
    assert rail.workspace.root_path == f"/tmp/test_ws"


def test_init_registers_without_workspace() -> None:
    """init registers tools even without workspace."""
    rail = _make_rail()
    agent = _make_agent(workspace="./default_ws")

    rail.init(agent)

    assert rail.tools is not None
    assert len(rail.tools) > 0
    assert rail.workspace is not None


def test_uninit_safe_without_tools() -> None:
    """uninit is safe when no tools were registered."""
    rail = _make_rail()
    agent = _make_agent()

    rail.uninit(agent)


def test_uninit_removes_todo_section() -> None:
    """uninit removes todo section from system_prompt_builder."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/test_ws")
    rail.init(agent)
    task_planning_section = build_todo_section()
    rail.system_prompt_builder.add_section(task_planning_section)
    assert rail.system_prompt_builder.get_section("todo") is not None
    rail.uninit(agent)
    assert rail.system_prompt_builder.get_section("todo") is None


def test_priority_is_90() -> None:
    """TaskPlanningRail has priority 90."""
    rail = _make_rail()
    assert rail.priority == 90


# ================================================================
# after_task_iteration bridge tests
# ================================================================


def _make_ctx(session=None):
    """Build a minimal AgentCallbackContext mock."""
    ctx = MagicMock()
    if not session:
        session = Session()
    ctx.session = session
    return ctx


def _make_todos(
    specs: List[tuple],
) -> List[TodoItem]:
    """Build TodoItem list from (title, status[, selected_model_id]) tuples."""
    items = []
    for spec in specs:
        content, status = spec[0], spec[1]
        selected_model_id = spec[2] if len(spec) > 2 else None
        items.append(
            TodoItem.create(
                content=content,
                status=status,
                selected_model_id=selected_model_id,
            )
        )
    return items


@pytest.mark.asyncio
async def test_after_task_iteration_bridges_todos() -> None:
    """_sync_todos_from_plan syncs TaskPlan status to todo file."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    task_id_a = "task-id-a"
    task_id_b = "task-id-b"
    task_id_c = "task-id-c"

    todos = [
        TodoItem(id=task_id_a, content="task-a", activeForm="Executing task-a", description="", status=TodoStatus.PENDING),
        TodoItem(id=task_id_b, content="task-b", activeForm="Executing task-b", description="", status=TodoStatus.PENDING),
        TodoItem(id=task_id_c, content="task-c", activeForm="Executing task-c", description="", status=TodoStatus.PENDING),
    ]

    plan_tasks = [
        TodoItem(id=task_id_a, content="task-a", activeForm="Executing task-a", description="", status=TodoStatus.IN_PROGRESS),
        TodoItem(id=task_id_b, content="task-b", activeForm="Executing task-b", description="", status=TodoStatus.PENDING),
        TodoItem(id=task_id_c, content="task-c", activeForm="Executing task-c", description="", status=TodoStatus.PENDING),
    ]

    plan = TaskPlan(goal="test", tasks=plan_tasks)
    state = DeepAgentState(iteration=1, task_plan=plan)
    saved_todos: List[TodoItem] = []

    def _capture_saved(todos_list: List[TodoItem]) -> None:
        saved_todos.extend(todos_list)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-1"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)
    tool.save_todos = AsyncMock(side_effect=_capture_saved)

    await rail.after_task_iteration(ctx)

    tool.save_todos.assert_called_once()
    assert saved_todos[0].status == TodoStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_after_task_iteration_syncs_todo_status_from_plan() -> None:
    """Todos status is synced from TaskPlan on each iteration."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    task_id_a = "task-id-a"
    task_id_b = "task-id-b"

    todos = [
        TodoItem(id=task_id_a, content="task-a", activeForm="Executing task-a", description="", status=TodoStatus.IN_PROGRESS),
        TodoItem(id=task_id_b, content="task-b", activeForm="Executing task-b", description="", status=TodoStatus.PENDING),
    ]

    plan_tasks = [
        TodoItem(id=task_id_a, content="task-a", activeForm="Executing task-a", description="", status=TodoStatus.COMPLETED),
        TodoItem(id=task_id_b, content="task-b", activeForm="Executing task-b", description="", status=TodoStatus.PENDING),
    ]

    plan = TaskPlan(goal="test", tasks=plan_tasks)
    state = DeepAgentState(iteration=2, task_plan=plan)
    saved_todos: List[TodoItem] = []

    def _capture_saved(todos_list: List[TodoItem]) -> None:
        saved_todos.extend(todos_list)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-sync-1"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)
    tool.save_todos = AsyncMock(side_effect=_capture_saved)

    await rail.after_task_iteration(ctx)

    tool.save_todos.assert_called_once()
    assert saved_todos[0].status == TodoStatus.COMPLETED
    assert saved_todos[1].status == TodoStatus.PENDING


@pytest.mark.asyncio
async def test_bridge_skips_when_plan_exists() -> None:
    """Existing plan with no pending tasks -> no overwrite."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    existing_todos = _make_todos([
        ("old-task", TodoStatus.COMPLETED),
    ])
    existing_plan = TaskPlan(goal="existing", tasks=existing_todos)
    state = DeepAgentState(iteration=1, task_plan=existing_plan)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-2"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=existing_todos)
    tool.save_todos = AsyncMock()

    await rail.after_task_iteration(ctx)

    # No save_state called since no pending tasks and plan already exists
    assert state.task_plan is existing_plan


@pytest.mark.asyncio
async def test_bridge_skips_when_no_todos() -> None:
    """No todo file -> no crash, no plan."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    state = DeepAgentState(iteration=1)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-3"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(
        side_effect=Exception("file not found")
    )

    await rail.after_task_iteration(ctx)

    assert state.task_plan is None


@pytest.mark.asyncio
async def test_bridge_skips_when_no_session() -> None:
    """ctx.session is None -> early return, no crash."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx(session=None)

    await rail.after_task_iteration(ctx)


@pytest.mark.asyncio
async def test_bridge_skips_when_no_pending() -> None:
    """All COMPLETED/IN_PROGRESS with existing plan -> no overwrite."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("done-a", TodoStatus.COMPLETED),
        ("done-b", TodoStatus.IN_PROGRESS),
    ])
    # Pre-existing plan with no pending tasks
    existing_plan = TaskPlan(goal="existing", tasks=todos)
    state = DeepAgentState(iteration=1, task_plan=existing_plan)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-5"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    await rail.after_task_iteration(ctx)

    # Plan not overwritten since no pending tasks and plan already exists
    assert state.task_plan is existing_plan


@pytest.mark.asyncio
async def test_bridge_skips_when_no_tools() -> None:
    """self.tools is None -> no crash."""
    rail = _make_rail()

    state = DeepAgentState(iteration=1)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-6"
    ctx.agent.load_state.return_value = state

    await rail.after_task_iteration(ctx)

    assert state.task_plan is None


# ================================================================
# before_model_call prompt injection tests
# ================================================================
@pytest.mark.asyncio
async def test_before_model_call_adds_section() -> None:
    """before_model_call adds todo section to system_prompt_builder."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.agent = agent

    mock_builder = MagicMock()
    rail.system_prompt_builder = mock_builder

    await rail.before_model_call(ctx)

    mock_builder.add_section.assert_called_once()


@pytest.mark.asyncio
async def test_before_model_call_without_prompt_builder() -> None:
    """before_model_call returns early when agent has no prompt_builder."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.agent = agent

    await rail.before_model_call(ctx)


# ================================================================
# after_tool_call progress reminder tests
# ================================================================
@pytest.mark.asyncio
async def test_after_tool_call_injects_progress_reminder() -> None:
    """after_tool_call injects progress reminder at interval."""
    rail = _make_rail()
    rail.enable_progress_repeat = True
    rail.list_tool_call_interval = 1
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.inputs = ToolCallInputs(tool_name="todo_create")
    ctx.context = MagicMock()
    ctx.context.get_messages.return_value = []
    ctx.session = MagicMock()
    ctx.session.get_session_id.return_value = "test-session-id"

    todos = _make_todos([
        ("task-a", TodoStatus.PENDING),
        ("task-b", TodoStatus.IN_PROGRESS),
    ])
    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    await rail.after_tool_call(ctx)

    assert rail._tool_call_counts["test-session-id"] == 1
    ctx.context.set_messages.assert_called_once()
    messages = ctx.context.set_messages.call_args[0][0]
    assert len(messages) == 1
    assert isinstance(messages[0], UserMessage)


@pytest.mark.asyncio
async def test_after_tool_call_counts_all_tools() -> None:
    """after_tool_call counts all tool calls."""
    rail = _make_rail()
    rail.enable_progress_repeat = True
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.inputs = ToolCallInputs(tool_name="todo_create")
    ctx.context = MagicMock()
    ctx.context.get_messages.return_value = []
    ctx.session = MagicMock()
    ctx.session.get_session_id.return_value = "test-session-id"

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=[])

    await rail.after_tool_call(ctx)
    assert rail._tool_call_counts["test-session-id"] == 1


@pytest.mark.asyncio
async def test_after_invoke_removes_tool_call_count() -> None:
    """after_invoke removes tool call count."""
    rail = _make_rail()
    rail.enable_progress_repeat = True
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.inputs = ToolCallInputs(tool_name="todo_create")
    ctx.context = MagicMock()
    ctx.context.get_messages.return_value = []
    ctx.session = MagicMock()
    ctx.session.get_session_id.return_value = "test-session-id"

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=[])

    await rail.after_tool_call(ctx)
    assert "test-session-id" in rail._tool_call_counts

    await rail.after_invoke(ctx)
    assert "test-session-id" not in rail._tool_call_counts


@pytest.mark.asyncio
async def test_after_tool_call_custom_interval() -> None:
    """after_tool_call respects custom interval."""
    rail = TaskPlanningRail(enable_progress_repeat=True, list_tool_call_interval=3)
    op = _make_operation()
    rail.set_sys_operation(op)
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.inputs = ToolCallInputs(tool_name="todo_create")
    ctx.context = MagicMock()
    ctx.context.get_messages.return_value = []
    ctx.session = MagicMock()
    ctx.session.get_session_id.return_value = "test-session-id"

    todos = _make_todos([
        ("task-a", TodoStatus.PENDING),
    ])
    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    await rail.after_tool_call(ctx)
    await rail.after_tool_call(ctx)
    assert rail._tool_call_counts[ctx.session.get_session_id()] == 2

    await rail.after_tool_call(ctx)
    assert rail._tool_call_counts[ctx.session.get_session_id()] == 3
    ctx.context.set_messages.assert_called_once()


@pytest.mark.asyncio
async def test_after_tool_call_skips_when_disabled() -> None:
    """after_tool_call skips when enable_progress_repeat is False."""
    rail = _make_rail()
    rail.enable_progress_repeat = False
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.inputs = ToolCallInputs(tool_name="todo_create")
    ctx.context = MagicMock()
    ctx.context.get_messages.return_value = []
    ctx.session = MagicMock()
    ctx.session.get_session_id.return_value = "test-session-id"

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=[])

    await rail.after_tool_call(ctx)

    assert "test-session-id" not in rail._tool_call_counts
    ctx.context.set_messages.assert_not_called()


@pytest.mark.asyncio
async def test_after_invoke_safe_without_session() -> None:
    """after_invoke is safe when ctx.session is None."""
    rail = _make_rail()
    rail.enable_progress_repeat = True
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx()
    ctx.session = None

    await rail.after_invoke(ctx)


# ================================================================
# _format_task_content tests
# ================================================================


def test_format_task_content_with_in_progress() -> None:
    """_format_task_content extracts in_progress task."""
    rail = _make_rail()

    todos = _make_todos([
        ("task-a", TodoStatus.PENDING),
        ("task-b", TodoStatus.IN_PROGRESS),
        ("task-c", TodoStatus.COMPLETED),
    ])

    tasks, in_progress_task = rail._format_task_content(todos)

    assert in_progress_task == "task-b"
    assert "task-a" in tasks
    assert "task-b" in tasks
    assert "task-c" in tasks


def test_format_task_content_without_in_progress() -> None:
    """_format_task_content returns empty in_progress_task when none."""
    rail = _make_rail()

    todos = _make_todos([
        ("task-a", TodoStatus.PENDING),
        ("task-b", TodoStatus.COMPLETED),
    ])

    tasks, in_progress_task = rail._format_task_content(todos)

    assert in_progress_task == ""
    assert "task-a" in tasks
    assert "task-b" in tasks


# ================================================================
# TaskPlan with TodoItem tests
# ================================================================


def test_task_plan_uses_todo_item() -> None:
    """TaskPlan.tasks now holds TodoItem instances directly."""
    todos = _make_todos([
        ("task-a", TodoStatus.PENDING),
        ("task-b", TodoStatus.IN_PROGRESS),
    ])
    plan = TaskPlan(goal="test", tasks=todos)
    assert len(plan.tasks) == 2
    assert isinstance(plan.tasks[0], TodoItem)
    assert plan.tasks[1].status == TodoStatus.IN_PROGRESS


# ================================================================
# Language support tests
# ================================================================


def test_enable_progress_repeat_default() -> None:
    """Default enable_progress_repeat is False."""
    rail = TaskPlanningRail()
    assert rail.enable_progress_repeat is False


def test_enable_progress_repeat_true() -> None:
    """Can set enable_progress_repeat to True."""
    rail = TaskPlanningRail(enable_progress_repeat=True)
    assert rail.enable_progress_repeat is True


def test_list_tool_call_interval_default() -> None:
    """Default list_tool_call_interval is 20."""
    rail = TaskPlanningRail()
    assert rail.list_tool_call_interval == 20


def test_build_todo_system_prompt_chinese() -> None:
    """build_todo_system_prompt returns Chinese prompt."""
    prompt = build_todo_system_prompt(language="cn")
    assert "任务规划" in prompt


def test_build_todo_system_prompt_english() -> None:
    """build_todo_system_prompt returns English prompt."""
    prompt = build_todo_system_prompt(language="en")
    assert "task planning" in prompt


def test_build_progress_reminder_user_prompt_chinese() -> None:
    """build_progress_reminder_user_prompt returns Chinese prompt."""
    prompt = build_progress_reminder_user_prompt(language="cn")
    assert "确保计划正在正确执行" in prompt


def test_build_progress_reminder_user_prompt_english() -> None:
    """build_progress_reminder_user_prompt returns English prompt."""
    prompt = build_progress_reminder_user_prompt(language="en")
    assert "ensure the plan is being executed correctly" in prompt


def test_build_progress_reminder_user_prompt_with_task_content() -> None:
    """build_progress_reminder_user_prompt includes task content."""
    tasks = "id: 1 |status: pending |content: task-a\nid: 2 |status: in_progress |content: task-b"
    in_progress_task = "task-b"
    prompt = build_progress_reminder_user_prompt(language="en", tasks=tasks, in_progress_task=in_progress_task)
    assert tasks in prompt
    assert in_progress_task in prompt
    assert "currently being executed" in prompt


def test_build_progress_reminder_user_prompt_with_task_content_chinese() -> None:
    """build_progress_reminder_user_prompt includes task content in Chinese."""
    tasks = "id: 1 |status: pending |content: 任务一\nid: 2 |status: in_progress |content: 任务二"
    in_progress_task = "任务二"
    prompt = build_progress_reminder_user_prompt(language="cn", tasks=tasks, in_progress_task=in_progress_task)
    assert tasks in prompt
    assert in_progress_task in prompt
    assert "正在执行的任务" in prompt


# ================================================================
# Model selection tests
# ================================================================

from unittest.mock import patch


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


def test_model_selection_default_is_none() -> None:
    """TaskPlanningRail._model_selection defaults to empty dict."""
    rail = TaskPlanningRail()
    assert rail._model_selection == {}


def test_model_selection_stored_on_init() -> None:
    """model_selection passed to __init__ is stored."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")
    model_selection = {
        fast_model: "cheap model for simple tasks",
        smart_model: "premium model for complex tasks",
    }
    rail = TaskPlanningRail(model_selection=model_selection)
    assert rail._model_selection == model_selection
    assert rail._model_id_to_model["fast"] == fast_model
    assert rail._model_id_to_model["smart"] == smart_model


@pytest.mark.asyncio
async def test_before_model_call_switches_model_for_in_progress_task() -> None:
    """before_model_call calls set_llm with the model matching selected_model_id."""
    fast_model = _make_mock_model("fast")
    smart_model = _make_mock_model("smart")
    rail = _make_rail()
    rail._model_selection = {
        fast_model: "cheap model",
        smart_model: "premium model",
    }
    rail._model_id_to_model = {
        "fast": fast_model,
        "smart": smart_model,
    }
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("task-a", TodoStatus.IN_PROGRESS, "fast"),
        ("task-b", TodoStatus.PENDING),
    ])
    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-model-switch"
    ctx.agent.set_llm = MagicMock()
    ctx.agent._llm = MagicMock()
    rail.system_prompt_builder = MagicMock()
    rail.system_prompt_builder.language = "en"

    await rail.before_model_call(ctx)

    ctx.agent.set_llm.assert_called_once_with(fast_model)


@pytest.mark.asyncio
async def test_before_model_call_restores_default_when_no_model_id() -> None:
    """before_model_call restores default model when task has no selected_model_id."""
    fast_model = _make_mock_model("fast")
    default_model = MagicMock()
    rail = _make_rail()
    rail._model_selection = {fast_model: "cheap model"}
    rail._model_id_to_model = {"fast": fast_model}
    rail._default_llm = default_model
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("task-a", TodoStatus.IN_PROGRESS),  # no selected_model_id
    ])
    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-default-restore"
    ctx.agent.set_llm = MagicMock()
    ctx.agent._llm = MagicMock()
    rail.system_prompt_builder = MagicMock()
    rail.system_prompt_builder.language = "en"

    await rail.before_model_call(ctx)

    ctx.agent.set_llm.assert_called_once_with(default_model)


@pytest.mark.asyncio
async def test_before_model_call_no_switch_when_model_selection_empty() -> None:
    """before_model_call skips model switching when _model_selection is empty."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx(session=MagicMock())
    ctx.agent.set_llm = MagicMock()
    rail.system_prompt_builder = MagicMock()
    rail.system_prompt_builder.language = "en"

    await rail.before_model_call(ctx)

    ctx.agent.set_llm.assert_not_called()


@pytest.mark.asyncio
async def test_after_model_call_accumulates_usage() -> None:
    """after_model_call accumulates token usage into _usage_records."""
    fast_model = _make_mock_model("fast")
    rail = _make_rail()
    rail._model_selection = {fast_model: "cheap model"}
    rail._model_id_to_model = {"fast": fast_model}
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50
    response = MagicMock()
    response.usage_metadata = usage

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-usage"
    ctx.inputs = MagicMock()
    ctx.inputs.response = response
    ctx.agent._llm = _make_mock_model("fast")

    await rail.after_model_call(ctx)

    assert "fast" in rail._usage_records
    assert rail._usage_records["fast"].input_tokens == 100
    assert rail._usage_records["fast"].output_tokens == 50

    await rail.after_model_call(ctx)
    assert rail._usage_records["fast"].input_tokens == 200
    assert rail._usage_records["fast"].output_tokens == 100


@pytest.mark.asyncio
async def test_after_invoke_resets_usage_records() -> None:
    """after_invoke logs and resets _usage_records."""
    fast_model = _make_mock_model("fast")
    rail = _make_rail()
    rail._model_selection = {fast_model: "cheap model"}
    rail._model_id_to_model = {"fast": fast_model}
    rail._usage_records = {"fast": ModelUsageRecord(model_id="fast", input_tokens=200, output_tokens=100)}
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-reset"

    await rail.after_invoke(ctx)

    assert rail._usage_records == {}


def test_build_todo_section_with_model_selection_injects_prompt() -> None:
    """build_todo_section includes model selection guidance when model_selection provided."""
    from openjiuwen.harness.prompts.sections.todo import build_todo_section

    fast_model = _make_mock_model("fast")
    model_selection = {fast_model: "cheap model"}
    section = build_todo_section(language="en", model_selection=model_selection)
    assert section is not None
    content = section.content.get("en", "")
    assert "fast" in content
    assert "Model Selection" in content


def test_build_todo_section_without_model_selection_no_model_prompt() -> None:
    """build_todo_section without model_selection includes warning about not using selected_model_id."""
    from openjiuwen.harness.prompts.sections.todo import build_todo_section

    section = build_todo_section(language="en")
    assert section is not None
    content = section.content.get("en", "")
    assert "Model Selection Note" in content
    assert "do NOT use the selected_model_id field" in content
