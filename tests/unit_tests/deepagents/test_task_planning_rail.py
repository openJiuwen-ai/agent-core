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
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.workspace.workspace import Workspace
from openjiuwen.deepagents.prompts.sections.todo import (
    build_progress_reminder_user_prompt,
    build_todo_system_prompt, build_todo_section,
)
from openjiuwen.deepagents.rails.task_planning_rail import (
    TaskPlanningRail,
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
from openjiuwen.deepagents.tools.todo import (
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
    agent_id = agent.card.id
    rail.init(agent)

    assert rail.tools is not None
    assert len(rail.tools) > 0
    assert rail.workspace is not None
    assert rail.workspace.root_path == f"/tmp/test_ws/{agent_id}_workspace"


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
    """Build TodoItem list from (content, status) tuples."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    items = []
    for content, status in specs:
        items.append(
            TodoItem(
                content=content,
                activeForm=f"Executing {content}",
                status=status,
                createdAt=now,
                updatedAt=now,
            )
        )
    return items


@pytest.mark.asyncio
async def test_after_task_iteration_bridges_todos() -> None:
    """3 todos (1 completed + 2 pending) -> TaskPlan with 3 items."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("task-a", TodoStatus.COMPLETED),
        ("task-b", TodoStatus.PENDING),
        ("task-c", TodoStatus.PENDING),
    ])

    state = DeepAgentState(iteration=1)
    saved_states: List[DeepAgentState] = []

    def _capture_state(_s: Any, st: DeepAgentState) -> None:
        saved_states.append(st)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-1"
    ctx.agent.load_state.return_value = state
    ctx.agent.save_state.side_effect = _capture_state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)
    tool.save_todos = AsyncMock()

    await rail.after_task_iteration(ctx)

    assert len(saved_states) == 1
    plan = saved_states[0].task_plan
    assert plan is not None
    assert len(plan.tasks) == 3
    assert plan.tasks[0].status == TaskStatus.COMPLETED
    assert plan.tasks[1].status == TaskStatus.PENDING
    assert plan.tasks[2].status == TaskStatus.PENDING
    tool.save_todos.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_task_iteration_syncs_todo_status_from_plan() -> None:
    """Existing plan should be synced back to todo file statuses."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("task-a", TodoStatus.IN_PROGRESS),
        ("task-b", TodoStatus.PENDING),
    ])
    todo_a_id = todos[0].id
    todo_b_id = todos[1].id

    existing_plan = TaskPlan(
        goal="existing",
        tasks=[
            TaskItem(
                id=todo_a_id,
                title="task-a",
                status=TaskStatus.COMPLETED,
            ),
            TaskItem(
                id=todo_b_id,
                title="task-b",
                status=TaskStatus.PENDING,
            ),
        ],
    )
    state = DeepAgentState(
        iteration=2, task_plan=existing_plan
    )
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-sync-1"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)
    tool.save_todos = AsyncMock()

    await rail.after_task_iteration(ctx)

    tool.save_todos.assert_awaited_once()
    saved_todos = tool.save_todos.call_args[0][0]
    status_map = {
        t.id: t.status for t in saved_todos
    }
    assert status_map[todo_a_id] == TodoStatus.COMPLETED
    assert status_map[todo_b_id] == TodoStatus.PENDING


@pytest.mark.asyncio
async def test_bridge_skips_when_plan_exists() -> None:
    """Existing plan -> no overwrite."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    existing_plan = TaskPlan(
        goal="existing",
        tasks=[TaskItem(title="old-task")],
    )
    state = DeepAgentState(
        iteration=1, task_plan=existing_plan
    )

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-2"
    ctx.agent.load_state.return_value = state

    await rail.after_task_iteration(ctx)

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
    """All COMPLETED -> no TaskPlan created."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    todos = _make_todos([
        ("done-a", TodoStatus.COMPLETED),
        ("done-b", TodoStatus.IN_PROGRESS),
    ])

    state = DeepAgentState(iteration=1)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-5"
    ctx.agent.load_state.return_value = state

    tool = rail._find_todo_tool()
    assert tool is not None
    tool.load_todos = AsyncMock(return_value=todos)

    await rail.after_task_iteration(ctx)

    assert state.task_plan is None


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
# _to_todo_status tests
# ================================================================


def test_to_todo_status_mapping() -> None:
    """_to_todo_status maps TaskStatus to TodoStatus correctly."""
    assert TaskPlanningRail._to_todo_status(TaskStatus.PENDING) == TodoStatus.PENDING
    assert TaskPlanningRail._to_todo_status(TaskStatus.IN_PROGRESS) == TodoStatus.IN_PROGRESS
    assert TaskPlanningRail._to_todo_status(TaskStatus.FAILED) == TodoStatus.CANCELLED
    assert TaskPlanningRail._to_todo_status(TaskStatus.COMPLETED) == TodoStatus.COMPLETED


# ================================================================
# Language support tests
# ================================================================


def test_language_parameter_default() -> None:
    """Default language is 'cn'."""
    rail = TaskPlanningRail()
    assert rail.language == "cn"


def test_language_parameter_english() -> None:
    """Can set language to 'en'."""
    rail = TaskPlanningRail(language="en")
    assert rail.language == "en"


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
    assert "任务规划助手" in prompt


def test_build_todo_system_prompt_english() -> None:
    """build_todo_system_prompt returns English prompt."""
    prompt = build_todo_system_prompt(language="en")
    assert "Todo assistant" in prompt


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
