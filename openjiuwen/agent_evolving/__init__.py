# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Self-evolving training and evaluation framework.

Includes:
- Trainer, Progress, Callbacks: Training orchestration
- BaseEvaluator, DefaultEvaluator, MetricEvaluator: Evaluation interfaces
- BaseOptimizer, TextualParameter, InstructionOptimizer: Optimization
- Case, EvaluatedCase, CaseLoader: Dataset handling
- Trajectory, TrajectoryStep: Execution trace types
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
)
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer

# trainer
from openjiuwen.agent_evolving.trainer import Trainer, Progress, Callbacks

# trajectory
from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    TrajectoryStep,
    UpdateKey,
    Updates,
    TracerTrajectoryExtractor,
)

# updater
from openjiuwen.agent_evolving.updater import Updater, SingleDimUpdater, MultiDimUpdater

# signal
from openjiuwen.agent_evolving.signal import (
    ConversationSignalDetector,
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
    EvolutionTarget,
    make_signal_fingerprint,
    from_evaluated_case,
    from_evaluated_cases,
)

_CONSTANTS = [
    "TuneConstant",
]

_CHECKPOINTING = [
    "EvolveCheckpoint",
    "FileCheckpointStore",
    "DefaultCheckpointManager",
    "CheckpointManager",
]

_DATASET = [
    "Case",
    "EvaluatedCase",
    "CaseLoader",
]

_EVALUATOR = [
    "BaseEvaluator",
    "DefaultEvaluator",
    "MetricEvaluator",
    "Metric",
    "ExactMatchMetric",
    "LLMAsJudgeMetric",
]

_OPTIMIZER = [
    "BaseOptimizer",
    "TextualParameter",
    "InstructionOptimizer",
    "SkillExperienceOptimizer",
]

_TRAINER = [
    "Trainer",
    "Progress",
    "Callbacks",
]

_TRAJECTORY = [
    "Trajectory",
    "TrajectoryStep",
    "UpdateKey",
    "Updates",
    "TracerTrajectoryExtractor",
]

_UPDATER = [
    "Updater",
    "SingleDimUpdater",
    "MultiDimUpdater",
]

_SIGNAL = [
    "ConversationSignalDetector",
    "SignalDetector",
    "EvolutionSignal",
    "EvolutionCategory",
    "EvolutionTarget",
    "make_signal_fingerprint",
    "from_evaluated_case",
    "from_evaluated_cases",
]

__all__ = (
    _CONSTANTS
    + _CHECKPOINTING
    + _DATASET
    + _EVALUATOR
    + _OPTIMIZER
    + _TRAINER
    + _TRAJECTORY
    + _UPDATER
    + _SIGNAL
)
