# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation judger package exports and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.rsi.harness_rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
)
from openjiuwen.rsi.harness_rsi.evaluator.judger.exact_match import ExactMatchJudger
from openjiuwen.rsi.harness_rsi.evaluator.judger.script_based import ScriptBasedJudger

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig


def build_judger(config: EvaluatorConfig) -> EvaluationJudger:
    """Build a concrete judger from evaluator configuration."""
    normalized = config.evaluation_method.strip().lower().replace("-", "_")
    if normalized == "exact_match":
        return ExactMatchJudger()
    if normalized in {"script_based", "rule_based"}:
        return ScriptBasedJudger()
    if normalized in {"", "default", "pass_through"}:
        return ScriptBasedJudger()
    raise ValueError(f"unsupported evaluation_method: {config.evaluation_method}")


__all__ = [
    "EvaluationJudger",
    "ExactMatchJudger",
    "JudgeResult",
    "ScriptBasedJudger",
    "build_judger",
]
