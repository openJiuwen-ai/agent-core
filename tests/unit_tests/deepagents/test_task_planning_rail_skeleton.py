# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Skeleton tests for TaskPlanningRail."""
from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.rails.task_planning_rail import (
    TaskPlanningRail,
)


@pytest.mark.asyncio
async def test_task_planning_rail_lifecycle_hooks_noop() -> None:
    rail = TaskPlanningRail()
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="build feature"),
        session=Session(session_id="sess_task_plan"),
    )

    await rail.before_invoke(ctx)
    await rail.before_task_iteration(ctx)
    await rail.before_model_call(ctx)

    ctx.inputs = ToolCallInputs(
        tool_name="todo_write",
        tool_result={"todos": []},
        tool_msg="ok",
    )
    await rail.after_tool_call(ctx)

    await rail.after_task_iteration(ctx)
    await rail.after_invoke(ctx)
