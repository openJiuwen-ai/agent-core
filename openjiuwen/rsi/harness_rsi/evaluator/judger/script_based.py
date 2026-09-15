# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Benchmark-neutral script-result judger.

Benchmark packages should provide their own ``EvaluationJudger`` implementation
instead of adding benchmark runtimes to RSI.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
    _comparable_response,
    _reference_answer,
)
from openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime import (
    SWEbenchInfrastructureError,
    collect_model_patch,
    run_official_swebench_evaluation,
)

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult


class ScriptBasedJudger(EvaluationJudger):
    """Score a backend result or compare it with an explicit reference.

    Container setup, verifier invocation, and infrastructure classification are
    responsibilities of a benchmark-owned backend/judger pair.
    """

    method = "script_based"

    def validate_case(self, case: dict[str, Any]) -> None:
        reference = case.get("reference") or {}
        if reference.get("rubric"):
            raise EvaluationInfrastructureError(
                "script_based does not evaluate reference.rubric; configure a rubric-capable judger"
            )
        if isinstance(case.get("swebench"), dict):
            path = Path(str(case["swebench"].get("official_dataset_path") or ""))
            if not path.is_file():
                raise EvaluationInfrastructureError("SWE-bench official_dataset_path is missing")
            return
        if reference.get("files"):
            raise EvaluationInfrastructureError("reference.files has no supported verifier adapter")
        if _reference_answer(case) is None:
            raise EvaluationInfrastructureError(
                "script_based cannot score this case: no backend JudgeResult or reference answer. "
                "Successful execution is not evidence of correctness."
            )

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        if execution_result.judge_result is not None:
            return execution_result.judge_result
        if isinstance(case.get("swebench"), dict):
            if execution_result.execution_status != "passed":
                return self._failure_result(execution_result.error)
            return _judge_swebench(case=case, execution_result=execution_result, output_dir=output_dir)

        self.validate_case(case)
        expected = _reference_answer(case)
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        passed = _comparable_response(execution_result.response, expected) == expected
        return JudgeResult(
            method=self.method,
            score=1.0 if passed else 0.0,
            passed=passed,
            reason="" if passed else "response does not match reference",
            metadata={"rule_engine_status": "reference_comparison"},
        )


__all__ = ["ScriptBasedJudger"]


def _judge_swebench(
    *,
    case: dict[str, Any],
    execution_result: CaseExecutionResult,
    output_dir: str,
) -> JudgeResult:
    workspace_dir = Path(execution_result.workspace_dir).expanduser().resolve()
    try:
        model_patch = _load_swebench_model_patch(
            execution_result=execution_result,
            workspace_dir=workspace_dir,
        )
        patch_path = Path(output_dir).expanduser().resolve() / "verifier" / "model.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(model_patch, encoding="utf-8")
        if not model_patch.strip():
            return JudgeResult(
                method="swebench_official",
                score=0.0,
                passed=False,
                reason="official SWE-bench evaluation received an empty model patch",
                metadata={
                    "model_patch_path": str(patch_path),
                    "model_patch_chars": 0,
                    "empty_patch": True,
                },
            )
        result = run_official_swebench_evaluation(
            case=case,
            model_patch=model_patch,
            output_dir=Path(output_dir),
        )
        metadata = dict(result)
        metadata.pop("passed", None)
        metadata.pop("score", None)
        metadata.pop("reason", None)
        metadata["model_patch_path"] = str(patch_path)
        metadata["model_patch_chars"] = len(model_patch)
        return JudgeResult(
            method="swebench_official",
            score=float(result["score"]),
            passed=bool(result["passed"]),
            reason=str(result["reason"]),
            metadata=metadata,
        )
    except SWEbenchInfrastructureError:
        raise
    except Exception as exc:
        return JudgeResult(
            method="swebench_official",
            score=0.0,
            passed=False,
            reason=str(exc),
            metadata={"model_patch_chars": 0},
        )


def _load_swebench_model_patch(
    *,
    execution_result: CaseExecutionResult,
    workspace_dir: Path,
) -> str:
    """Load the patch captured in Linux before falling back to the host checkout."""
    captured_path = str((execution_result.metadata or {}).get("swebench_model_patch_path", "")).strip()
    if not captured_path:
        return collect_model_patch(workspace_dir)
    try:
        return Path(captured_path).expanduser().resolve().read_text(encoding="utf-8")
    except OSError as exc:
        raise SWEbenchInfrastructureError(f"failed to read captured SWE-bench patch: {captured_path}: {exc}") from exc
