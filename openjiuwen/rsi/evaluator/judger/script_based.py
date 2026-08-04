# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Benchmark-neutral script-result judger.

Benchmark packages should provide their own ``EvaluationJudger`` implementation
instead of adding benchmark runtimes to RSI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
    _reference_answer,
)

if TYPE_CHECKING:
    from openjiuwen.rsi.evaluator.case_backend import CaseExecutionResult


class ScriptBasedJudger(EvaluationJudger):
    """Score a backend result or compare it with an explicit reference.

    Container setup, verifier invocation, and infrastructure classification are
    responsibilities of a benchmark-owned backend/judger pair.
    """

    method = "script_based"

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        del output_dir
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        if execution_result.judge_result is not None:
            return execution_result.judge_result

        expected = _reference_answer(case)
        if expected is None:
            return JudgeResult(
                method=self.method,
                score=1.0,
                passed=True,
                metadata={"rule_engine_status": "backend_completed"},
            )
        passed = execution_result.response == expected
        return JudgeResult(
            method=self.method,
            score=1.0 if passed else 0.0,
            passed=passed,
            reason="" if passed else "response does not match reference",
            metadata={"rule_engine_status": "reference_comparison"},
        )


__all__ = ["ScriptBasedJudger"]
