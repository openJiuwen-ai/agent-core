# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run-scoped token ledger — the shared object behind ``budget.*``.

A run's budget is one number burned by many concurrent writers: every LLM call
of every agent the run spawns, each deep inside its own harness. The engine
cannot poll them, so the ledger is passed *by reference* to whoever makes the
calls (see :meth:`AgentBackend.bind_budget`); they report real usage into it as
it happens, and the engine only ever reads it.

That reference sharing is the whole point — an ``int`` field on the runtime
could not be handed to a backend rail, which is why the ceiling used to be
advisory. The ledger is business-agnostic (a counter and a ceiling, no
``agent_teams`` import), so it stays in ``engine/`` next to ``admission.py``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetLedger:
    """Tokens burned by one run, against an optional ceiling.

    Attributes:
        total: The run's token ceiling; ``None`` means unbounded.
        spent: Tokens reported so far. Real usage when the backend reads it off
            the model client's response; an estimate under ``MockBackend``.
    """

    total: int | None = None
    spent: int = 0

    def add(self, tokens: int) -> None:
        """Report ``tokens`` consumed. Non-positive values are ignored."""
        if tokens > 0:
            self.spent += tokens

    def remaining(self) -> int | None:
        """Tokens left before the ceiling, or ``None`` when unbounded.

        Clamped at 0: a run that overshoots (the last call is only accounted
        once it returns) reports no headroom rather than a negative one.
        """
        if self.total is None:
            return None
        return max(0, self.total - self.spent)

    @property
    def exhausted(self) -> bool:
        """Whether the ceiling is reached (always ``False`` when unbounded)."""
        return self.total is not None and self.spent >= self.total


__all__ = ["BudgetLedger"]
