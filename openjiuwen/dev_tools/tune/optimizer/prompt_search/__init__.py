# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""RLAF-P: a multi-candidate prompt-search optimizer for ``dev_tools.tune``.

An RL-style feedback loop that improves a system prompt over repeated
executions of similar tasks — no model weights are trained. A Policy (LLM)
proposes N candidate prompts per round, an Environment executes each, a
RewardModel scores the results, a DriftJudge guards the original objective,
and a compressed optimization history steers the next round.

Built on the same scaffolding as the rest of ``dev_tools.tune`` — ``Case``,
``BaseEvaluator`` / ``DefaultEvaluator``, ``Model`` — rather than parallel
types, and usable either standalone (:func:`optimize_prompt`) or bound to an
agent's ``LLMCall`` parameters through :class:`Trainer`, like the other
optimizers in this package.
"""

from openjiuwen.dev_tools.tune.optimizer.prompt_search.convergence import (
    ConvergenceDetector,
    ConvergenceState,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.drift import (
    DriftJudge,
    LLMDriftJudge,
    NullDriftJudge,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.environment import (
    AgentEnvironment,
    CallableEnvironment,
    LLMEnvironment,
    PromptEnvironment,
    WorkflowEnvironment,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.history import (
    HistoryCompressor,
    HistoryEntry,
    OptimizationHistory,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.memory import (
    JsonlPromptMemory,
    NullPromptMemory,
    PromptMemory,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    Evaluation,
    Execution,
    IterationRecord,
    OptimizationResult,
    PromptCandidate,
    PromptRecord,
    PromptTaskCase,
    PromptTaskSpec,
    RewardBreakdown,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.optimizer import (
    DEFAULT_REWARD_WEIGHTS,
    PromptSearchOptimizer,
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
    CustomReward,
    LatencyReward,
    RewardComponent,
    RewardModel,
    StructuredValidationReward,
    TokenUsageReward,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.run_log import (
    NullRunLogger,
    OptimizerRunLogger,
    read_run_log,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.service import optimize_prompt

__all__ = [
    "PromptSearchOptimizer",
    "DEFAULT_REWARD_WEIGHTS",
    "optimize_prompt",
    "PromptTaskCase",
    "PromptTaskSpec",
    "PromptCandidate",
    "Execution",
    "Evaluation",
    "RewardBreakdown",
    "IterationRecord",
    "OptimizationResult",
    "PromptRecord",
    "PromptPolicy",
    "LLMPromptPolicy",
    "PolicyRequest",
    "PromptEnvironment",
    "LLMEnvironment",
    "CallableEnvironment",
    "AgentEnvironment",
    "WorkflowEnvironment",
    "DriftJudge",
    "NullDriftJudge",
    "LLMDriftJudge",
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
    "PromptMemory",
    "NullPromptMemory",
    "JsonlPromptMemory",
    "HistoryEntry",
    "OptimizationHistory",
    "HistoryCompressor",
    "ConvergenceDetector",
    "ConvergenceState",
    "OptimizerRunLogger",
    "NullRunLogger",
    "read_run_log",
]
