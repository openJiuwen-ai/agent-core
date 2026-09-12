# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""BudgetNoticeRail — warn the agent before a task-loop budget runs out.

The rail reads the *actual* limits configured on the loop's stop-condition
evaluators (``LoopCoordinator.budget_limits``) and the current consumption
(rounds, tokens, wall-clock), then injects a system-prompt "wind down" notice
when any resource is close to its limit. Because it never carries its own copy
of the limits, its warnings cannot drift from what really stops the loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.budget_notice import (
    build_budget_notice_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail


class BudgetNoticeRail(DeepAgentRail):
    """Inject a prompt notice when a task-loop budget is nearly exhausted.

    The rail is a passive observer: it does not stop the loop (the
    stop-condition evaluators own that). It only tells the model to wind down
    so the run ends with a usable partial result instead of being cut off.

    Args:
        enabled: Master switch. When false the section is always removed.
        round_remaining: Absolute number of remaining rounds that triggers the
            notice. Overrides ``round_ratio`` for the rounds budget.
        round_ratio: Fraction of the rounds budget remaining that triggers the
            notice (default: 20%).
        token_ratio: Fraction of the token budget remaining that triggers the
            notice (default: 15%).
        time_ratio: Fraction of the wall-clock budget remaining that triggers
            the notice (default: 15%).
    """

    priority = 80

    _DEFAULT_ROUND_RATIO = 0.2
    _DEFAULT_TOKEN_RATIO = 0.15
    _DEFAULT_TIME_RATIO = 0.15

    def __init__(
        self,
        *,
        enabled: bool = True,
        round_remaining: Optional[int] = None,
        round_ratio: Optional[float] = None,
        token_ratio: Optional[float] = None,
        time_ratio: Optional[float] = None,
    ) -> None:
        super().__init__()
        self._enabled = enabled
        self._round_remaining = round_remaining
        self._round_ratio = round_ratio
        self._token_ratio = token_ratio
        self._time_ratio = time_ratio
        self.system_prompt_builder = None

    # -- lifecycle --

    def init(self, agent: Any) -> None:
        """Acquire the system prompt builder from the agent."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        """Remove the injected section and release the builder reference."""
        self._remove_section()

    async def before_invoke(self, ctx: AgentCallbackContext, **kwargs: Any) -> None:
        """Clear any stale notice at the start of a new invocation."""
        self._remove_section()

    async def before_model_call(self, ctx: AgentCallbackContext, **kwargs: Any) -> None:
        """Inject or remove the budget notice before every model call."""
        if not self._enabled or self.system_prompt_builder is None:
            self._remove_section()
            return

        coordinator = getattr(getattr(ctx, "agent", None), "loop_coordinator", None)
        notices = self._near_limit_notices(coordinator)
        section = build_budget_notice_section(
            getattr(self.system_prompt_builder, "language", "cn"),
            notices,
        )
        self._remove_section()
        if section is not None:
            self.system_prompt_builder.add_section(section)

    # -- internals --

    def _remove_section(self) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.BUDGET_NOTICE)

    def _near_limit_notices(
        self, coordinator: Any,
    ) -> List[Dict[str, Any]]:
        """Return one notice per budget that is at or below its threshold."""
        if coordinator is None:
            return []

        used_by_kind = {
            "rounds": getattr(coordinator, "current_iteration", 0),
            "tokens": getattr(coordinator, "token_usage", 0),
            "seconds": getattr(coordinator, "elapsed_seconds", 0.0),
        }

        notices: List[Dict[str, Any]] = []
        for budget in coordinator.budget_limits():
            kind = budget.kind
            limit = float(budget.limit)
            if limit <= 0:
                continue
            used = float(used_by_kind.get(kind, 0.0) or 0.0)
            remaining = limit - used
            if remaining > self._threshold(kind, limit):
                continue
            notices.append(
                {
                    "kind": kind,
                    "used": used,
                    "limit": limit,
                    "remaining": max(0.0, remaining),
                }
            )
        return notices

    def _threshold(self, kind: str, limit: float) -> float:
        """Resolve the warning threshold (absolute or ratio) for a budget kind."""
        if kind == "rounds":
            if self._round_remaining is not None:
                return max(0.0, float(self._round_remaining))
            ratio = (
                self._round_ratio
                if self._round_ratio is not None
                else self._DEFAULT_ROUND_RATIO
            )
            return max(0.0, ratio) * limit
        if kind == "tokens":
            ratio = (
                self._token_ratio
                if self._token_ratio is not None
                else self._DEFAULT_TOKEN_RATIO
            )
            return max(0.0, ratio) * limit
        if kind == "seconds":
            ratio = (
                self._time_ratio
                if self._time_ratio is not None
                else self._DEFAULT_TIME_RATIO
            )
            return max(0.0, ratio) * limit
        return 0.0


__all__ = [
    "BudgetNoticeRail",
]
