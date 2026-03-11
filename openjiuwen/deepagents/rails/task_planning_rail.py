# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail skeleton.

This rail wires all required lifecycle hooks first.
Planning/replanning strategies are intentionally deferred.
"""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents import DeepAgent
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.tools.todo import create_todos_tool


class TaskPlanningRail(DeepAgentRail):
    """Skeleton task planning rail.

    TODO(P1):
      - initial plan generation
      - progress restatement cadence
      - dynamic replanning after each iteration
      - tight integration with todo_write/todo_read tools
    """

    priority = 90

    def __init__(self, operation: SysOperation):
        super().__init__()
        self.tools = None
        self.workspace = None
        self.sys_operation = operation

    def init(self, agent):
        if isinstance(agent, DeepAgent) and agent.deep_config.workspace and hasattr(agent, 'ability_manager'):
            self.workspace = agent.deep_config.workspace
            tools = create_todos_tool(self.sys_operation, self.workspace.root_path)
            self.tools = tools
            for tool in tools:
                agent.ability_manager.add(tool.card)

    def uninit(self, agent):
        if self.tools and hasattr(agent, 'ability_manager'):
            for tool in self.tools:
                name = getattr(tool, 'name', None)
                if name:
                    agent.ability_manager.remove(name)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Invoke-start placeholder."""
        _ = ctx

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Iteration-start hook placeholder."""
        _ = ctx

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Model-call hook placeholder for progress restatement."""
        _ = ctx

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Tool-call hook placeholder."""
        _ = ctx

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Iteration-end hook placeholder for dynamic replanning."""
        _ = ctx

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Invoke-end placeholder."""
        _ = ctx


__all__ = [
    "TaskPlanningRail",
]
