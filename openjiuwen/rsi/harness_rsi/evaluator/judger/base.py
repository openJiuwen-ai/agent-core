# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared contracts for evaluator judgers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
        CaseExecutionResult,
    )


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Structured score returned by an evaluation method."""

    method: str
    score: float
    passed: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class EvaluationJudger(ABC):
    """Abstract base class for all evaluation methods."""

    method: str

    @abstractmethod
    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        """Score one case response."""

    def _failure_result(self, error: str) -> JudgeResult:
        """Return a canonical score=0.0 result for execution failures."""
        return JudgeResult(
            method=self.method,
            score=0.0,
            passed=False,
            reason=error or "case execution failed",
        )


def _reference_answer(case: dict[str, Any]) -> Any:
    """Return the case reference answer with backward-compatible aliases."""
    reference = case.get("reference")
    if isinstance(reference, dict):
        if "answer" in reference:
            return reference["answer"]
        if "expected_output" in reference:
            return reference["expected_output"]
    if "reference_answer" in case:
        return case["reference_answer"]
    if "expected_output" in case:
        return case["expected_output"]
    return None


__all__ = [
    "EvaluationJudger",
    "JudgeResult",
    "_reference_answer",
]
