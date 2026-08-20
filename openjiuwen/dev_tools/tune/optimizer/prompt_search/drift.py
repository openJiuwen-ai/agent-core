# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Drift judge interface.

Semantic drift protection approximates a KL constraint with an LLM judge: given
the original objective and a candidate prompt, return a deviation score in
``[0, 1]`` (0 = faithful, 1 = the prompt has repurposed the task). The optimizer
subtracts ``drift_penalty * deviation`` from the reward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import PromptCandidate
from openjiuwen.dev_tools.tune.utils import TuneUtils


class DriftJudge(ABC):
    @abstractmethod
    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        """Return semantic deviation of ``candidate`` from ``original_objective`` in [0, 1]."""


class NullDriftJudge(DriftJudge):
    """No-op judge (always 0.0) — useful for tests and drift-insensitive tasks."""

    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        return 0.0


_SYSTEM = (
    "You measure whether an optimized system prompt still serves the original task "
    "objective. Output only valid JSON wrapped in ```json fences."
)

_PROMPT = """\
Original task objective:
{objective}

Candidate system prompt:
{prompt}

Does the candidate system prompt still faithfully pursue the SAME objective, or has it
drifted toward a different task, added unrelated goals, or narrowed/broadened the scope?

Return ONLY valid JSON wrapped in ```json fences:
```json
{{"deviation": <float 0..1>, "reason": "<short reason>"}}
```
where 0.0 means identical intent and 1.0 means a completely different task.
"""


class LLMDriftJudge(DriftJudge):
    """Approximate KL divergence from the objective with an LLM judge."""

    def __init__(self, model: Model) -> None:
        self._model = model

    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        if not (candidate.prompt or "").strip():
            return 1.0
        prompt = _PROMPT.format(objective=original_objective, prompt=candidate.prompt)
        try:
            response = await self._model.invoke(
                messages=[SystemMessage(content=_SYSTEM), UserMessage(content=prompt)],
            )
            data = TuneUtils.parse_json_from_llm_response(response.content) or {}
            value = float(data.get("deviation", 0.0))
            return max(0.0, min(1.0, value))
        except Exception as exc:  # noqa: BLE001
            # Fail safe: assume no drift rather than penalize a candidate for a judge outage.
            logger.warning(f"LLMDriftJudge: judge failed, assuming 0 drift: {exc}")
            return 0.0


__all__ = ["DriftJudge", "NullDriftJudge", "LLMDriftJudge"]
