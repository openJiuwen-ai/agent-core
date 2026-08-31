# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Method-aware deterministic signal extraction for the analyzer.

Each extractor performs zero-LLM pre-extraction that primes the DeepAgent
diagnosis prompt.  Fingerprinting normalises transient tokens (paths, hex,
line numbers) so that structurally identical errors group together.
"""

from __future__ import annotations

import re

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    DeterministicSignals,
    EvaluationSummaryInput,
)

# ---------------------------------------------------------------------------
# Error fingerprinting (业界 B3 – Drain-class log clustering)
# ---------------------------------------------------------------------------

_PATH_PATTERN = re.compile(r"[A-Za-z]:[/\\][^\s,;'\"\]]+|/[/\w.\-]+")
_HEX_PATTERN = re.compile(r"\b[0-9a-fA-F]{6,}\b")
_LINE_PATTERN = re.compile(r":\d+")
_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _fingerprint_error(error: str) -> str:
    """Normalise volatile tokens in an error string to produce a stable cluster key."""
    text = _TIMESTAMP_PATTERN.sub("<ts>", error)
    text = _UUID_PATTERN.sub("<uuid>", text)
    text = _PATH_PATTERN.sub("<path>", text)
    text = _HEX_PATTERN.sub("<hex>", text)
    text = _LINE_PATTERN.sub(":<N>", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# GenericSignalExtractor (default / exact_match)
# ---------------------------------------------------------------------------


class GenericSignalExtractor:
    """Deterministic extractor for default and exact_match judger methods."""

    name: str = "generic"

    @staticmethod
    def extract(
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        """Extract exec/judge failures and error clusters from case outputs.

        Args:
            summary: Aggregated evaluation summary.
            case_inputs: Per-case structured inputs.

        Returns:
            DeterministicSignals with exec_failures, judge_failures,
            error_clusters, expected_mismatch_cases, and missing_reference_cases.
        """
        method = summary.evaluation_method

        exec_failures: list[str] = []
        judge_failures: list[str] = []
        expected_mismatch_cases: list[str] = []
        missing_reference_cases: list[str] = []
        error_map: dict[str, list[str]] = {}

        for case in case_inputs:
            if case.status == "failed":
                exec_failures.append(case.case_id)
            elif not case.evaluation_passed:
                judge_failures.append(case.case_id)

            if case.error:
                fp = _fingerprint_error(case.error)
                error_map.setdefault(fp, []).append(case.case_id)

            # Only check expected/response for cases that executed successfully;
            # exec-failure responses are artefacts of the crash, not real answers.
            if case.status != "failed":
                if case.expected is None:
                    missing_reference_cases.append(case.case_id)
                elif case.expected != case.response:
                    expected_mismatch_cases.append(case.case_id)

        error_clusters = [{"fingerprint": fp, "cases": cases} for fp, cases in error_map.items()]

        return DeterministicSignals(
            method=method,
            exec_failures=exec_failures,
            judge_failures=judge_failures,
            error_clusters=error_clusters,
            method_specific={
                "expected_mismatch_cases": expected_mismatch_cases,
                "missing_reference_cases": missing_reference_cases,
            },
        )


# ---------------------------------------------------------------------------
# PytestSignalExtractor (script_based)
# ---------------------------------------------------------------------------


class PytestSignalExtractor:
    """Signal extractor for script_based (pytest) judger method.

    Reads ``evaluation_metadata.evidence`` clusters from each case.  Falls
    back to GenericSignalExtractor and marks ``pytest_evidence_missing`` when
    evidence is absent (guards against feat_006 dependency being incomplete).
    """

    name: str = "pytest"

    @staticmethod
    def extract(
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        """Extract pytest failure clusters from case evaluation metadata.

        Args:
            summary: Aggregated evaluation summary.
            case_inputs: Per-case structured inputs.

        Returns:
            DeterministicSignals with pytest-specific cluster data, or a
            generic fallback marked with ``pytest_evidence_missing``.
        """
        method = summary.evaluation_method
        generic = GenericSignalExtractor().extract(summary, case_inputs)

        evidence_missing: list[str] = []
        failed_test_clusters: dict[str, list[str]] = {}
        assertion_type_clusters: dict[str, list[str]] = {}

        for case in case_inputs:
            evidence = case.evaluation_metadata.get("evidence")
            if not evidence:
                evidence_missing.append(case.case_id)
                continue

            # Group by test name and assertion type
            if isinstance(evidence, list):
                for item in evidence:
                    if isinstance(item, dict):
                        test_name = str(item.get("test_name", "unknown"))
                        failed_test_clusters.setdefault(test_name, []).append(case.case_id)
                        a_type = str(item.get("assertion_type", "unknown"))
                        assertion_type_clusters.setdefault(a_type, []).append(case.case_id)

        if evidence_missing:
            return DeterministicSignals(
                method=method,
                exec_failures=generic.exec_failures,
                judge_failures=generic.judge_failures,
                error_clusters=generic.error_clusters,
                method_specific={
                    **generic.method_specific,
                    "evidence_missing_cases": evidence_missing,
                    "fallback_reason": "pytest_evidence_missing",
                },
            )

        return DeterministicSignals(
            method=method,
            exec_failures=generic.exec_failures,
            judge_failures=generic.judge_failures,
            error_clusters=generic.error_clusters,
            method_specific={
                "failed_test_clusters": failed_test_clusters,
                "assertion_type_clusters": assertion_type_clusters,
            },
        )


# ---------------------------------------------------------------------------
# RewardSignalExtractor
# ---------------------------------------------------------------------------


class RewardSignalExtractor:
    """Signal extractor for reward-based evaluators with trajectory evidence."""

    name: str = "reward"

    @staticmethod
    def extract(
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        """Extract reward and agent-stall signals from evaluator results."""
        method = summary.evaluation_method
        generic = GenericSignalExtractor().extract(summary, case_inputs)

        zero_reward_cases: list[str] = []
        max_iteration_cases: list[str] = []
        member_harness_issue_cases: list[str] = []
        trajectory_failure_signatures_by_case: dict[str, list[str]] = {}
        trajectory_recent_events_by_case: dict[str, list[dict[str, str]]] = {}
        normalized_trace_attribution_by_case: dict[str, dict[str, object]] = {}

        for case in case_inputs:
            reward = case.evaluation_metadata.get("reward")
            if reward == 0 or reward == 0.0:
                zero_reward_cases.append(case.case_id)

            response_text = str(case.response)
            if "Max iterations reached without completion" in response_text:
                max_iteration_cases.append(case.case_id)

            if case.case_id in zero_reward_cases or case.case_id in max_iteration_cases:
                member_harness_issue_cases.append(case.case_id)

            window = case.trajectory_window_summary
            if isinstance(window, dict):
                signatures = _string_list(window.get("failure_signatures"), 20)
                if signatures:
                    trajectory_failure_signatures_by_case[case.case_id] = signatures
                    if case.case_id not in member_harness_issue_cases:
                        member_harness_issue_cases.append(case.case_id)
                recent_events = _bounded_recent_events(window.get("recent_events"))
                if recent_events:
                    trajectory_recent_events_by_case[case.case_id] = recent_events

            attribution = _normalized_trace_attribution(case)
            if attribution:
                normalized_trace_attribution_by_case[case.case_id] = attribution

        return DeterministicSignals(
            method=method,
            exec_failures=generic.exec_failures,
            judge_failures=generic.judge_failures,
            error_clusters=generic.error_clusters,
            method_specific={
                **generic.method_specific,
                "zero_reward_cases": zero_reward_cases,
                "max_iteration_cases": max_iteration_cases,
                "member_harness_issue_cases": member_harness_issue_cases,
                "trajectory_failure_signatures_by_case": trajectory_failure_signatures_by_case,
                "trajectory_recent_events_by_case": trajectory_recent_events_by_case,
                "normalized_trace_attribution_by_case": normalized_trace_attribution_by_case,
            },
        )


# ---------------------------------------------------------------------------
# AtomicChecksSignalExtractor
# ---------------------------------------------------------------------------


class AtomicChecksSignalExtractor:
    """Extract atomic verifier failures and trajectory attribution."""

    name: str = "atomic_checks"

    @staticmethod
    def extract(
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        method = summary.evaluation_method
        generic = GenericSignalExtractor().extract(summary, case_inputs)
        atomic_checks_by_case: dict[str, dict[str, list[str]]] = {}
        failed_check_details_by_case: dict[str, list[dict[str, str]]] = {}
        partial_score_cases: list[str] = []
        member_harness_issue_cases: list[str] = []
        trajectory_failure_signatures_by_case: dict[str, list[str]] = {}
        trajectory_recent_events_by_case: dict[str, list[dict[str, str]]] = {}
        normalized_trace_attribution_by_case: dict[str, dict[str, object]] = {}

        for case in case_inputs:
            raw_checks = case.evaluation_metadata.get("atomic_checks", [])
            passed_checks: list[str] = []
            failed_checks: list[str] = []
            failed_details: list[dict[str, str]] = []
            if isinstance(raw_checks, list):
                for check in raw_checks:
                    if not isinstance(check, dict):
                        continue
                    name = str(check.get("name", "") or "").strip()
                    if not name:
                        continue
                    if check.get("passed") is True:
                        passed_checks.append(name)
                    elif str(check.get("status", "")) != "skipped":
                        failed_checks.append(name)
                        failed_details.append(
                            {
                                "name": name,
                                "detail": str(check.get("detail", "") or "")[:2000],
                            }
                        )
            atomic_checks_by_case[case.case_id] = {
                "passed": passed_checks,
                "failed": failed_checks,
            }
            if failed_details:
                failed_check_details_by_case[case.case_id] = failed_details
                member_harness_issue_cases.append(case.case_id)
            if 0.0 < case.score < 1.0:
                partial_score_cases.append(case.case_id)

            window = case.trajectory_window_summary
            if isinstance(window, dict):
                signatures = _string_list(window.get("failure_signatures"), 20)
                if signatures:
                    trajectory_failure_signatures_by_case[case.case_id] = signatures
                recent_events = _bounded_recent_events(window.get("recent_events"))
                if recent_events:
                    trajectory_recent_events_by_case[case.case_id] = recent_events
            attribution = _normalized_trace_attribution(case)
            if attribution:
                normalized_trace_attribution_by_case[case.case_id] = attribution

        return DeterministicSignals(
            method=method,
            exec_failures=[case.case_id for case in case_inputs if case.error],
            judge_failures=[case.case_id for case in case_inputs if not case.error and not case.evaluation_passed],
            error_clusters=generic.error_clusters,
            method_specific={
                "atomic_checks_by_case": atomic_checks_by_case,
                "failed_check_details_by_case": failed_check_details_by_case,
                "partial_score_cases": partial_score_cases,
                "member_harness_issue_cases": member_harness_issue_cases,
                "trajectory_failure_signatures_by_case": trajectory_failure_signatures_by_case,
                "trajectory_recent_events_by_case": trajectory_recent_events_by_case,
                "normalized_trace_attribution_by_case": normalized_trace_attribution_by_case,
            },
        )


# ---------------------------------------------------------------------------
# LlmJudgeSignalExtractor (llm_as_judge)
# ---------------------------------------------------------------------------


class LlmJudgeSignalExtractor:
    """Zero-LLM extractor for llm_as_judge method.

    Reads ``evaluation_metadata.parsed.dimensions`` from each case.  Falls
    back to GenericSignalExtractor and marks ``parsed_dimensions_missing``
    when the parsed dimensions block is absent.
    """

    name: str = "llm_judge"

    @staticmethod
    def extract(
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        """Extract per-behavior score data from parsed llm_as_judge dimensions.

        Args:
            summary: Aggregated evaluation summary.
            case_inputs: Per-case structured inputs.

        Returns:
            DeterministicSignals with behavior-level signal maps, or a generic
            fallback marked with ``parsed_dimensions_missing``.
        """
        method = summary.evaluation_method
        generic = GenericSignalExtractor().extract(summary, case_inputs)

        rationale_missing: list[str] = []
        low_score_behaviors: dict[str, list[str]] = {}
        behavior_score_distribution: dict[str, dict[str, float]] = {}
        behavior_diagnostics: dict[str, dict[str, dict[str, str]]] = {}
        avg_behavior_score: dict[str, float] = {}
        behavior_pass_fail_counts: dict[str, dict[str, int]] = {}

        for case in case_inputs:
            parsed = case.evaluation_metadata.get("parsed", {})
            dimensions = parsed.get("dimensions") if isinstance(parsed, dict) else None

            if not dimensions or not isinstance(dimensions, dict):
                rationale_missing.append(case.case_id)
                continue

            cid = case.case_id
            low_score_behaviors[cid] = dimensions.get("low_score_behaviors") or []
            behavior_score_distribution[cid] = dimensions.get("per_behavior_scores") or {}
            behavior_diagnostics[cid] = _behavior_diagnostics(dimensions.get("behavior_diagnostics"))
            avg_behavior_score[cid] = float(dimensions.get("avg_behavior_score", 0.0))
            behavior_pass_fail_counts[cid] = {
                "pass_count": int(dimensions.get("pass_count", 0)),
                "fail_count": int(dimensions.get("fail_count", 0)),
            }

        if rationale_missing and len(rationale_missing) == len(case_inputs):
            return DeterministicSignals(
                method=method,
                exec_failures=generic.exec_failures,
                judge_failures=generic.judge_failures,
                error_clusters=generic.error_clusters,
                method_specific={
                    **generic.method_specific,
                    "rationale_missing_cases": rationale_missing,
                    "fallback_reason": "parsed_dimensions_missing",
                },
            )

        method_specific: dict[str, object] = {
            "low_score_behaviors": low_score_behaviors,
            "behavior_score_distribution": behavior_score_distribution,
            "behavior_diagnostics": behavior_diagnostics,
            "avg_behavior_score": avg_behavior_score,
            "behavior_pass_fail_counts": behavior_pass_fail_counts,
        }
        if rationale_missing:
            method_specific["rationale_missing_cases"] = rationale_missing

        return DeterministicSignals(
            method=method,
            exec_failures=generic.exec_failures,
            judge_failures=generic.judge_failures,
            error_clusters=generic.error_clusters,
            method_specific=method_specific,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_EXTRACTOR_MAP: dict[
    str,
    GenericSignalExtractor
    | PytestSignalExtractor
    | RewardSignalExtractor
    | AtomicChecksSignalExtractor
    | LlmJudgeSignalExtractor,
] = {
    "script_based": PytestSignalExtractor(),
    "reward_based": RewardSignalExtractor(),
    "atomic_checks": AtomicChecksSignalExtractor(),
    "llm_as_judge": LlmJudgeSignalExtractor(),
}


def register_signal_extractor(method: str, extractor: object) -> None:
    """Register a method-specific extractor supplied by an evaluator adapter."""
    normalized = str(method).strip()
    if not normalized:
        raise ValueError("signal extractor method must be non-empty")
    if not callable(getattr(extractor, "extract", None)):
        raise TypeError("signal extractor must define extract(summary, case_inputs)")
    _EXTRACTOR_MAP[normalized] = extractor  # type: ignore[assignment]


def build_signal_extractor(
    method: str,
) -> (
    GenericSignalExtractor
    | PytestSignalExtractor
    | RewardSignalExtractor
    | AtomicChecksSignalExtractor
    | LlmJudgeSignalExtractor
):
    """Dispatch a SignalExtractor by judger method name.

    Falls back to GenericSignalExtractor for unknown methods.

    Args:
        method: Judger method identifier (e.g. ``"llm_as_judge"``).

    Returns:
        The matching extractor instance.
    """
    return _EXTRACTOR_MAP.get(method, GenericSignalExtractor())


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


def _bounded_recent_events(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    events: list[dict[str, str]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        events.append(
            {
                "event_type": str(item.get("event_type", ""))[:80],
                "summary": str(item.get("summary", ""))[:1200],
            }
        )
    return events


def _normalized_trace_attribution(case: CaseAnalysisInput) -> dict[str, object]:
    """Derive a deterministic attribution seed from normalized trace evidence."""
    trace_summary = case.normalized_trace_summary
    if not isinstance(trace_summary, dict):
        return {}
    for trace in trace_summary.get("traces", []):
        if not isinstance(trace, dict):
            continue
        trace_id = str(trace.get("trace_id", ""))
        role = _normalized_trace_role(trace)
        messages = trace.get("messages")
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_index = message.get("message_index")
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                error = str(call.get("error", "") or "")
                if not error:
                    continue
                tool_name = str(call.get("name", "tool"))
                step_pointer = str(call.get("step_pointer", "") or "")
                command = str(call.get("input", "") or "")
                display_role = role or "unattributed execution"
                evidence_ref = {
                    "trace_id": trace_id,
                    "role": role,
                    "message_index": message_index,
                    "step_pointer": step_pointer,
                }
                return {
                    "root_cause": (
                        f"{display_role} encountered a failing {tool_name} step and did "
                        f"not recover before the verifier failed."
                    ),
                    "critical_mistake": (
                        f"Earliest decisive failed step: {step_pointer or 'unknown step'}; "
                        f"{tool_name} input={command[:240]!r}; error={error[:240]!r}."
                    ),
                    "general_mechanism": (
                        "Strengthen the member workflow so failed commands or verifier errors trigger "
                        "inspection, targeted correction, and re-verification before completion."
                    ),
                    "target_ref": (f"member_harness.{role}.skill" if role else "unassigned"),
                    "evidence_refs": [evidence_ref],
                    "confidence": "medium" if role else "low",
                }
    return {}


def _normalized_trace_role(trace: dict[str, object]) -> str:
    role = str(trace.get("member_role") or trace.get("member_id") or "").strip()
    if role in {"", "team", "unattributed"}:
        return ""
    return role


def _behavior_diagnostics(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    diagnostics: dict[str, dict[str, str]] = {}
    for behavior_id, raw in value.items():
        if not isinstance(raw, dict):
            continue
        diagnostics[str(behavior_id)] = {
            "reason": str(raw.get("reason", "") or ""),
            "failure_reason": str(raw.get("failure_reason", "") or ""),
            "missing_capability": str(raw.get("missing_capability", "") or ""),
            "suggested_surface_hint": str(raw.get("suggested_surface_hint", "") or ""),
            "evidence": str(raw.get("evidence", "") or ""),
        }
    return diagnostics


__all__ = [
    "GenericSignalExtractor",
    "LlmJudgeSignalExtractor",
    "PytestSignalExtractor",
    "RewardSignalExtractor",
    "AtomicChecksSignalExtractor",
    "build_signal_extractor",
    "register_signal_extractor",
]
