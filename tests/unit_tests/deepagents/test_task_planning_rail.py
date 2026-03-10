# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskPlanningRail hooks."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.core.session.agent import Session
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
    _write_runtime_state,
    load_state,
)
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)


def _make_operation():
    """Create a SysOperation for tests."""
    card_id = "test_rail_op"
    card = SysOperationCard(
        id=card_id, mode=OperationMode.LOCAL
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(  # type: ignore[return-value]
        card.id
    )


def _make_rail(
    interval: int = 5,
    replanning: bool = True,
) -> TaskPlanningRail:
    """Build a TaskPlanningRail with test defaults."""
    op = _make_operation()
    return TaskPlanningRail(
        operation=op,  # type: ignore[arg-type]
        progress_restate_interval=interval,
        enable_replanning=replanning,
    )


def _make_agent_and_session():
    """Build a DeepAgent + Session pair."""
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    )
    agent.configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    session = Session(session_id="sess_rail")
    return agent, session


def _make_ctx(
    agent: DeepAgent,
    session: Session,
    inputs: Any = None,
) -> AgentCallbackContext:
    """Build an AgentCallbackContext."""
    if inputs is None:
        inputs = InvokeInputs(query="build feature")
    return AgentCallbackContext(
        agent=agent,
        inputs=inputs,
        session=session,
    )


# ── before_invoke ──


@pytest.mark.asyncio
async def test_before_invoke_creates_plan() -> None:
    """before_invoke creates a TaskPlan in state."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()
    ctx = _make_ctx(agent, session)

    await rail.before_invoke(ctx)

    state = load_state(ctx)
    plan = state.task_plan
    assert plan is not None
    assert plan.goal == "build feature"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].id == "t1"


@pytest.mark.asyncio
async def test_before_invoke_skips_if_plan_exists() -> None:
    """before_invoke does not overwrite existing plan."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()
    ctx = _make_ctx(agent, session)

    existing = TaskPlan(
        goal="old",
        tasks=[TaskItem(id="old1", title="old")],
    )
    state = DeepAgentState(task_plan=existing)
    _write_runtime_state(session, state)

    await rail.before_invoke(ctx)

    loaded = load_state(ctx)
    assert loaded.task_plan is not None
    assert loaded.task_plan.goal == "old"
    assert len(loaded.task_plan.tasks) == 1


@pytest.mark.asyncio
async def test_before_invoke_skips_no_session() -> None:
    """before_invoke is a no-op without session."""
    rail = _make_rail()
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    )
    agent.configure(
        DeepAgentConfig(enable_task_loop=True)
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="hello"),
        session=None,
    )

    await rail.before_invoke(ctx)
    # No crash, no state


@pytest.mark.asyncio
async def test_before_invoke_skips_empty_query() -> None:
    """before_invoke skips when query is empty."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()
    ctx = _make_ctx(
        agent, session,
        inputs=InvokeInputs(query=""),
    )

    await rail.before_invoke(ctx)

    state = load_state(ctx)
    assert state.task_plan is None


# ── before_task_iteration ──


@pytest.mark.asyncio
async def test_before_task_iteration_marks_next() -> None:
    """before_task_iteration marks next PENDING task."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id="t1", title="step 1"),
            TaskItem(id="t2", title="step 2"),
        ],
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    ctx = _make_ctx(
        agent, session,
        inputs=TaskIterationInputs(
            iteration=1, loop_event=None
        ),
    )

    await rail.before_task_iteration(ctx)

    assert plan.tasks[0].status == TaskStatus.IN_PROGRESS
    assert plan.current_task_id == "t1"


@pytest.mark.asyncio
async def test_before_task_iteration_no_plan() -> None:
    """before_task_iteration is safe without plan."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()
    ctx = _make_ctx(
        agent, session,
        inputs=TaskIterationInputs(
            iteration=1, loop_event=None
        ),
    )

    await rail.before_task_iteration(ctx)
    # No crash


# ── before_model_call ──


@pytest.mark.asyncio
async def test_before_model_call_injects_progress() -> None:
    """before_model_call injects progress at interval."""
    rail = _make_rail(interval=2)
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="test",
        tasks=[TaskItem(id="t1", title="step 1")],
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    messages: List[Dict[str, str]] = [
        {"role": "user", "content": "hello"}
    ]
    model_inputs = ModelCallInputs(messages=messages)
    ctx = _make_ctx(agent, session, inputs=model_inputs)

    # Call 1: not at interval
    await rail.before_model_call(ctx)
    assert len(messages) == 1

    # Call 2: at interval (2)
    ctx.inputs = ModelCallInputs(messages=messages)
    await rail.before_model_call(ctx)
    assert len(messages) == 2
    assert "[Task Progress]" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_before_model_call_skips_no_plan() -> None:
    """before_model_call skips when no plan exists."""
    rail = _make_rail(interval=1)
    agent, session = _make_agent_and_session()

    messages: List[Dict[str, str]] = []
    model_inputs = ModelCallInputs(messages=messages)
    ctx = _make_ctx(agent, session, inputs=model_inputs)

    await rail.before_model_call(ctx)
    assert len(messages) == 0


# ── after_tool_call ──


@pytest.mark.asyncio
async def test_after_tool_call_syncs_todo_write() -> None:
    """after_tool_call processes todo_write calls."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="test",
        tasks=[TaskItem(id="t1", title="step 1")],
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    tool_inputs = ToolCallInputs(
        tool_name="todo_write",
        tool_result={"message": "created"},
        tool_msg="ok",
    )
    ctx = _make_ctx(agent, session, inputs=tool_inputs)

    await rail.after_tool_call(ctx)
    # No crash, sync logged


