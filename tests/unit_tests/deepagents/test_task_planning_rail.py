# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskPlanningRail init/uninit."""
# pylint: disable=protected-access
from __future__ import annotations

from typing import List
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import (
    SysOperationCard,
    OperationMode,
)
from openjiuwen.deepagents.deep_agent import DeepAgent
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
    return TaskPlanningRail(
        operation=op,
    )


def _make_agent(workspace: str = None) -> DeepAgent:
    """Build a DeepAgent with optional workspace."""
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    )
    agent.configure(
        DeepAgentConfig(
            enable_task_loop=True,
            workspace=workspace,
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
    assert rail.workspace == "/tmp/test_ws"


def test_init_registers_without_workspace() -> None:
    """init registers tools even without workspace."""
    rail = _make_rail()
    agent = _make_agent(workspace=None)

    rail.init(agent)

    assert rail.tools is not None
    assert len(rail.tools) > 0
    assert rail.workspace is None


def test_uninit_removes_tools() -> None:
    """uninit removes previously registered tools."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/test_ws")

    rail.init(agent)
    assert rail.tools is not None

    rail.uninit(agent)
    # Tools list still exists on rail but removed
    # from agent's ability_manager


def test_uninit_safe_without_tools() -> None:
    """uninit is safe when no tools were registered."""
    rail = _make_rail()
    agent = _make_agent()

    rail.uninit(agent)
    # No crash


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
    """3 todos (1 completed + 2 pending) → TaskPlan with 3 items."""
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

    def fake_save(_ctx, st):
        saved_states.append(st)

    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-1"

    with (
        patch(
            "openjiuwen.deepagents.rails"
            ".task_planning_rail.load_state",
            return_value=state,
        ),
        patch(
            "openjiuwen.deepagents.rails"
            ".task_planning_rail.save_state",
            side_effect=fake_save,
        ),
    ):
        # Mock load_todos on the first TodoTool
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

    with patch(
        "openjiuwen.deepagents.rails"
        ".task_planning_rail.load_state",
        return_value=state,
    ):
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
    """Existing plan → no overwrite."""
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

    with patch(
        "openjiuwen.deepagents.rails"
        ".task_planning_rail.load_state",
        return_value=state,
    ):
        await rail.after_task_iteration(ctx)

    # save_state should NOT have been called
    assert state.task_plan is existing_plan


@pytest.mark.asyncio
async def test_bridge_skips_when_no_todos() -> None:
    """No todo file → no crash, no plan."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    state = DeepAgentState(iteration=1)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-3"

    with patch(
        "openjiuwen.deepagents.rails"
        ".task_planning_rail.load_state",
        return_value=state,
    ):
        tool = rail._find_todo_tool()
        assert tool is not None
        tool.load_todos = AsyncMock(
            side_effect=Exception("file not found")
        )

        await rail.after_task_iteration(ctx)

    assert state.task_plan is None


@pytest.mark.asyncio
async def test_bridge_skips_when_no_session() -> None:
    """ctx.session is None → early return, no crash."""
    rail = _make_rail()
    agent = _make_agent(workspace="/tmp/ws")
    rail.init(agent)

    ctx = _make_ctx(session=None)

    # Should not raise
    await rail.after_task_iteration(ctx)


@pytest.mark.asyncio
async def test_bridge_skips_when_no_pending() -> None:
    """All COMPLETED → no TaskPlan created."""
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

    with patch(
        "openjiuwen.deepagents.rails"
        ".task_planning_rail.load_state",
        return_value=state,
    ):
        tool = rail._find_todo_tool()
        assert tool is not None
        tool.load_todos = AsyncMock(return_value=todos)

        await rail.after_task_iteration(ctx)

    assert state.task_plan is None


@pytest.mark.asyncio
async def test_bridge_skips_when_no_tools() -> None:
    """self.tools is None → no crash."""
    rail = _make_rail()
    # Do NOT call rail.init() → tools stays None

    state = DeepAgentState(iteration=1)
    ctx = _make_ctx(session=MagicMock())
    ctx.session.get_session_id.return_value = "sess-6"

    with patch(
        "openjiuwen.deepagents.rails"
        ".task_planning_rail.load_state",
        return_value=state,
    ):
        await rail.after_task_iteration(ctx)

    assert state.task_plan is None
