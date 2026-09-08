# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reference-based evaluator agent restored from the earlier behavior-scoring workflow."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger.base import EvaluationJudger, JudgeResult
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_evidence import prepare_judge_workspace, write_judge_json
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import run_judge_agent
from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import (
    finite_number,
    parse_judge_output,
    score_judge_output,
    scoring_contract,
)
from openjiuwen.rsi.harness_rsi.model_call import run_model_call_with_retries

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult


class LlmAsJudgeJudger(EvaluationJudger):
    """Explicit opt-in model grading; official and exact-match judgers stay unchanged."""

    method = "llm_as_judge"

    def __init__(self, config: EvaluatorConfig) -> None:
        self._config = config
        if not (config.judge_model_config_ref or config.model_config_ref):
            raise ValueError("llm_as_judge requires judge_model_config_ref or model_config_ref")
        finite_number(config.judge_success_score, minimum=0, maximum=1, name="judge_success_score")
        for name in ("judge_agent_max_iterations", "judge_agent_max_tokens", "judge_timeout_sec"):
            value = getattr(config, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(config.judge_max_retries, bool) or not isinstance(config.judge_max_retries, int):
            raise ValueError("judge_max_retries must be an integer")
        if not 0 <= config.judge_max_retries <= 5:
            raise ValueError("judge_max_retries must be between 0 and 5")

    def validate_case(self, case: dict[str, Any]) -> None:
        try:
            scoring_contract(case)
        except (ValueError, TypeError) as exc:
            raise EvaluationInfrastructureError(str(exc)) from exc

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        self.validate_case(case)
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        if not output_dir:
            raise EvaluationInfrastructureError("llm_as_judge requires a case output directory")
        case_dir = Path(output_dir).resolve()
        judge_dir = case_dir / "judge" / f"evaluation_{uuid.uuid4().hex[:12]}"
        workspace = judge_dir / "evidence"
        behaviors, forbidden = scoring_contract(case)
        try:
            await asyncio.to_thread(
                prepare_judge_workspace,
                case=case,
                response=execution_result.response,
                case_dir=case_dir,
                workspace=workspace,
                behaviors=behaviors,
                forbidden=forbidden,
            )
            async with asyncio.timeout(self._config.judge_timeout_sec):
                return await self._evaluate(workspace, judge_dir, behaviors, forbidden)
        except EvaluationInfrastructureError as exc:
            write_judge_json(judge_dir / "error.json", {"error_type": type(exc).__name__, "message": str(exc)})
            raise
        except Exception as exc:
            write_judge_json(judge_dir / "error.json", {"error_type": type(exc).__name__, "message": str(exc)})
            raise EvaluationInfrastructureError(f"LLM evaluator failed; inspect {judge_dir / 'error.json'}") from exc

    async def _evaluate(
        self,
        workspace: Path,
        judge_dir: Path,
        behaviors: list[dict[str, Any]],
        forbidden: list[dict[str, Any]],
    ) -> JudgeResult:
        prompt = "Read request.json and the relevant evidence files, then return the complete evaluation JSON."
        # One structural retry, on the same evidence and contract, never selecting a better score.
        for attempt in range(2):

            async def invoke() -> str:
                return await run_judge_agent(self._config, workspace, prompt, judge_dir / "tool_events.jsonl")

            raw = await run_model_call_with_retries(
                invoke,
                operation_name="llm evaluator",
                max_retries=self._config.judge_max_retries,
            )
            write_judge_json(judge_dir / f"response_{attempt + 1}.json", {"raw_output": raw})
            try:
                parsed = parse_judge_output(raw)
                if parsed.get("status") == "unavailable":
                    raise EvaluationInfrastructureError(f"LLM evaluation unavailable: {parsed.get('reason', '')}")
                if parsed.get("status", "completed") != "completed":
                    raise ValueError("invalid judge status")
                score, normalized, requirements = score_judge_output(parsed, behaviors, forbidden)
            except (ValueError, TypeError) as exc:
                if attempt:
                    raise EvaluationInfrastructureError(f"Unusable LLM evaluation; inspect {judge_dir}") from exc
                prompt += f"\nThe prior output was invalid: {exc}. Return all required fields and IDs exactly once."
                continue
            write_judge_json(judge_dir / "assessment.json", normalized)
            return JudgeResult(
                method=self.method,
                score=score,
                passed=score >= self._config.judge_success_score,
                reason=normalized["overall_reason"],
                metadata={
                    "parsed": normalized,
                    "dimensions": normalized["dimensions"],
                    "requirement_results": requirements,
                    "judge_dir": str(judge_dir),
                    "pass_threshold": self._config.judge_success_score,
                    "attempt": attempt + 1,
                },
            )
        raise EvaluationInfrastructureError("LLM evaluation did not produce a result")
