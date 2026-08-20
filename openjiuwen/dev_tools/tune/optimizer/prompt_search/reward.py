# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Reward interfaces, built-in components, and the composite reward model.

A :class:`RewardComponent` maps one execution to a scalar (ideally already in
``[0, 1]``). A :class:`RewardModel` combines components into a comparable
:class:`RewardBreakdown` per candidate — :class:`CompositeReward` is the
default. ``CorrectnessReward`` judges via a :class:`BaseEvaluator` (defaulting
to :class:`DefaultEvaluator`) instead of a bespoke judge prompt, so correctness
grading is shared with the rest of ``dev_tools.tune`` rather than duplicated.

Anti-reward-hacking guards applied by :class:`CompositeReward`:
  * **min-correctness gate** — a candidate scoring below the correctness floor
    cannot win by trading quality for latency/tokens; its reward is capped at
    its correctness.
  * **drift penalty** — semantic deviation from the objective is subtracted.
  * **hidden-set consistency** — if visible correctness far exceeds held-out
    correctness, the candidate is overfitting and is penalized.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.tune.base import Case
from openjiuwen.dev_tools.tune.evaluator.evaluator import BaseEvaluator
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    CaseResult,
    Execution,
    PromptTaskSpec,
)

_OVERFIT_MARGIN = 0.25
_OVERFIT_PENALTY = 0.5


def clamp01(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def min_max_normalize(values: list) -> list:
    """Scale ``values`` into ``[0, 1]``. Constant inputs map to all-1.0 (no signal)."""
    if not values:
        return []
    low = min(values)
    high = max(values)
    spread = high - low
    if spread <= 1e-9:
        return [1.0 for _ in values]
    return [(v - low) / spread for v in values]


class RewardComponent(ABC):
    """One reward metric. Implement :meth:`score` returning a scalar in ``[0, 1]``."""

    name: str = "component"

    @abstractmethod
    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        """Return this metric's raw score for ``execution`` (higher is better)."""


class RewardModel(ABC):
    """Turns executions into comparable rewards.

    Batch-oriented so implementations may normalize across the candidates of
    one iteration (which is what prevents a single runaway metric from
    dominating).
    """

    @abstractmethod
    async def evaluate(
        self,
        executions: list,
        task: PromptTaskSpec,
        drift_scores: dict,
    ) -> list:
        """Score every execution; ``drift_scores`` maps candidate_id -> deviation."""


class CorrectnessReward(RewardComponent):
    """Correctness judged via a :class:`BaseEvaluator` (shared with ``Trainer``).

    When a case carries an explicit expected answer, that is what the
    evaluator grades against; when it doesn't, the task objective stands in as
    the "expected answer" so the same judge can still assess whether the
    output plausibly satisfies the task. Configure ``evaluator``'s ``metric``
    to make that dual usage explicit to the judge model.
    """

    name = "correctness"

    def __init__(self, evaluator: BaseEvaluator, *, max_concurrency: int = 5) -> None:
        self._evaluator = evaluator
        self._sem = asyncio.Semaphore(max(1, max_concurrency))

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        return await self.correctness_on(execution.visible_results, task)

    async def correctness_on(self, results: list, task: PromptTaskSpec) -> float:
        graded = [r for r in results if r.error is None]
        if not graded:
            return 0.0
        expected_by_input = {c.input_text: c.expected_text for c in task.cases}
        scores = await asyncio.gather(
            *(self._judge_case(r, expected_by_input.get(r.case_input, ""), task.objective) for r in graded)
        )
        return sum(scores) / len(scores) if scores else 0.0

    async def _judge_case(self, result: CaseResult, expected: str, objective: str) -> float:
        if not (result.output or "").strip():
            return 0.0
        label_text = expected or objective
        case = Case(inputs={"query": result.case_input}, label={"output": label_text})
        try:
            async with self._sem:
                evaluated = await asyncio.to_thread(self._evaluator.evaluate, case, {"output": result.output})
            return clamp01(evaluated.score)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"CorrectnessReward: evaluator failed, scoring 0.0: {exc}")
            return 0.0


class CompletenessReward(RewardComponent):
    """Heuristic completeness: fraction of visible cases that produced non-trivial output."""

    name = "completeness"

    def __init__(self, *, min_chars: int = 1) -> None:
        self._min_chars = max(1, min_chars)

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        results = execution.visible_results
        if not results:
            return 0.0
        ok = sum(
            1 for r in results if r.error is None and len((r.output or "").strip()) >= self._min_chars
        )
        return ok / len(results)


class LatencyReward(RewardComponent):
    """Lower average latency scores higher. ``half_life_s`` is where the score hits 0.5."""

    name = "latency"

    def __init__(self, *, half_life_s: float = 5.0) -> None:
        self._half_life = max(1e-3, half_life_s)

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        return 1.0 / (1.0 + execution.latency_s / self._half_life)


class TokenUsageReward(RewardComponent):
    """Fewer tokens scores higher. ``half_life_tokens`` is where the score hits 0.5."""

    name = "token_usage"

    def __init__(self, *, half_life_tokens: int = 2000) -> None:
        self._half_life = max(1, half_life_tokens)

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        return 1.0 / (1.0 + execution.total_tokens / self._half_life)


