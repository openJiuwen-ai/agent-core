# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Core data models for the prompt-search optimizer (RLAF-P).

These are the shared vocabulary for the Policy, Environment, Reward, DriftJudge,
Memory, and the optimization loop in this package. ``PromptTaskCase`` wraps the
existing :class:`~openjiuwen.dev_tools.tune.base.Case` dataset type instead of
redefining it, so a task built here composes with the rest of ``dev_tools.tune``
(``CaseLoader``, ``DefaultEvaluator``) rather than living beside a parallel case
format.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from openjiuwen.dev_tools.tune.base import Case


def _first_value(data: dict, preferred_keys: tuple = ("query", "output", "input")) -> str:
    for key in preferred_keys:
        if key in data:
            return str(data[key])
    if data:
        return str(next(iter(data.values())))
    return ""


@dataclass
class PromptTaskCase:
    """One evaluation case, plus whether it is held out from the visible set.

    ``hidden`` cases are never shown to the policy; the reward model uses them
    to detect a candidate that overfits the visible cases (reward hacking).
    """

    case: Case
    hidden: bool = False

    @property
    def input_text(self) -> str:
        return _first_value(self.case.inputs, ("query", "input"))

    @property
    def expected_text(self) -> str:
        return _first_value(self.case.label, ("output", "expected"))

    @classmethod
    def from_text(cls, text: str, expected: str = "", *, hidden: bool = False) -> "PromptTaskCase":
        """Convenience constructor for the common single-string input/expected shape."""
        return cls(case=Case(inputs={"query": text}, label={"output": expected}), hidden=hidden)


@dataclass
class PromptTaskSpec:
    """What the optimizer is optimizing a prompt *for*.

    ``objective`` is the immutable intent used for drift protection. ``cases``
    are the evaluation inputs; those flagged ``hidden`` form the held-out set
    used to detect reward hacking.
    """

    objective: str
    cases: list[PromptTaskCase] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    base_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def visible_cases(self) -> list[PromptTaskCase]:
        return [c for c in self.cases if not c.hidden]

    @property
    def hidden_cases(self) -> list[PromptTaskCase]:
        return [c for c in self.cases if c.hidden]

    @property
    def characteristics(self) -> str:
        """A compact text signature used for memory embedding / retrieval."""
        parts = [self.objective]
        if self.constraints:
            parts.append("constraints: " + "; ".join(self.constraints))
        return "\n".join(parts)


@dataclass
class PromptCandidate:
    """One candidate system prompt produced by the Policy."""

    prompt: str
    candidate_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "prompt": self.prompt,
            "rationale": self.rationale,
        }


@dataclass
class CaseResult:
    """Result of running a candidate prompt against one case."""

    case_input: str
    output: str
    latency_s: float = 0.0
    hidden: bool = False
    error: Optional[str] = None


@dataclass
class Execution:
    """The outcome of executing a candidate prompt in the Environment."""

    candidate: PromptCandidate
    case_results: list[CaseResult] = field(default_factory=list)
    latency_s: float = 0.0
    token_usage: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        total = self.token_usage.get("total") if isinstance(self.token_usage, dict) else None
        if isinstance(total, dict):
            try:
                return max(0, int(total.get("total_tokens") or 0))
            except (TypeError, ValueError):
                return 0
        return 0

    @property
    def visible_results(self) -> list[CaseResult]:
        return [r for r in self.case_results if not r.hidden]

    @property
    def hidden_results(self) -> list[CaseResult]:
        return [r for r in self.case_results if r.hidden]

    def combined_output(self, *, hidden: bool = False) -> str:
        results = self.hidden_results if hidden else self.visible_results
        return "\n\n---\n\n".join(r.output for r in results if r.output)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "latency_s": round(self.latency_s, 4),
            "total_tokens": self.total_tokens,
            "error": self.error,
            "outputs": [
                {"input": r.case_input, "output": r.output, "hidden": r.hidden}
                for r in self.case_results
            ],
        }


@dataclass
class RewardBreakdown:
    """Scalar reward plus the per-component contributions that produced it."""

    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    raw_components: dict[str, float] = field(default_factory=dict)
    correctness: float = 0.0
    drift: float = 0.0
    drift_penalty_applied: float = 0.0
    gated: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "raw_components": {k: round(v, 4) for k, v in self.raw_components.items()},
            "correctness": round(self.correctness, 4),
            "drift": round(self.drift, 4),
            "drift_penalty_applied": round(self.drift_penalty_applied, 4),
            "gated": self.gated,
            "notes": list(self.notes),
        }


