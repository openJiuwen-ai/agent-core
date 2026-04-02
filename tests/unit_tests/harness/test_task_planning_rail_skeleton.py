# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Skeleton tests for TaskPlanningRail."""
from __future__ import annotations

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.task_planning_rail import (
    TaskPlanningRail,
)


@pytest.mark.asyncio
async def test_task_planning_rail_lifecycle_hooks_noop() -> None:
    card_id = "test_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL)
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card.id)
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
