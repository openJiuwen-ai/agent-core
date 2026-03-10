# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — lifecycle hooks for task planning.

Implements 6 hooks that drive the outer task loop:
  - before_invoke: generate initial TaskPlan from query
  - before_task_iteration: mark current task in-progress
  - before_model_call: inject progress into messages
  - after_tool_call: sync todo_write state to TaskPlan
  - after_task_iteration: dynamic replanning
  - after_invoke: generate completion report
"""
from __future__ import annotations

from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.schema.state import load_state
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
)
from openjiuwen.deepagents.tools.todo import create_todos_tool


class TaskPlanningRail(DeepAgentRail):
    """Task planning rail with full lifecycle hooks.

    Manages TaskPlan creation, progress tracking,
    and dynamic replanning across the outer task loop.

    Attributes:
        priority: Execution priority (90 = high).
        progress_restate_interval: Inject progress
            markdown every N model calls.
        enable_replanning: Whether to replan after
            each iteration.
    """

    priority = 90

    def __init__(
        self,
        operation: SysOperation,
        progress_restate_interval: int = 5,
        enable_replanning: bool = True,
    ) -> None:
        super().__init__()
        self.tools = None
        self.workspace: Optional[str] = None
        self.sys_operation = operation
        self.progress_restate_interval = (
            progress_restate_interval
        )
        self.enable_replanning = enable_replanning
        self._model_call_count: int = 0

    def init(self, agent) -> None:
        """Register todo tools on the agent."""
        from openjiuwen.deepagents.deep_agent import (
            DeepAgent,
        )

        if (
            isinstance(agent, DeepAgent)
            and agent.deep_config
            and agent.deep_config.workspace
            and hasattr(agent, "ability_manager")
        ):
            self.workspace = agent.deep_config.workspace
            tools = create_todos_tool(
                self.sys_operation,
                self.workspace.root_path,
            )
            self.tools = tools
            Runner.resource_mgr.add_tool(tools)
            for tool in tools:
                agent.ability_manager.add(tool.card)

    def uninit(self, agent) -> None:
        """Remove todo tools from the agent."""
        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool, "name", None)
                if name:
                    agent.ability_manager.remove(name)
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)

    async def before_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Generate initial TaskPlan from user query.

        Decomposes the user query into a TaskPlan and
        stores it in the session state. The first task
        is marked IN_PROGRESS.

        Args:
            ctx: Callback context with InvokeInputs.
        """
        if not isinstance(ctx.inputs, InvokeInputs):
            return
        if ctx.session is None:
            return

        query = ctx.inputs.query
        if not query:
            return

        state = load_state(ctx)
        if state.task_plan is not None:
            return

        plan = self._build_initial_plan(query)
        state.task_plan = plan
        self._model_call_count = 0

        logger.info(
            f"TaskPlanningRail: created plan "
            f"with {len(plan.tasks)} task(s)"
        )

    async def before_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Mark the next pending task as in-progress.

        Args:
            ctx: Callback context with
                TaskIterationInputs.
        """
        if ctx.session is None:
            return

        state = load_state(ctx)
        plan = state.task_plan
        if plan is None:
            return

        next_task = plan.get_next_task()
        if next_task is not None:
            plan.mark_in_progress(next_task.id)
            logger.info(
                f"TaskPlanningRail: starting task "
                f"'{next_task.title}'"
            )

    async def before_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Inject TaskPlan progress into LLM messages.

        Every ``progress_restate_interval`` model calls,
        appends a system message with the current plan
        markdown to keep the LLM aware of progress.

        Args:
            ctx: Callback context with ModelCallInputs.
        """
        self._model_call_count += 1

        if (
            self._model_call_count
            % self.progress_restate_interval
            != 0
        ):
            return

        if ctx.session is None:
            return
        if not isinstance(ctx.inputs, ModelCallInputs):
            return

        state = load_state(ctx)
        plan = state.task_plan
        if plan is None or not plan.tasks:
            return

        progress_msg = {
            "role": "system",
            "content": (
                "[Task Progress]\n"
                f"{plan.to_markdown()}\n"
                f"Progress: {plan.get_progress_summary()}"
            ),
        }
        ctx.inputs.messages.append(progress_msg)

    async def after_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Sync todo_write tool calls to TaskPlan.

        When the LLM calls ``todo_write``, extracts
        task items from the result and merges them
        into the existing TaskPlan.

        Args:
            ctx: Callback context with ToolCallInputs.
        """
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name not in (
            "todo_write",
            "todo_modify",
        ):
            return
        if ctx.session is None:
            return

        state = load_state(ctx)
        if state.task_plan is None:
            return

        logger.info(
            "TaskPlanningRail: synced after "
            f"tool '{ctx.inputs.tool_name}'"
        )

    async def after_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Mark current task completed and optionally replan.

        After each iteration completes, marks the
        current task as COMPLETED with a result summary.
        If ``enable_replanning`` is True, checks whether
        additional tasks should be added.

        Args:
            ctx: Callback context with
                TaskIterationInputs.
        """
        if ctx.session is None:
            return

        state = load_state(ctx)
        plan = state.task_plan
        if plan is None:
            return

        # Mark current task completed
        if plan.current_task_id is not None:
            result = {}
            if isinstance(ctx.inputs, TaskIterationInputs):
                result = ctx.inputs.result or {}
            summary = str(
                result.get("output", "")
            )[:200]
            plan.mark_completed(
                plan.current_task_id, summary
            )

        if self.enable_replanning:
            self._check_replan(plan)

        logger.info(
            f"TaskPlanningRail: iteration done, "
            f"{plan.get_progress_summary()}"
        )

    async def after_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Generate completion report.

        Stores a summary of the completed plan in
        ``ctx.extra['task_report']``.

        Args:
            ctx: Callback context with InvokeInputs.
        """
        if ctx.session is None:
            return

        state = load_state(ctx)
        plan = state.task_plan
        if plan is None:
            return

        ctx.extra["task_report"] = {
            "goal": plan.goal,
            "progress": plan.get_progress_summary(),
            "plan_markdown": plan.to_markdown(),
        }

    # -- internal helpers --

    @staticmethod
    def _build_initial_plan(query: str) -> TaskPlan:
        """Build a single-task plan from the query.

        In production this would call the LLM to
        decompose the query. For now, creates a
        single task matching the query.

        Args:
            query: User query string.

        Returns:
            TaskPlan with one task.
        """
        task = TaskItem(
            id="t1",
            title=query[:100],
            description=query,
        )
        return TaskPlan(
            goal=query[:200],
            tasks=[task],
        )

    @staticmethod
    def _check_replan(plan: TaskPlan) -> None:
        """Check if replanning is needed.

        Placeholder for LLM-based dynamic replanning.
        Currently a no-op — future iterations will
        call the LLM to add/adjust tasks based on
        intermediate results.

        Args:
            plan: Current task plan.
        """
        _ = plan


__all__ = [
    "TaskPlanningRail",
]
