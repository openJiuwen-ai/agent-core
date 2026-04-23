# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RLRail for RL training.

Extends EvolutionRail to provide RL-specific trajectory collection:
- Standard LLM/tool call tracking (via EvolutionRail base class)
- RL-specific metadata enhancement (turn_id, case_id in step meta)

This rail is designed for offline RL training scenarios where
complete trajectory data is needed for PPO/GRPO training.
"""

from typing import Optional

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.harness.rails.evolution_rail import EvolutionRail


class RLRail(EvolutionRail):
    """Rail that collects trajectory data for RL training.

    Extends EvolutionRail to provide:
    - Automatic trajectory collection via base class (after_invoke handles build/save)
    - Step-level metadata enhancement (turn_id, case_id)

    Attributes:
        priority: Rail priority (100 = high priority for accurate data capture)
    """

    priority = 100

    def __init__(
        self,
        session_id: str = "",
        source: str = "rl_offline",
        case_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Initialize the RL rail.

        Args:
            session_id: Session identifier for the trajectory
            source: Source type ("offline" for RL training, "online" for inference)
            case_id: Optional case ID for grouping related trajectories
            **kwargs: Passed to EvolutionRail base class
        """
        super().__init__(**kwargs)
        self._session_id = session_id
        self._source = source
        self._case_id = case_id

        # State tracking (RL-specific)
        self._llm_step_count: int = 0

    # ------------------------------------------------------------------
    # EvolutionRail extension points (RL-specific overrides)
    # ------------------------------------------------------------------

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize RL-specific state at the start of invoke."""
        self._llm_step_count = 0

    async def _on_after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called after each model call - increment step and enhance metadata."""
        self._llm_step_count += 1

        # Enhance last step's metadata with RL-specific tracking info
        if self._builder is not None and self._builder.steps:
            last_step = self._builder.steps[-1]
            if last_step.kind == "llm":
                last_step.meta.update({
                    "turn_id": self._llm_step_count - 1,  # 0-indexed
                    "source": self._source,
                    "case_id": self._case_id,
                })


__all__ = ["RLRail"]
