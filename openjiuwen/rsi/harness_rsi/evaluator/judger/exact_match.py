# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exact-match evaluation judger."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
    _comparable_response,
    _reference_answer,
)

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
        CaseExecutionResult,
    )


class ExactMatchJudger(EvaluationJudger):
    """Score by strict equality with the case reference answer."""

    method = "exact_match"

    def validate_case(self, case: dict[str, Any]) -> None:
        reference = case.get("reference") or {}
        if reference.get("rubric") or reference.get("files") or case.get("swebench"):
            raise EvaluationInfrastructureError(
                "exact_match only supports reference answers, not rubric or file grading"
            )
        if _reference_answer(case) is None:
            raise EvaluationInfrastructureError("exact_match cannot score this case: a reference answer is required")

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        """Score one response by strict equality."""
        _ = output_dir
        self.validate_case(case)
        expected = _reference_answer(case)
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        passed = _comparable_response(execution_result.response, expected) == expected
        return JudgeResult(
            method=self.method,
            score=1.0 if passed else 0.0,
            passed=passed,
            reason="" if passed else "response does not exactly match reference answer",
        )


__all__ = [
    "ExactMatchJudger",
]