class CostReward(RewardComponent):
    """Lower monetary cost scores higher. ``price_per_mtok`` in currency per 1M tokens."""

    name = "cost"

    def __init__(self, *, price_per_mtok: float = 0.0, half_life_cost: float = 0.01) -> None:
        self._price = max(0.0, price_per_mtok)
        self._half_life = max(1e-9, half_life_cost)

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        cost = execution.total_tokens / 1_000_000 * self._price
        return 1.0 / (1.0 + cost / self._half_life)


class StructuredValidationReward(RewardComponent):
    """Fraction of visible outputs that pass a validator (regex, JSON-parse, or callable)."""

    name = "structured_validation"

    def __init__(self, validator: Callable[[str], bool]) -> None:
        self._validator = validator

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        results = execution.visible_results
        if not results:
            return 0.0
        ok = 0
        for r in results:
            try:
                if self._validator(r.output or ""):
                    ok += 1
            except Exception:  # noqa: BLE001
                continue
        return ok / len(results)


class CustomReward(RewardComponent):
    """Wrap any user callable ``(execution, task) -> float`` (sync or async)."""

    def __init__(self, name: str, fn: Callable) -> None:
        self.name = name
        self._fn = fn

    async def score(self, execution: Execution, task: PromptTaskSpec) -> float:
        result = self._fn(execution, task)
        if isinstance(result, Awaitable):
            result = await result
        return clamp01(result)


class CompositeReward(RewardModel):
    """Weighted combination of :class:`RewardComponent` instances.

    Built-in components already return bounded ``[0, 1]`` absolute scores, so
    their weighted sum is comparable across iterations (which is what lets
    reward *improve over time*). Cross-candidate min-max normalization is
    therefore opt-in (``normalize=True``) and intended only for custom,
    unbounded metrics.
    """

    def __init__(
        self,
        components: list,
        weights: dict,
        *,
        min_correctness: float = 0.5,
        drift_penalty: float = 0.5,
        normalize: bool = False,
        correctness_name: str = "correctness",
    ) -> None:
        self._components = [c for c in components if weights.get(c.name, 0.0) > 0.0]
        self._weights = dict(weights)
        self._min_correctness = clamp01(min_correctness)
        self._drift_penalty = max(0.0, drift_penalty)
        self._normalize = normalize
        self._correctness_name = correctness_name
        self._correctness = next(
            (c for c in self._components if isinstance(c, CorrectnessReward)), None
        )

    async def evaluate(
        self,
        executions: list,
        task: PromptTaskSpec,
        drift_scores: dict,
    ) -> list:
        from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import RewardBreakdown

        if not executions:
            return []

        raw: dict = {}
        for component in self._components:
            raw[component.name] = await asyncio.gather(
                *(component.score(ex, task) for ex in executions)
            )

        norm = {
            name: (min_max_normalize(values) if self._normalize else list(values))
            for name, values in raw.items()
        }

        hidden_correctness = await self._hidden_correctness(executions, task)
        total_weight = sum(self._weights.get(c.name, 0.0) for c in self._components) or 1.0

        breakdowns = []
        for idx, ex in enumerate(executions):
            components = {name: norm[name][idx] for name in norm}
            raw_components = {name: raw[name][idx] for name in raw}
            weighted = sum(
                self._weights.get(name, 0.0) * value for name, value in components.items()
            )
            score = weighted / total_weight

            correctness = raw_components.get(self._correctness_name, 1.0)
            notes: list = []

            gated = False
            if self._correctness is not None and correctness < self._min_correctness:
                gated = True
                score = min(score, correctness)
                notes.append(
                    f"correctness {correctness:.2f} < gate {self._min_correctness:.2f}: reward capped"
                )

            hid = hidden_correctness.get(ex.candidate.candidate_id)
            if hid is not None and correctness - hid > _OVERFIT_MARGIN:
                gap = correctness - hid
                penalty = _OVERFIT_PENALTY * gap
                score = max(0.0, score - penalty)
                notes.append(
                    f"overfitting: visible {correctness:.2f} vs hidden {hid:.2f} (-{penalty:.2f})"
                )

            drift = clamp01(drift_scores.get(ex.candidate.candidate_id, 0.0))
            drift_applied = self._drift_penalty * drift
            if drift_applied > 0:
                score = max(0.0, score - drift_applied)
                notes.append(f"drift {drift:.2f} penalty -{drift_applied:.2f}")

            if ex.error:
                score = 0.0
                notes.append(f"execution error: {ex.error}")

            breakdowns.append(
                RewardBreakdown(
                    score=clamp01(score),
                    components=components,
                    raw_components=raw_components,
                    correctness=correctness,
                    drift=drift,
                    drift_penalty_applied=drift_applied,
                    gated=gated,
                    notes=notes,
                )
            )
        return breakdowns

    async def _hidden_correctness(self, executions: list, task: PromptTaskSpec) -> dict:
        if self._correctness is None or not task.hidden_cases:
            return {}
        result: dict = {}
        scores = await asyncio.gather(
            *(self._correctness.correctness_on(ex.hidden_results, task) for ex in executions)
        )
        for ex, sc in zip(executions, scores):
            result[ex.candidate.candidate_id] = sc
        return result


__all__ = [
    "clamp01",
    "min_max_normalize",
    "RewardComponent",
    "RewardModel",
    "CorrectnessReward",
    "CompletenessReward",
    "LatencyReward",
    "TokenUsageReward",
    "CostReward",
    "StructuredValidationReward",
    "CustomReward",
    "CompositeReward",
]
