# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The rail that makes a swarmflow token budget real, on both counts.

The engine sees one ``agent()`` as a single call, but a worker is a whole agent
loop behind it — many model calls, any one of which can blow the run's ceiling.
So enforcement has to live where the calls happen, which is here: the backend
attaches this rail to every harness it spawns, and each of them bills the run's
shared :class:`BudgetLedger` and stops itself once the ledger runs dry.

It also supplies the real numbers. Token counts come off the model client's
response (``AssistantMessage.usage_metadata``), replacing an estimate that
divided prompt length by four and never saw the loop's other calls at all.
"""
from __future__ import annotations

from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)


class SwarmflowBudgetRail(AgentRail):
    """Bill one agent's model calls to the run's ledger; stop it when dry.

    Attributes:
        call_tokens: Tokens this agent has reported so far — the backend reads
            it to attribute a cost to the ``agent()`` call as a whole.
    """

    #: Ahead of the worker's other rails (higher runs first): a call the run
    #: cannot pay for should be refused before anything else prepares for it.
    priority: int = 950

    def __init__(self, budget: BudgetLedger) -> None:
        super().__init__()
        self._budget = budget
        self.call_tokens: int = 0

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Refuse to start a model call the run can no longer pay for.

        The ledger is shared, so this also catches the budget being drained by
        a *sibling* worker while this one was mid-loop.
        """
        self._stop_if_exhausted(ctx, "before")

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Bill the call that just returned, then stop if that emptied the pot."""
        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return

        tokens = _usage_tokens(inputs.response)
        if tokens > 0:
            self.call_tokens += tokens
            self._budget.add(tokens)
        self._stop_if_exhausted(ctx, "after")

    def _stop_if_exhausted(self, ctx: AgentCallbackContext, when: str) -> None:
        """End this agent's round when the ledger has nothing left.

        A force-finish rather than an exception: the run is over budget, not
        broken, so the work done so far is kept and returned normally. The
        engine's own gate then stops the *next* ``agent()`` from starting.
        """
        if not self._budget.exhausted:
            return
        team_logger.warning(
            "[swarmflow] token budget exhausted ({}/{}); finishing agent round ({} model call)",
            self._budget.spent,
            self._budget.total,
            when,
        )
        ctx.request_force_finish(
            {
                "reason": (
                    f"swarmflow token budget exhausted "
                    f"({self._budget.spent}/{self._budget.total})"
                )
            }
        )


def _usage_tokens(response: object | None) -> int:
    """Total tokens one model response reports, or 0 when it reports none.

    Reads ``AssistantMessage.usage_metadata`` — what the provider itself billed,
    which is the only number worth enforcing against. Providers that omit usage
    (or a stub model in a test) yield 0 rather than a guess: silently swapping in
    an estimate would make the ceiling mean something different per provider.
    ``total_tokens`` can also be absent while the input/output split is present,
    so fall back to their sum.
    """
    if response is None:
        return 0
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0
    total = getattr(usage, "total_tokens", 0) or 0
    if total > 0:
        return int(total)
    inputs = getattr(usage, "input_tokens", 0) or 0
    outputs = getattr(usage, "output_tokens", 0) or 0
    return int(inputs + outputs)


__all__ = ["SwarmflowBudgetRail"]
