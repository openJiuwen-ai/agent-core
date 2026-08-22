# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# ruff: noqa: I001
"""
Self-evolving training and evaluation framework.

Includes:
- Trainer, Progress, Callbacks: Training orchestration
- BaseEvaluator, DefaultEvaluator, MetricEvaluator: Evaluation interfaces
- BaseOptimizer, TextualParameter, InstructionOptimizer: Optimization
- Case, EvaluatedCase, CaseLoader: Dataset handling
- Trajectory: Canonical execution trace model
- SingleDimUpdater, MultiDimUpdater: Update generation
- Checkpointing: State persistence
- Signal: Evolution signal detection and conversion
"""

# constants
from openjiuwen.agent_evolving.constant import TuneConstant

# checkpointing
from openjiuwen.agent_evolving.checkpointing import (
    EvolveCheckpoint,
    FileCheckpointStore,
    DefaultCheckpointManager,
    CheckpointManager,
)

# dataset
from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase, CaseLoader

# dataset
from openjiuwen.agent_evolving.evaluator import (
    BaseEvaluator,
    DefaultEvaluator,
    MetricEvaluator,
    Metric,
    ExactMatchMetric,
    LLMAsJudgeMetric,
)

# optimizer
from openjiuwen.agent_evolving.optimizer import (
    BaseOptimizer,
    TextualParameter,
    InstructionOptimizer,
    MetisContextEvolveOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer

# trainer
from openjiuwen.agent_evolving.trainer import Trainer, Progress, Callbacks

# trajectory
from openjiuwen.agent_evolving.trajectory import Trajectory

# updater
from openjiuwen.agent_evolving.updater import Updater, SingleDimUpdater, MultiDimUpdater

# agent_rl
from openjiuwen.agent_evolving.agent_rl import (  # pylint: disable=no-name-in-module
    RLConfig,
    OfflineRLOptimizer,
    OnlineRLOptimizer,
    RewardRegistry,
    RLTask,
    Rollout,
    RolloutMessage,
    RolloutWithReward,
)

# signal
from openjiuwen.agent_evolving.signal import (
    ConversationSignalDetector,
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
    EvolutionTarget,
    REVIEW_FEEDBACK_SIGNAL,
    REVIEW_FEEDBACK_SOURCE,
    ReviewFeedbackAction,
    ReviewFeedbackAttribution,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContext,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
    make_signal_fingerprint,
    from_evaluated_case,
    from_evaluated_cases,
)

__all__ = [
    "TuneConstant",
    "EvolveCheckpoint",
    "FileCheckpointStore",
    "DefaultCheckpointManager",
    "CheckpointManager",
    "Case",
    "EvaluatedCase",
    "CaseLoader",
    "BaseEvaluator",
    "DefaultEvaluator",
    "MetricEvaluator",
    "Metric",
    "ExactMatchMetric",
    "LLMAsJudgeMetric",
    "BaseOptimizer",
    "TextualParameter",
    "InstructionOptimizer",
    "MetisContextEvolveOptimizer",
    "SkillExperienceOptimizer",
    "Trainer",
    "Progress",
    "Callbacks",
    "Trajectory",
    "Updater",
    "SingleDimUpdater",
    "MultiDimUpdater",
    "RLConfig",
    "OfflineRLOptimizer",
    "OnlineRLOptimizer",
    "RewardRegistry",
    "RLTask",
    "Rollout",
    "RolloutMessage",
    "RolloutWithReward",
    "ConversationSignalDetector",
    "SignalDetector",
    "EvolutionSignal",
    "EvolutionCategory",
    "EvolutionTarget",
    "REVIEW_FEEDBACK_SIGNAL",
    "REVIEW_FEEDBACK_SOURCE",
    "ReviewFeedbackAction",
    "ReviewFeedbackAttribution",
    "ReviewFeedbackAttributor",
    "ReviewFeedbackClassification",
    "ReviewFeedbackContext",
    "ReviewFeedbackContextBuilder",
    "attribution_to_evolution_signal",
    "make_signal_fingerprint",
    "from_evaluated_case",
    "from_evaluated_cases",
]
