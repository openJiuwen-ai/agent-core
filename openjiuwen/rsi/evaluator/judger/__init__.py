# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation judger package exports and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
)
from openjiuwen.rsi.evaluator.judger.exact_match import ExactMatchJudger
from openjiuwen.rsi.evaluator.judger.llm_as_judge import LlmAsJudgeJudger
from openjiuwen.rsi.evaluator.judger.script_based import ScriptBasedJudger

if TYPE_CHECKING:
    from openjiuwen.rsi.config import EvaluatorConfig


def build_judger(config: EvaluatorConfig) -> EvaluationJudger:
    """Build a concrete judger from evaluator configuration."""
    normalized = config.evaluation_method.strip().lower().replace("-", "_")
    has_model_config = bool(config.model_config_ref.strip())
    if normalized == "exact_match":
        return ExactMatchJudger()
    if normalized in {"script_based", "rule_based"}:
        return ScriptBasedJudger()
    if normalized == "llm_as_judge":
        if has_model_config:
            return LlmAsJudgeJudger(config)
        raise ValueError("evaluation_method='llm_as_judge' requires evaluator.model_config_ref")
    if normalized in {"", "default", "pass_through"}:
        if has_model_config:
            return LlmAsJudgeJudger(config)
        return ExactMatchJudger()
    raise ValueError(f"unsupported evaluation_method: {config.evaluation_method}")


__all__ = [
    "EvaluationJudger",
    "ExactMatchJudger",
    "JudgeResult",
    "LlmAsJudgeJudger",
    "ScriptBasedJudger",
    "build_judger",
]
