# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""PromptSearchOptimizer — the RLAF-P prompt-search loop, as a BaseOptimizer.

An RL-style feedback loop that improves a system prompt over repeated
executions of similar tasks, without training any model weights. A Policy
(LLM) proposes N candidate prompts per round, an Environment executes each,
a RewardModel scores the results, a DriftJudge guards the original objective,
and a compressed optimization history steers the next round.

Two ways to use it:

* **Standalone** — call :meth:`optimize` directly with a
  :class:`PromptTaskSpec`; no bound ``LLMCall`` or ``Trainer`` required. This
  is the black-box path: give it an objective and some cases, get back the
  best prompt found and the full run trace.
* **Bound to a Trainer** — like :class:`InstructionOptimizer` /
  :class:`JointOptimizer`, bind it to an agent's ``LLMCall`` parameters and
  drive it through :meth:`backward` / :meth:`update` from
  :class:`~openjiuwen.dev_tools.tune.trainer.trainer.Trainer`. Internally
  ``_backward`` builds a :class:`PromptTaskSpec` from the bound parameter's
  current prompt plus the epoch's evaluated cases and runs the same loop;
  every candidate prompt tried (not only the winner) is exposed via
  ``TextualParameter.get_candidates("system_prompt")`` so a multi-candidate
  search — see ``Trainer.search_prompt_candidates`` — can evaluate more than
  the single best one this run already picked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.dev_tools.tune.evaluator.evaluator import BaseEvaluator, DefaultEvaluator
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer
from openjiuwen.dev_tools.tune.optimizer.prompt_search.convergence import ConvergenceDetector
from openjiuwen.dev_tools.tune.optimizer.prompt_search.drift import (
    DriftJudge,
    LLMDriftJudge,
    NullDriftJudge,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.environment import (
    LLMEnvironment,
    PromptEnvironment,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.history import (
    HistoryCompressor,
    HistoryEntry,
    OptimizationHistory,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.memory import NullPromptMemory, PromptMemory
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    Evaluation,
    Execution,
    IterationRecord,
    OptimizationResult,
    PromptCandidate,
    PromptRecord,
    PromptTaskCase,
    PromptTaskSpec,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.policy import (
    LLMPromptPolicy,
    PolicyRequest,
    PromptPolicy,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.reward import (
    CompletenessReward,
    CompositeReward,
    CorrectnessReward,
    CostReward,
    LatencyReward,
    RewardModel,
    TokenUsageReward,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.run_log import NullRunLogger, OptimizerRunLogger
from openjiuwen.dev_tools.tune.utils import TuneUtils

DEFAULT_REWARD_WEIGHTS: Dict[str, float] = {
    "correctness": 1.0,
    "completeness": 0.3,
    "latency": 0.1,
    "token_usage": 0.1,
    "cost": 0.0,
}

_CORRECTNESS_METRIC_HINT = (
    "If the expected answer text is actually the task objective (no fixed expected "
    "value was available for this case), judge whether the model answer plausibly and "
    "correctly fulfills that objective rather than requiring a verbatim match."
)


class PromptSearchOptimizer(BaseOptimizer):
    """Iterative, multi-candidate prompt optimizer. See module docstring."""

    def __init__(
        self,
        model_config: Optional[ModelRequestConfig] = None,
        model_client_config: Optional[ModelClientConfig] = None,
        parameters: Optional[Dict[str, LLMCall]] = None,
        *,
        judge_model_config: Optional[ModelRequestConfig] = None,
        judge_model_client_config: Optional[ModelClientConfig] = None,
        evaluator: Optional[BaseEvaluator] = None,
        candidate_prompts: int = 5,
        max_iterations: int = 6,
        parallel_execution: bool = True,
        convergence_threshold: float = 0.01,
        convergence_window: int = 3,
        drift_penalty: float = 0.5,
        min_correctness: float = 0.5,
        policy_temperature: float = 0.9,
        reward_weights: Optional[Dict[str, float]] = None,
        policy: Optional[PromptPolicy] = None,
        environment: Optional[PromptEnvironment] = None,
        reward_model: Optional[RewardModel] = None,
        drift_judge: Optional[DriftJudge] = None,
        memory: Optional[PromptMemory] = None,
        history_compressor: Optional[HistoryCompressor] = None,
        run_logger: Optional[OptimizerRunLogger] = None,
    ) -> None:
        """Build the optimizer.

        ``model_config`` / ``model_client_config`` are only required when at
        least one default collaborator is *not* overridden — every LLM-backed
        default (policy, environment, evaluator, drift judge, history
        compressor) is built lazily, so a fully-overridden optimizer (as in
        tests, or a caller supplying its own policy/environment/reward/drift)
        never needs to construct an LLM client at all.
        """
        super().__init__(parameters)

        self._model_config = model_config
        self._model_client_config = model_client_config
        self._judge_model_config = judge_model_config or model_config
        self._judge_model_client_config = judge_model_client_config or model_client_config
        self._policy_model_cache: Optional[Model] = None
        self._judge_model_cache: Optional[Model] = None

        self._candidate_prompts = max(1, int(candidate_prompts))
        self._max_iterations = max(1, int(max_iterations))
        self._parallel_execution = bool(parallel_execution)
        self._convergence_threshold = max(0.0, float(convergence_threshold))
        self._convergence_window = max(1, int(convergence_window))
        self._drift_penalty = max(0.0, float(drift_penalty))
        self._policy_temperature = float(policy_temperature)

        self._policy = policy or LLMPromptPolicy(self._policy_model())
        self._environment = environment or LLMEnvironment(
            self._policy_model(),
            parallel=self._parallel_execution,
            max_concurrency=self._candidate_prompts,
        )

        if reward_model is not None:
            self._reward_model = reward_model
        else:
            evaluator = evaluator or DefaultEvaluator(
                self._judge_model_config, self._judge_model_client_config,
                metric=_CORRECTNESS_METRIC_HINT,
            )
            weights = dict(reward_weights) if reward_weights else dict(DEFAULT_REWARD_WEIGHTS)
            self._reward_model = CompositeReward(
                [
                    CorrectnessReward(evaluator, max_concurrency=self._candidate_prompts),
                    CompletenessReward(),
                    LatencyReward(),
                    TokenUsageReward(),
                    CostReward(),
                ],
                weights,
                min_correctness=min_correctness,
                drift_penalty=self._drift_penalty,
            )
        self._drift_judge = drift_judge or (
            NullDriftJudge() if self._drift_penalty <= 0 else LLMDriftJudge(self._judge_model())
        )
        self._memory = memory or NullPromptMemory()
        self._compressor = history_compressor or HistoryCompressor(self._policy_model())
        self._log = run_logger or NullRunLogger()

        self._objectives: Dict[str, str] = {}
        self._last_results: Dict[str, OptimizationResult] = {}

    def _policy_model(self) -> Optional[Model]:
        """Lazily build the shared policy/environment LLM client, if actually needed."""
        if self._policy_model_cache is None and self._model_client_config is not None:
            self._policy_model_cache = Model(self._model_client_config, self._model_config)
        return self._policy_model_cache

    def _judge_model(self) -> Optional[Model]:
        """Lazily build the shared judge/drift LLM client, if actually needed."""
        if self._judge_model_cache is None and self._judge_model_client_config is not None:
            self._judge_model_cache = Model(self._judge_model_client_config, self._judge_model_config)
        return self._judge_model_cache

    # -- standalone entrypoint -------------------------------------------------

    async def optimize(self, task: PromptTaskSpec) -> OptimizationResult:
        """Run the full search loop for ``task`` and return the best prompt found."""

        run_id = _new_run_id()
        self._log.reset()
        self._log.record(
            "run.start",
            run_id=run_id,
            objective=task.objective,
            candidate_prompts=self._candidate_prompts,
            max_iterations=self._max_iterations,
        )

        history = OptimizationHistory()
        similar = self._safe_search(task)
        if similar:
            self._log.record("memory.warm_start", count=len(similar))

        convergence = ConvergenceDetector(
            threshold=self._convergence_threshold, window=self._convergence_window
        )

        best_prompt = task.base_prompt
        best_score = float("-inf")
        iterations: list = []
        converged = False
        convergence_reason = ""

        for iteration in range(1, self._max_iterations + 1):
            request = PolicyRequest(
                task=task,
                history=history,
                num_candidates=self._candidate_prompts,
                iteration=iteration,
                temperature=self._policy_temperature,
                similar_records=similar if iteration == 1 else [],
            )
            candidates = await self._policy.generate(request)
            self._log.record(
                "policy.candidates",
                iteration=iteration,
                count=len(candidates),
                candidates=[c.to_dict() for c in candidates],
            )

            executions = await self._execute_all(candidates, task)
            drift_scores = await self._drift_all(candidates, task)
            breakdowns = await self._reward_model.evaluate(executions, task, drift_scores)
            evaluations = [Evaluation(ex, bd) for ex, bd in zip(executions, breakdowns)]

            best_eval = max(evaluations, key=lambda e: e.score, default=None)
            if best_eval is None:
                self._log.record("iteration.empty", iteration=iteration)
                break

            observations = _observations(best_eval)
            history.add(
                HistoryEntry(
                    iteration=iteration,
                    prompt=best_eval.candidate.prompt,
                    reward=best_eval.score,
                    observations=observations,
                )
            )
            history.compressed = await self._compressor.compress(history)

            state = convergence.update(best_eval.score)

            record = IterationRecord(
                iteration=iteration,
                evaluations=evaluations,
                best_candidate_id=best_eval.candidate.candidate_id,
                best_score=best_eval.score,
                converged=state.converged,
                convergence_reason=state.reason,
                observations=observations,
            )
            iterations.append(record)
            self._log.record(
                "iteration.done",
                iteration=iteration,
                best_score=round(best_eval.score, 4),
                best_candidate_id=best_eval.candidate.candidate_id,
                moving_average=round(state.moving_average, 4),
                variance=round(state.variance, 6),
                drift=round(best_eval.reward.drift, 4),
                reward_breakdown=best_eval.reward.to_dict(),
                observations=observations,
            )

            if best_eval.score > best_score:
                best_score = best_eval.score
                best_prompt = best_eval.candidate.prompt

            if state.converged:
                converged = True
                convergence_reason = state.reason
                self._log.record("run.converged", iteration=iteration, reason=state.reason)
                break

        if not converged and iterations:
            convergence_reason = "max_iterations reached"

        self._persist_best(task, best_prompt, best_score, iterations)

        result = OptimizationResult(
            success=bool(iterations),
            best_prompt=best_prompt,
            best_score=best_score if best_score != float("-inf") else 0.0,
            iterations=iterations,
            converged=converged,
            convergence_reason=convergence_reason,
            detail="" if iterations else "no iterations produced a candidate",
            run_id=run_id,
        )
        self._log.record(
            "run.done",
            run_id=run_id,
            best_score=round(result.best_score, 4),
            iterations=len(iterations),
            converged=converged,
            reason=convergence_reason,
        )
        return result

    async def _execute_all(self, candidates: list, task: PromptTaskSpec) -> list:
        if self._parallel_execution:
            return list(
                await asyncio.gather(*(self._environment.execute(c, task) for c in candidates))
            )
        return [await self._environment.execute(c, task) for c in candidates]

    async def _drift_all(self, candidates: list, task: PromptTaskSpec) -> dict:
        if self._drift_penalty <= 0:
            return {c.candidate_id: 0.0 for c in candidates}
        scores = await asyncio.gather(
            *(self._drift_judge.deviation(task.objective, c) for c in candidates)
        )
        return {c.candidate_id: s for c, s in zip(candidates, scores)}

    def _safe_search(self, task: PromptTaskSpec) -> list:
        try:
            return self._memory.search_similar(task, top_k=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"PromptSearchOptimizer: memory search failed: {exc}")
            return []

    def _persist_best(
        self, task: PromptTaskSpec, best_prompt: str, best_score: float, iterations: list
    ) -> None:
        if not iterations or not best_prompt or best_score == float("-inf"):
            return
        best_eval = _best_evaluation(iterations)
        metrics = best_eval.reward.raw_components if best_eval else {}
        observations = best_eval.reward.notes if best_eval else []
        baseline = self._safe_baseline(task.objective)
        record = PromptRecord(
            prompt=best_prompt,
            reward=best_score,
            objective=task.objective,
            task_characteristics=task.characteristics,
            metrics=dict(metrics),
            observations=list(observations),
            metadata={"iterations": len(iterations), **task.metadata},
            baseline_reward=baseline.reward if baseline is not None else None,
        )
        try:
            self._memory.add(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"PromptSearchOptimizer: failed to persist best prompt: {exc}")

    def _safe_baseline(self, objective: str) -> Optional[PromptRecord]:
        try:
            return self._memory.best_for_objective(objective)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"PromptSearchOptimizer: baseline lookup failed: {exc}")
            return None

    # -- BaseOptimizer / Trainer integration -----------------------------------

    def bind_parameter(self, parameters: Optional[Dict[str, LLMCall]]) -> None:
        super().bind_parameter(parameters)
        if not parameters:
            return
        # Snapshot each parameter's starting prompt as the drift-protection
        # objective — it must stay fixed across epochs even as the prompt
        # itself is rewritten, or drift protection would be measuring against
        # a moving target.
        for name, param in self._parameters.items():
            if name not in self._objectives:
                self._objectives[name] = TuneUtils.get_content_string_from_template(
                    param.llm_call.get_system_prompt()
                )

    def _backward(self, evaluated_cases: list) -> None:
        for name, param in self._parameters.items():
            if param.llm_call.get_freeze_system_prompt():
                continue
            base_prompt = TuneUtils.get_content_string_from_template(param.llm_call.get_system_prompt())
            objective = self._objectives.get(name) or base_prompt
            task = PromptTaskSpec(
                objective=objective,
                base_prompt=base_prompt,
                cases=[PromptTaskCase(case=ec.case) for ec in evaluated_cases],
            )
            result = asyncio.run(self.optimize(task))
            self._last_results[name] = result
            param.set_gradient("system_prompt", result.best_prompt or base_prompt)
            param.set_candidates("system_prompt", result.candidate_prompts or [result.best_prompt])

    def _update(self) -> None:
        for name, param in self._parameters.items():
            if param.llm_call.get_freeze_system_prompt():
                continue
            gradient = param.get_gradient("system_prompt")
            if gradient:
                param.llm_call.update_system_prompt(gradient)

    def last_result(self, name: str) -> Optional[OptimizationResult]:
        """The full :class:`OptimizationResult` for parameter ``name``'s last epoch."""
        return self._last_results.get(name)


def _observations(evaluation: Evaluation) -> list:
    obs: list = []
    if evaluation.candidate.rationale:
        obs.append(evaluation.candidate.rationale)
    obs.extend(evaluation.reward.notes)
    components = evaluation.reward.raw_components
    if components:
        strongest = max(components, key=components.get)
        weakest = min(components, key=components.get)
        if strongest != weakest:
            obs.append(
                f"strongest metric: {strongest} ({components[strongest]:.2f}); "
                f"weakest: {weakest} ({components[weakest]:.2f})"
            )
    return obs


def _best_evaluation(iterations: list) -> Optional[Evaluation]:
    best: Optional[Evaluation] = None
    for record in iterations:
        for evaluation in record.evaluations:
            if best is None or evaluation.score > best.score:
                best = evaluation
    return best


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


__all__ = ["PromptSearchOptimizer", "DEFAULT_REWARD_WEIGHTS"]
