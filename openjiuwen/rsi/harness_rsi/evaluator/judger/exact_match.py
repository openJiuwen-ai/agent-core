# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exact-match evaluation judger."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.harness_rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
    _reference_answer,
)

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
        CaseExecutionResult,
    )


class ExactMatchJudger(EvaluationJudger):
    """Score by strict equality with the case reference answer."""

    method = "exact_match"

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        """Score one response by strict equality."""
        _ = output_dir
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        expected = _reference_answer(case)
        if expected is None:
            return JudgeResult(
                method=self.method,
                score=0.0,
                passed=False,
                reason="reference.answer is required for exact_match",
            )
        passed = execution_result.response == expected
        return JudgeResult(
            method=self.method,
            score=1.0 if passed else 0.0,
            passed=passed,
            reason="" if passed else "response does not exactly match reference answer",
        )


__all__ = [
    "ExactMatchJudger",
]
