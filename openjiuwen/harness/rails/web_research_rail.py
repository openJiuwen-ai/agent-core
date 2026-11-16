# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind bounded retrieval state to an invocation, never a shared tool instance."""

from dataclasses import replace

from openjiuwen.core.common.run_context import RunContext
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.web._budget import WEB_RESEARCH_BUDGET_KEY, WebResearchBudget


class WebResearchBudgetRail(DeepAgentRail):
    def __init__(self):
        super().__init__()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        budget = WebResearchBudget.from_env()
        ctx.extra[WEB_RESEARCH_BUDGET_KEY] = budget
        if isinstance(ctx.inputs, InvokeInputs):
            if isinstance(ctx.inputs.query, str):
                budget.original_task = ctx.inputs.query
            original = ctx.inputs.run_context or RunContext()
            extra = dict(original.extra)
            extra[WEB_RESEARCH_BUDGET_KEY] = budget
            ctx.inputs.run_context = replace(original, extra=extra)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if WEB_RESEARCH_BUDGET_KEY in ctx.extra:
            return
        # Task-loop rounds pass RunContext across the outer/inner agent boundary.
        run_context = ctx.extra.get("run_context")
        budget = run_context.extra.get(WEB_RESEARCH_BUDGET_KEY) if isinstance(run_context, RunContext) else None
        ctx.extra[WEB_RESEARCH_BUDGET_KEY] = (
            budget if isinstance(budget, WebResearchBudget) else WebResearchBudget.from_env()
        )
        active_budget = ctx.extra[WEB_RESEARCH_BUDGET_KEY]
        if not active_budget.original_task:
            for message in reversed(getattr(ctx.inputs, "messages", [])):
                role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
                content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
                if role == "user" and isinstance(content, str):
                    active_budget.original_task = content
                    break
