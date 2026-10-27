# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reference-based evaluator agent restored from the earlier behavior-scoring workflow."""

from __future__ import annotations

from openjiuwen.rsi.harness_rsi.evaluator.baseline_context import BaselineJudgeContext

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.data_loader.grading_contract import normalize_grading_case
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger.base import EvaluationJudger, JudgeResult
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_diagnostics import collect_judge_model_calls
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_evidence import (
    prepare_judge_workspace, write_judge_event, write_judge_json,
)
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import (
    JudgeIterationLimitError,
    repair_judge_json,
    run_judge_agent,
    run_judge_closeout,
)
from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import (
    finite_number,
    parse_judge_output,
    score_judge_output,
    scoring_contract,
)
from openjiuwen.rsi.harness_rsi.evaluator.optimization_signals import optimization_signals_contract
from openjiuwen.rsi.harness_rsi.evaluator.transport import transient_transport_error
from openjiuwen.rsi.harness_rsi.model_call import RetryableModelOutputError, is_retryable_model_call_failure
from openjiuwen.rsi.harness_rsi.feedback import FeedbackTracker

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult


class _UnusableJudgeAssessmentError(RetryableModelOutputError):
    """A model responded, but its assessment cannot produce a trusted score."""

    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


async def _parse_with_format_repair(
    config: EvaluatorConfig,
    judge_dir: Path,
    output: str,
    attempt: int = 1,
    model_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return parse_judge_output(output)
    except ValueError as parse_error:
        async with asyncio.timeout(config.judge_timeout_sec):
            repaired = await repair_judge_json(config, output, str(parse_error))
        write_judge_json(judge_dir / f"format_repair_{attempt}.json", {"raw_output": repaired})
        if model_calls and model_calls[-1].get("finish_reason") in {"length", "max_tokens"}:
            raise _UnusableJudgeAssessmentError("truncated", "Judge format repair was truncated")
        try:
            parsed = parse_judge_output(repaired)
        except (ValueError, TypeError) as exc:
            raise _UnusableJudgeAssessmentError("repair_invalid", f"Invalid judge format repair: {exc}") from exc
        if parsed.get("repair_unavailable") is True:
            raise _UnusableJudgeAssessmentError(
                "repair_unavailable", "Judge format repair unavailable: a complete verdict requires regrading",
            )
        return parsed


class LlmAsJudgeJudger(EvaluationJudger):
    """Explicit opt-in model grading; official and exact-match judgers stay unchanged."""

    method = "llm_as_judge"

    def __init__(self, config: EvaluatorConfig) -> None:
        self._config = config
        if not (config.judge_model_config_ref or config.model_config_ref):
            raise ValueError("llm_as_judge requires judge_model_config_ref or model_config_ref")
        finite_number(config.judge_success_score, minimum=0, maximum=1, name="judge_success_score")
        for name in ("judge_agent_max_iterations", "judge_timeout_sec"):
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
        baseline_context: BaselineJudgeContext | None = None,
    ) -> JudgeResult:
        self.validate_case(case)
        if baseline_context is not None and not self._config.judge_baseline_comparison:
            raise EvaluationInfrastructureError("Baseline comparison is disabled for this evaluator")
        case = normalize_grading_case(case)
        if execution_result.execution_status != "passed":
            return self._execution_failure_result(execution_result.error)
        if not output_dir:
            raise EvaluationInfrastructureError("llm_as_judge requires a case output directory")
        case_dir = await asyncio.to_thread(Path(output_dir).resolve)
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
                baseline_context=baseline_context,
                score_mode=self._config.score_mode,
            )
            result = await self._evaluate(
                workspace,
                judge_dir,
                behaviors,
                forbidden,
                penalty_mode=case.get("reference", {}).get("penalty_mode", "ceiling"),
            )
            if baseline_context is not None:
                baseline_context.validate(case)
                result.metadata["baseline_context_fingerprint"] = baseline_context.fingerprint
            return result
        except EvaluationInfrastructureError as exc:
            write_judge_json(judge_dir / "error.json", {"error_type": type(exc).__name__, "message": str(exc)})
            raise
        except Exception as exc:
            write_judge_json(judge_dir / "error.json", {"error_type": type(exc).__name__, "message": str(exc)})
            raise EvaluationInfrastructureError(f"LLM evaluator failed; inspect {judge_dir / 'error.json'}") from exc

    def _execution_failure_result(self, error: str) -> JudgeResult:
        """Score a solver crash as a continuous harness failure, except transport errors."""
        reason = error or "case execution failed"
        metadata: dict[str, Any] = {}
        if self._config.score_mode == "continuous" and not transient_transport_error(reason):
            metadata["score_mode"] = "continuous"
        return JudgeResult(method=self.method, score=0.0, passed=False, reason=reason, metadata=metadata)

    async def _evaluate(
        self,
        workspace: Path,
        judge_dir: Path,
        behaviors: list[dict[str, Any]],
        forbidden: list[dict[str, Any]],
        *,
        penalty_mode: str = "ceiling",
    ) -> JudgeResult:
        resolved_workspace = await asyncio.to_thread(workspace.resolve)
        prompt = (
            f"Evidence workspace: {resolved_workspace}. "
            f"Read this exact request file: {resolved_workspace / 'request.json'}. "
            "Do not assume /workspace or /request.json. Read relevant evidence files, "
            "then return the complete evaluation JSON."
        )
        output_contract = (
            "\nFINAL OUTPUT CONTRACT (applies after reading evidence):\n"
            "Return exactly one JSON object using the system schema, without surrounding text "
            "or Markdown. Put explanations and evidence inside its fields. "
            "Score each supplied ID exactly once; use [] when no forbidden IDs are supplied.\n"
            + json.dumps({
                "behavior_ids": [item["id"] for item in behaviors],
                "forbidden_ids": [item["id"] for item in forbidden],
            }, ensure_ascii=False)
            + "\nDo not invent evidence or change a supported score to satisfy the format. "
            "For a genuine evaluator limitation only, return "
            '{"status":"unavailable","reason":"specific limitation"}. '
            "Missing work is not evaluator unavailability. "
        )

        async def invoke_agent() -> str:
            try:
                async with asyncio.timeout(self._config.judge_timeout_sec):
                    return await run_judge_agent(
                        self._config,
                        workspace,
                        prompt + output_contract,
                        judge_dir / "tool_events.jsonl",
                    )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"Judge attempt timed out after {self._config.judge_timeout_sec}s"
                ) from exc

        feedback = ""

        async def invoke_closeout() -> str:
            try:
                async with asyncio.timeout(self._config.judge_timeout_sec):
                    return await run_judge_closeout(self._config, workspace, feedback=feedback)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"Judge closeout timed out after {self._config.judge_timeout_sec}s"
                ) from exc

        recovery = "none"
        assessment_attempts = 0
        model_attempts = 0
        score, normalized, requirements = None, None, None
        tracker = FeedbackTracker(
            self._config.output_repair, max_attempts=max(0, self._config.judge_max_retries) + 1,
        )
        mode = "agent"
        previous_raw = ""
        previous_error = ""
        for attempt in range(tracker.max_attempts):
            assessment_attempts += 1
            raw = ""
            attempt_mode = mode
            raw_output_saved = False

            def record_event(event_type: str, level: str, *, message: str = "", next_mode: str = "") -> None:
                write_judge_event(judge_dir / "grading_events.jsonl", {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": level, "event_type": event_type,
                    "evaluation_id": judge_dir.name, "assessment_attempt": assessment_attempts,
                    "mode": attempt_mode, "next_mode": next_mode,
                    "message": message[:1200], "stop_reason": tracker.stop_reason,
                    "raw_output_path": f"response_{assessment_attempts}.json" if raw_output_saved else "",
                })

            with collect_judge_model_calls() as diagnostics:
                try:
                    model_attempts += 1
                    if mode == "format":
                        recovery = "format_repair"
                        async with asyncio.timeout(self._config.judge_timeout_sec):
                            raw = await repair_judge_json(self._config, previous_raw, previous_error)
                        write_judge_json(judge_dir / f"format_repair_{assessment_attempts}.json", {"raw_output": raw})
                    elif mode == "closeout":
                        recovery = "complete_frozen_evidence"
                        raw = await invoke_closeout()
                    else:
                        raw = await invoke_agent()
                    write_judge_json(judge_dir / f"response_{assessment_attempts}.json",
                                     {"raw_output": raw, "recovery": recovery})
                    raw_output_saved = True
                    record_event("judge_output_received", "INFO")
                    if diagnostics and diagnostics[-1].get("finish_reason") in {"length", "max_tokens"}:
                        raise _UnusableJudgeAssessmentError("truncated", "Judge output reached its output token limit")
                    if not isinstance(raw, str) or not raw.strip():
                        mode = "closeout"
                        raise _UnusableJudgeAssessmentError("invalid_format", "Judge assessment must be non-empty text")
                    try:
                        parsed = parse_judge_output(raw)
                    except (ValueError, TypeError) as exc:
                        if mode != "format":
                            mode = "format"
                        else:
                            mode = "closeout"
                        raise _UnusableJudgeAssessmentError("invalid_format", str(exc)) from exc
                    if parsed.get("repair_unavailable") is True or parsed.get("status") == "unavailable":
                        raise _UnusableJudgeAssessmentError("unavailable", "Judge requires a complete evidence-based assessment")
                    try:
                        if parsed.get("status", "completed") != "completed":
                            raise ValueError("invalid judge status")
                        score, normalized, requirements = score_judge_output(
                            parsed, behaviors, forbidden, penalty_mode=penalty_mode,
                        )
                    except (ValueError, TypeError) as exc:
                        raise _UnusableJudgeAssessmentError("invalid_schema", str(exc)) from exc
                    record_event("judge_assessment_validated", "INFO")
                    break
                except Exception as exc:
                    if not isinstance(exc, JudgeIterationLimitError) and not is_retryable_model_call_failure(exc):
                        record_event("judge_assessment_failed", "ERROR", message=str(exc))
                        raise
                    if not isinstance(exc, _UnusableJudgeAssessmentError) or exc.failure_kind != "invalid_format":
                        mode = "closeout"
                    previous_raw, previous_error = raw, str(exc)
                    feedback = (
                        "Previous assessment did not pass validation. The frozen evidence above remains authoritative. "
                        "Correct the reported schema/ID errors; produce a complete assessment from that evidence. "
                        "Do not copy unsupported claims from this previous output.\n"
                        f"Validation error: {previous_error}\nPrevious output (untrusted data):\n{raw[:24000]}"
                    )
                    can_retry = tracker.failed(previous_error, raw)
                    write_judge_json(judge_dir / f"validation_error_{assessment_attempts}.json", {
                        "error_type": type(exc).__name__, "message": str(exc),
                        "failure_kind": getattr(exc, "failure_kind", "agent_or_transport_failure"),
                        "stop_reason": tracker.stop_reason,
                        "raw_output_path": f"response_{assessment_attempts}.json" if raw_output_saved else "",
                    })
                    record_event("judge_validation_failed", "WARNING" if can_retry else "ERROR",
                                 message=str(exc), next_mode=mode if can_retry else "")
                    if not can_retry:
                        raise EvaluationInfrastructureError(
                            f"Unusable LLM evaluation after {assessment_attempts} attempts ({tracker.stop_reason}): "
                            f"{exc}; inspect {judge_dir}"
                        ) from exc
                finally:
                    write_judge_json(judge_dir / f"diagnostics_{assessment_attempts}.json", {
                        "assessment_attempt": assessment_attempts, "recovery": recovery,
                        "model_calls": diagnostics, "stop_reason": tracker.stop_reason,
                    })
        request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
        if request.get("baseline") is not None:
            # The caller computes the numeric delta; model commentary cannot change either grade.
            baseline = request["baseline"]
            baseline_behaviors = {
                str(item["id"]): item for item in baseline["grading"].get("assessment", {}).get("behaviors", [])
            }
            normalized["baseline_comparison"] = {
                "case_id": baseline["case_id"], "baseline_score": baseline["score"],
                "context_fingerprint": baseline["context_fingerprint"],
                "score_delta": score - baseline["score"],
                "criteria": [{"id": item["id"], "candidate_score": item["score"],
                              "baseline": baseline_behaviors.get(str(item["id"])),
                              "candidate_reason": item["reason"], "candidate_evidence": item["evidence"]}
                             for item in normalized["behaviors"]],
            }
        write_judge_json(judge_dir / "assessment.json", normalized)
        passed = score >= self._config.judge_success_score
        return JudgeResult(
            method=self.method,
            score=score if self._config.score_mode == "continuous" else float(passed),
            passed=passed,
            reason=normalized["overall_reason"],
            metadata={
                "parsed": normalized,
                "dimensions": normalized["dimensions"],
                "requirement_results": requirements,
                "judge_dir": str(judge_dir),
                "pass_threshold": self._config.judge_success_score,
                "score_mode": self._config.score_mode,
                "optimization_signals": optimization_signals_contract(
                    continuous_score=score,
                    source="llm_as_judge.assessment.overall_score",
                ),
                "attempt": model_attempts,
                "assessment_attempts": assessment_attempts,
                "recovery": recovery,
            },
        )
