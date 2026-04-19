# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budget rail — session time + API cost + CI gate logging.

Merges the former ``SessionBudgetRail``,
``CostBudgetRail``, and ``CIGateRail`` into one rail.
"""
from __future__ import annotations

import logging

from openjiuwen.auto_harness.infra.session_budget import (
    SessionBudgetController,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

# Rough per-token cost estimates (USD).
_INPUT_COST_PER_TOKEN = 3e-6
_OUTPUT_COST_PER_TOKEN = 15e-6


class BudgetRail(DeepAgentRail):
    """Monitor wall-clock + cost budget, log CI iterations.

    - ``before_tool_call``: force-finish if time exceeded.
    - ``after_model_call``: estimate cost, force-finish
      if cost exceeded.
    - ``before/after_task_iteration``: CI gate logging.

    Args:
        budget: Session budget controller instance.
    """

    def __init__(
        self,
        budget: SessionBudgetController,
    ) -> None:
        super().__init__()
        self._budget = budget

    # -- Session budget (before_tool_call) ----------------

    async def before_tool_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Check budget before each tool call."""
        if self._budget.should_stop:
            logger.warning("Session budget exceeded")
            ctx.request_force_finish(
                {"reason": "Session budget exceeded"}
            )

    # -- Cost budget (after_model_call) -------------------

    async def after_model_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Estimate cost from model response usage."""
        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return

        response = inputs.response
        if response is None:
            return

        usage = getattr(response, "usage", None)
        if usage is None:
            return

        input_tokens = getattr(
            usage, "input_tokens", 0,
        )
        output_tokens = getattr(
            usage, "output_tokens", 0,
        )
        cost = (
            input_tokens * _INPUT_COST_PER_TOKEN
            + output_tokens * _OUTPUT_COST_PER_TOKEN
        )
        if cost > 0:
            self._budget.add_cost(cost)
            logger.debug("API cost +$%.6f", cost)

        if self._budget.should_stop:
            logger.warning("Cost budget exceeded")
            ctx.request_force_finish(
                {"reason": "Cost budget exceeded"}
            )

    # -- CI gate logging (iteration boundaries) -----------

    async def before_task_iteration(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
    ) -> None:
        """Log iteration start."""
        logger.info("CI gate rail: iteration starting")

    async def after_task_iteration(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
    ) -> None:
        """Log iteration completion."""
        logger.info("CI gate rail: iteration complete")