@pytest.mark.asyncio
async def test_after_tool_call_ignores_other_tools() -> None:
    """after_tool_call ignores non-todo tools."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    tool_inputs = ToolCallInputs(
        tool_name="shell_exec",
        tool_result={"output": "ok"},
    )
    ctx = _make_ctx(agent, session, inputs=tool_inputs)

    await rail.after_tool_call(ctx)
    # No crash, no action


# ── after_task_iteration ──


@pytest.mark.asyncio
async def test_after_task_iteration_completes_task() -> None:
    """after_task_iteration marks current task done."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="test",
        tasks=[TaskItem(id="t1", title="step 1")],
    )
    plan.mark_in_progress("t1")
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    iter_inputs = TaskIterationInputs(
        iteration=1,
        loop_event=None,
        result={"output": "done step 1"},
    )
    ctx = _make_ctx(agent, session, inputs=iter_inputs)

    await rail.after_task_iteration(ctx)

    assert plan.tasks[0].status == TaskStatus.COMPLETED
    assert plan.tasks[0].result_summary == "done step 1"
    assert plan.current_task_id is None


@pytest.mark.asyncio
async def test_after_task_iteration_no_current() -> None:
    """after_task_iteration is safe without current task."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="test",
        tasks=[TaskItem(id="t1", title="step 1")],
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    iter_inputs = TaskIterationInputs(
        iteration=1, loop_event=None
    )
    ctx = _make_ctx(agent, session, inputs=iter_inputs)

    await rail.after_task_iteration(ctx)

    assert plan.tasks[0].status == TaskStatus.PENDING


# ── after_invoke ──


@pytest.mark.asyncio
async def test_after_invoke_generates_report() -> None:
    """after_invoke stores task_report in ctx.extra."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()

    plan = TaskPlan(
        goal="build feature",
        tasks=[
            TaskItem(
                id="t1",
                title="step 1",
                status=TaskStatus.COMPLETED,
            ),
        ],
    )
    state = DeepAgentState(task_plan=plan)
    _write_runtime_state(session, state)

    ctx = _make_ctx(agent, session)

    await rail.after_invoke(ctx)

    report = ctx.extra.get("task_report")
    assert report is not None
    assert report["goal"] == "build feature"
    assert "1/1 completed" in report["progress"]
    assert "step 1" in report["plan_markdown"]


@pytest.mark.asyncio
async def test_after_invoke_no_plan_no_report() -> None:
    """after_invoke is a no-op without plan."""
    rail = _make_rail()
    agent, session = _make_agent_and_session()
    ctx = _make_ctx(agent, session)

    await rail.after_invoke(ctx)

    assert "task_report" not in ctx.extra


# ── _build_initial_plan ──


def test_build_initial_plan_structure() -> None:
    """_build_initial_plan creates correct structure."""
    plan = TaskPlanningRail._build_initial_plan(
        "implement auth"
    )
    assert plan.goal == "implement auth"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].id == "t1"
    assert plan.tasks[0].title == "implement auth"
    assert plan.tasks[0].description == "implement auth"
    assert plan.tasks[0].status == TaskStatus.PENDING


def test_build_initial_plan_truncates_long_query() -> None:
    """_build_initial_plan truncates long queries."""
    long_query = "x" * 300
    plan = TaskPlanningRail._build_initial_plan(long_query)
    assert len(plan.goal) == 200
    assert len(plan.tasks[0].title) == 100


# ── full lifecycle ──


@pytest.mark.asyncio
async def test_full_lifecycle_flow() -> None:
    """Simulate a complete before_invoke → iteration → after_invoke."""
    rail = _make_rail(interval=1)
    agent, session = _make_agent_and_session()

    # 1. before_invoke: create plan
    ctx = _make_ctx(agent, session)
    await rail.before_invoke(ctx)

    state = load_state(ctx)
    assert state.task_plan is not None
    plan = state.task_plan

    # 2. before_task_iteration: mark t1 in-progress
    iter_ctx = _make_ctx(
        agent, session,
        inputs=TaskIterationInputs(
            iteration=1, loop_event=None
        ),
    )
    await rail.before_task_iteration(iter_ctx)
    assert plan.current_task_id == "t1"
    assert (
        plan.tasks[0].status == TaskStatus.IN_PROGRESS
    )

    # 3. before_model_call: inject progress
    messages: List[Dict[str, str]] = [
        {"role": "user", "content": "go"}
    ]
    model_ctx = _make_ctx(
        agent, session,
        inputs=ModelCallInputs(messages=messages),
    )
    await rail.before_model_call(model_ctx)
    assert len(messages) == 2
    assert "[Task Progress]" in messages[-1]["content"]

    # 4. after_task_iteration: complete t1
    iter_ctx.inputs = TaskIterationInputs(
        iteration=1,
        loop_event=None,
        result={"output": "feature built"},
    )
    await rail.after_task_iteration(iter_ctx)
    assert plan.tasks[0].status == TaskStatus.COMPLETED

    # 5. after_invoke: generate report
    final_ctx = _make_ctx(agent, session)
    await rail.after_invoke(final_ctx)
    report = final_ctx.extra["task_report"]
    assert "1/1 completed" in report["progress"]
