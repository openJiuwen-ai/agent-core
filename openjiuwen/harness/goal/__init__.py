# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal command module for DeepAgent.

Provides session-level persistent goal management including goal lifecycle,
completion evaluation, and integration with the task loop.

Goal state is exclusively owned by ``GoalManager``; task-loop lifecycle
hooks are handled by ``TaskCompletionRail`` (in ``openjiuwen.harness.rails``).
"""
from __future__ import annotations

from openjiuwen.harness.goal.evaluation import GoalEvaluator
from openjiuwen.harness.goal.manager import GoalManager
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalOperationError,
    GoalRecord,
    GoalStatus,
    GoalStopConfig,
    GoalStopStrategy,
    TokenUsage,
)
from openjiuwen.harness.goal.store import DictGoalStore, SessionGoalStore

__all__ = [
    "DictGoalStore",
    "GoalAssessment",
    "GoalAssessmentStatus",
    "GoalEvaluator",
    "GoalManager",
    "GoalOperationError",
    "GoalRecord",
    "GoalStatus",
    "GoalStopConfig",
    "GoalStopStrategy",
    "SessionGoalStore",
    "TokenUsage",
]