@dataclass
class Evaluation:
    """A candidate paired with its execution and reward — the loop's unit of comparison."""

    execution: Execution
    reward: RewardBreakdown

    @property
    def candidate(self) -> PromptCandidate:
        return self.execution.candidate

    @property
    def score(self) -> float:
        return self.reward.score

    def to_dict(self) -> dict[str, Any]:
        return {"execution": self.execution.to_dict(), "reward": self.reward.to_dict()}


@dataclass
class IterationRecord:
    """Everything that happened in one optimization iteration (for logging)."""

    iteration: int
    evaluations: list[Evaluation] = field(default_factory=list)
    best_candidate_id: str = ""
    best_score: float = 0.0
    converged: bool = False
    convergence_reason: str = ""
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "best_candidate_id": self.best_candidate_id,
            "best_score": round(self.best_score, 4),
            "converged": self.converged,
            "convergence_reason": self.convergence_reason,
            "observations": list(self.observations),
            "evaluations": [e.to_dict() for e in self.evaluations],
        }


@dataclass
class OptimizationResult:
    """The final product of an optimization run."""

    success: bool
    best_prompt: str
    best_score: float
    iterations: list[IterationRecord] = field(default_factory=list)
    converged: bool = False
    convergence_reason: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    run_id: str = ""

    @property
    def candidate_prompts(self) -> list[str]:
        """Every distinct candidate prompt tried across all iterations, best-first.

        This is what lets a caller (e.g. a Trainer-driven multi-candidate search)
        evaluate more than just the single winner this run already picked.
        """
        seen: dict[str, float] = {}
        for record in self.iterations:
            for evaluation in record.evaluations:
                prompt = evaluation.candidate.prompt
                if prompt and evaluation.score > seen.get(prompt, float("-inf")):
                    seen[prompt] = evaluation.score
        return [p for p, _ in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "best_prompt": self.best_prompt,
            "best_score": round(self.best_score, 4),
            "converged": self.converged,
            "convergence_reason": self.convergence_reason,
            "iteration_count": len(self.iterations),
            "iterations": [i.to_dict() for i in self.iterations],
            "token_usage": self.token_usage,
            "detail": self.detail,
            "run_id": self.run_id,
        }


@dataclass
class PromptRecord:
    """A persisted optimization outcome for the prompt memory.

    ``baseline_reward`` is the best reward previously known for the same
    ``objective`` at the time this record was created (``None`` the first time),
    so ``gain`` (``reward - baseline_reward``) shows the actual improvement, not
    just an absolute score. ``applied`` tracks whether a human has actually
    installed this prompt somewhere; the optimizer never sets it itself.
    """

    prompt: str
    reward: float
    objective: str
    task_characteristics: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    baseline_reward: Optional[float] = None
    applied: bool = False
    applied_at: Optional[float] = None

    @property
    def gain(self) -> float:
        baseline = self.baseline_reward if self.baseline_reward is not None else 0.0
        return self.reward - baseline

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "prompt": self.prompt,
            "reward": self.reward,
            "objective": self.objective,
            "task_characteristics": self.task_characteristics,
            "metrics": self.metrics,
            "observations": list(self.observations),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "baseline_reward": self.baseline_reward,
            "applied": self.applied,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromptRecord":
        baseline = data.get("baseline_reward")
        applied_at = data.get("applied_at")
        return cls(
            prompt=str(data.get("prompt", "")),
            reward=float(data.get("reward", 0.0)),
            objective=str(data.get("objective", "")),
            task_characteristics=str(data.get("task_characteristics", "")),
            metrics=dict(data.get("metrics", {})),
            observations=[str(x) for x in data.get("observations", [])],
            metadata=dict(data.get("metadata", {})),
            record_id=str(data.get("record_id", uuid.uuid4().hex[:12])),
            created_at=float(data.get("created_at", 0.0)),
            baseline_reward=float(baseline) if baseline is not None else None,
            applied=bool(data.get("applied", False)),
            applied_at=float(applied_at) if applied_at is not None else None,
        )


__all__ = [
    "PromptTaskCase",
    "PromptTaskSpec",
    "PromptCandidate",
    "CaseResult",
    "Execution",
    "RewardBreakdown",
    "Evaluation",
    "IterationRecord",
    "OptimizationResult",
    "PromptRecord",
]
