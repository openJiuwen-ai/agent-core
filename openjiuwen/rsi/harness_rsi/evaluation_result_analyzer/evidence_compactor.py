# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic, decision-centered compression for analyzer evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_MIN_SELECTED_CALLS_PER_TRIAL = 8
_MAX_SELECTED_CALLS_PER_TRIAL = 20
_MAX_TOOL_SEQUENCE = 80
_MUTATING_TOOL_PATTERN = re.compile(
    r"(?:^|_)(?:assign|cancel|create|delete|disable|enable|finish|modify|order|patch|save|schedule|send|submit|"
    r"update|write)(?:_|$)",
    re.IGNORECASE,
)
_ARTIFACT_PATTERN = re.compile(
    r"(?:^|[\s`'\"])([^\s`'\"]+\.(?:csv|docx|html|json|md|pdf|pptx|txt|xlsx))(?:$|[\s`'\"])", re.IGNORECASE
)
_FAILED_RESPONSE_PATTERN = re.compile(
    r"(?:success\s*=\s*false|\"success\"\s*:\s*false|exit\s+code\s*:\s*[1-9]\d*|traceback\s*\(|access\s+denied)",
    re.IGNORECASE,
)
_BASH_MUTATION_PATTERN = re.compile(
    r"(?:^|[;&|\n]\s*)(?:cp|mv|rm|mkdir|touch|install)\b|"
    r"(?:\.save\s*\(|save_workbook\s*\(|write_text\s*\(|write_bytes\s*\(|"
    r"shutil\.(?:copy|copy2|copytree|move)|libreoffice\b.*--convert-to|(?:^|\s)>\s*[^&])",
    re.IGNORECASE,
)
_BASH_CONTENT_PATTERN = re.compile(
    r"(?:pdftotext|python\w*\b.*(?:docx|openpyxl|pypdf|python-pptx)|"
    r"(?:cat|sed|head|tail|unzip)\s)",
    re.IGNORECASE | re.DOTALL,
)
_CRITICAL_EVIDENCE_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,8}")
_CRITICAL_EVIDENCE_STOPWORDS = {
    "and",
    "agent",
    "answer",
    "assessment",
    "because",
    "between",
    "but",
    "calculating",
    "conclusion",
    "correctly",
    "criterion",
    "did",
    "directly",
    "evidence",
    "explicitly",
    "failed",
    "failure",
    "first",
    "identified",
    "met",
    "must",
    "not",
    "requirement",
    "required",
    "response",
    "second",
    "specific",
    "state",
    "stated",
    "states",
    "task",
    "text",
    "than",
    "the",
    "that",
    "this",
    "timeline",
    "timelines",
    "with",
}
_COMPACTION_MARKER = (
    "[ANALYZER_EVIDENCE_COMPACTION: omitted {omitted} source chars; this marker was not observed by the task agent]"
)


def public_task_contract_snapshot(task: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the public task and tool contract while excluding scorer internals."""
    public = task.get("public_task_contract")
    public = public if isinstance(public, Mapping) else {}
    metadata = public.get("metadata", task.get("metadata"))
    metadata = metadata if isinstance(metadata, Mapping) else {}
    tool_schemas = public.get("tool_schemas")
    tool_schemas = tool_schemas if isinstance(tool_schemas, Sequence) and not isinstance(tool_schemas, str) else []
    task_metadata: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in {"canary", "category", "difficulty", "language"} and _is_scalar(value):
            task_metadata[str(key)] = value
    normalized_tool_schemas: list[dict[str, Any]] = []
    for schema in tool_schemas:
        if not isinstance(schema, Mapping):
            continue
        normalized = _normalize_tool_schema(schema)
        if normalized:
            normalized_tool_schemas.append(normalized)
    return {
        "schema_version": 1,
        "provenance": "official_suite.public_task_contract",
        "task_id": str(task.get("id") or public.get("task_id") or ""),
        "domain": str(public.get("domain") or task.get("domain") or ""),
        "prompt": str(public.get("prompt") or task.get("prompt") or ""),
        "task_metadata": task_metadata,
        "tool_schemas": normalized_tool_schemas,
    }


def load_public_task_contract(
    *,
    case_id: str,
    result_path: str,
    evaluation_metadata: Mapping[str, Any],
    task_input: str,
) -> dict[str, Any]:
    """Load a materialized public contract, with a safe fallback for older runs."""
    direct = evaluation_metadata.get("analysis_task_contract")
    if isinstance(direct, Mapping):
        return dict(direct)

    result = Path(result_path)
    evaluation_dir = result.parent.parent.parent if len(result.parents) >= 3 else Path()
    suite_path = evaluation_dir / "official" / "suite.json"
    suite = _read_mapping(suite_path)
    for split_name in ("validation", "evaluation", "tasks"):
        tasks = suite.get(split_name)
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, Mapping) and str(task.get("id") or "") == case_id:
                return public_task_contract_snapshot(task)

    return {
        "schema_version": 1,
        "provenance": "case.input",
        "task_id": case_id,
        "prompt": task_input,
        "tool_schemas": [],
    }


def build_causal_evidence_digest(  # pylint: disable=huawei-too-many-arguments
    *,
    case_id: str,
    task_input: str,
    response: str,
    evaluation_passed: bool,
    evaluation_score: float,
    evaluation_reason: str,
    evaluation_metadata: Mapping[str, Any],
    trace_data: Mapping[str, Any],
    task_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a compact evidence packet that preserves trial differences and exact actions."""
    traces = trace_data.get("traces")
    traces = traces if isinstance(traces, list) else []
    trial_scores = _aligned_list(evaluation_metadata.get("trial_scores"), len(traces))
    trial_passed = _aligned_list(evaluation_metadata.get("trial_passed"), len(traces))
    trial_exit_reasons = _aligned_list(evaluation_metadata.get("trial_exit_reasons"), len(traces))
    trial_details = _aligned_list(evaluation_metadata.get("trial_details"), len(traces))
    has_trial_outcomes = bool(traces) and bool(trial_scores) and bool(trial_passed)
    critical_evidence_terms = _failed_requirement_terms(evaluation_metadata)

    trial_records: list[dict[str, Any]] = []
    all_calls: list[dict[str, Any]] = []
    raw_message_count = 0
    for index, raw_trace in enumerate(traces):
        if not isinstance(raw_trace, Mapping):
            continue
        score = trial_scores[index] if has_trial_outcomes else (evaluation_score if len(traces) == 1 else None)
        passed = trial_passed[index] if has_trial_outcomes else (evaluation_passed if len(traces) == 1 else None)
        exit_reason = trial_exit_reasons[index] if trial_exit_reasons else ""
        trial = _build_trial_record(
            raw_trace,
            index=index,
            score=score,
            passed=passed,
            exit_reason=exit_reason,
            critical_evidence_terms=critical_evidence_terms,
        )
        trial["trial_evaluation"] = _compact_trial_evaluation(trial_details[index] if trial_details else None)
        raw_message_count += int(trial.pop("_raw_message_count"))
        all_calls.extend(trial.pop("_all_calls"))
        trial_records.append(trial)

    _deduplicate_selected_payloads(trial_records)
    tool_contracts = _tool_contract_observations(task_contract, all_calls)
    compact_task_contract = {
        key: _compact_value(value) for key, value in task_contract.items() if key not in {"prompt", "tool_schemas"}
    }
    compact_task_contract["prompt_ref"] = "authoritative_task_contract.input_excerpt"
    compact_task_contract["tool_schema_ref"] = "tool_contract_observations"
    digest = {
        "schema_version": 1,
        "compression_policy": {
            "method": "deterministic_decision_centered",
            "no_llm_summarization": True,
            "trial_boundaries_preserved": has_trial_outcomes,
            "exact_numbers_and_structured_tool_arguments_preserved": True,
            "action_selection": (
                "dynamic decision-linked budget; prioritize observed failures, state mutations, "
                "content-bearing reads, tool boundaries, and the terminal window"
            ),
            "response_only_field_policy": (
                "A response-only field is not a missing request field unless the public tool schema declares it."
            ),
            "lossless_evidence_policy": (
                "Compacted response excerpts are display views, not task-agent observations. "
                "ANALYZER_EVIDENCE_COMPACTION markers are generated after execution. "
                "Exact failed-requirement-linked spans are retained separately with raw evidence pointers."
            ),
        },
        "task_contract": compact_task_contract,
        "outcome": {
            "case_id": case_id,
            "passed": evaluation_passed,
            "score": evaluation_score,
            "reason": _compact_text(evaluation_reason, 1_000),
            "trial_count": len(trial_records),
            "judge_dimensions": _judge_dimensions(evaluation_metadata),
            "judge_evidence": _compact_judge_evidence(evaluation_metadata),
        },
        "tool_contract_observations": tool_contracts,
        "critical_evidence_terms": critical_evidence_terms,
        "trials": trial_records,
        "cross_trial_contrast": _cross_trial_contrast(trial_records),
        "fallback_final_response": (
            {"available": False, "reason": "per_trial_final_outputs_available"}
            if trial_records
            else {"available": True, **_output_summary(response)}
        ),
        "compression_stats": {
            "raw_trace_count": len(traces),
            "raw_message_count": raw_message_count,
            "raw_tool_call_count": len(all_calls),
            "selected_tool_call_count": sum(len(trial["selected_actions"]) for trial in trial_records),
        },
    }
    return digest


def _compact_trial_evaluation(value: Any) -> dict[str, Any]:
    """Preserve per-trial grader evidence without treating absence as zero."""
    if not isinstance(value, Mapping):
        return {
            "schema_version": 1,
            "availability": {
                "score_file": "not_instrumented",
                "score_reason": "not_instrumented",
                "judge_detail": "not_instrumented",
                "dimension_scores": "not_instrumented",
            },
            "score_reason": "",
            "judge_detail": None,
            "dimension_scores": {},
            "source": {},
        }

    raw_availability = value.get("availability")
    raw_availability = raw_availability if isinstance(raw_availability, Mapping) else {}
    availability = {
        str(key): str(item) for key, item in raw_availability.items() if isinstance(key, str) and isinstance(item, str)
    }
    raw_dimensions = value.get("dimension_scores")
    raw_dimensions = raw_dimensions if isinstance(raw_dimensions, Mapping) else {}
    dimensions: dict[str, dict[str, Any]] = {}
    for name, raw_dimension in raw_dimensions.items():
        if not isinstance(raw_dimension, Mapping):
            continue
        state = str(raw_dimension.get("availability") or "not_available")
        raw_score = raw_dimension.get("value")
        dimensions[str(name)] = {
            "availability": state,
            "value": raw_score if _is_number(raw_score) else None,
            "source": str(raw_dimension.get("source")) if raw_dimension.get("source") else None,
        }
    raw_judge_detail = value.get("judge_detail")
    judge_detail = _compact_value(raw_judge_detail) if isinstance(raw_judge_detail, Mapping) else None
    source = value.get("source")
    source = _compact_value(source) if isinstance(source, Mapping) else {}
    return {
        "schema_version": 1,
        "availability": availability,
        "score": value.get("score") if _is_number(value.get("score")) else None,
        "passed": value.get("passed") if isinstance(value.get("passed"), bool) else None,
        "score_reason": _compact_text(value.get("score_reason"), 1_000),
        "judge_detail": judge_detail,
        "dimension_scores": dimensions,
        "source": source,
    }


def _compact_judge_evidence(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Keep aggregate criterion feedback that is not present in trial score files."""
    normalized = metadata.get("judge_evidence")
    if isinstance(normalized, Mapping):
        criteria = normalized.get("criteria")
        return {
            "schema_version": 1,
            "availability": str(normalized.get("availability") or "not_available"),
            "grading_run_status": str(normalized.get("grading_run_status") or ""),
            "criteria": _compact_value(criteria) if isinstance(criteria, list) else [],
        }

    detail = metadata.get("judge_detail")
    if not isinstance(detail, Mapping):
        return {
            "schema_version": 1,
            "availability": "not_instrumented",
            "grading_run_status": "",
            "criteria": [],
        }
    raw_criteria = detail.get("criteria")
    if not isinstance(raw_criteria, list):
        return {
            "schema_version": 1,
            "availability": "not_available",
            "grading_run_status": str(detail.get("grading_run_status") or ""),
            "criteria": [],
        }
    criteria = [_compact_value(item) for item in raw_criteria if isinstance(item, Mapping)]
    return {
        "schema_version": 1,
        "availability": "available" if criteria else "invalid",
        "grading_run_status": str(detail.get("grading_run_status") or ""),
        "criteria": criteria,
    }


def compact_candidate_feedback(feedback: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize paired intervention evidence without losing outcome signals.

    Candidate evaluation and historical journal records use slightly different
    field names.  Normalize both into one analyzer-facing schema so a diagnosis
    always sees the pre-evaluation prediction, activation evidence, strict
    acceptance score, continuous diagnostic score, and per-requirement delta.
    """
    if not isinstance(feedback, Mapping):
        return {}
    records = feedback.get("experiments")
    if not isinstance(records, list):
        return {}
    experiments: list[dict[str, Any]] = []
    for record in records[-3:]:
        if not isinstance(record, Mapping):
            continue
        diagnoses = record.get("candidate_failure_diagnoses")
        diagnoses = diagnoses if isinstance(diagnoses, list) else []
        raw_observed = record.get("observed_outcome")
        raw_observed = raw_observed if isinstance(raw_observed, Mapping) else {}
        raw_prediction = record.get("prediction")
        raw_prediction = raw_prediction if isinstance(raw_prediction, Mapping) else {}
        strict_score = _compact_score_comparison(
            raw_observed.get("strict_score"),
            source=_first_present(raw_observed, record, keys=("source_target_score", "source_strict_score")),
            candidate=_first_present(
                raw_observed,
                record,
                keys=("candidate_target_score", "candidate_strict_score"),
            ),
            delta=_first_present(raw_observed, record, keys=("target_score_delta", "strict_score_delta")),
        )
        continuous_score = _compact_score_comparison(
            raw_observed.get("continuous_score"),
            source=_first_present(raw_observed, record, keys=("source_native_score", "source_continuous_score")),
            candidate=_first_present(
                raw_observed,
                record,
                keys=("candidate_native_score", "candidate_continuous_score"),
            ),
            delta=_first_present(raw_observed, record, keys=("native_score_delta", "continuous_score_delta")),
        )
        continuous_score.update(
            {
                "source_signal": str(
                    _first_present(raw_observed, record, keys=("source_native_signal", "source_continuous_signal"))
                    or ""
                ),
                "candidate_signal": str(
                    _first_present(
                        raw_observed,
                        record,
                        keys=("candidate_native_signal", "candidate_continuous_signal"),
                    )
                    or ""
                ),
                "role": str(_first_present(raw_observed, record, keys=("native_signal_role",)) or ""),
            }
        )
        requirement_delta = _first_mapping(
            raw_observed.get("requirement_delta"),
            raw_observed.get("verifier_delta"),
            record.get("requirement_delta"),
            record.get("verifier_delta"),
        )
        dimension_deltas = _first_mapping(
            raw_observed.get("dimension_deltas"),
            raw_observed.get("native_dimension_deltas"),
            record.get("dimension_deltas"),
            record.get("native_dimension_deltas"),
        )
        contracts = raw_prediction.get("causal_intervention_contracts")
        if not isinstance(contracts, list):
            contracts = record.get("causal_intervention_contracts")
        contracts = contracts if isinstance(contracts, list) else []
        activation = record.get("activation")
        activation = activation if isinstance(activation, Mapping) else {}
        observed_outcome = {
            "status": str(_first_present(raw_observed, record, keys=("status", "outcome")) or ""),
            "reason": str(_first_present(raw_observed, record, keys=("reason",)) or ""),
            "strict_score": strict_score,
            "continuous_score": continuous_score,
            "requirement_delta": _compact_value(requirement_delta),
            "dimension_deltas": _compact_value(dimension_deltas),
            "selected_for_promotion": _first_present(
                raw_observed,
                record,
                keys=("selected_for_promotion",),
            ),
            # Preserve v1 aliases while analyzer consumers migrate to the
            # explicit strict/continuous comparison objects above.
            "source_target_score": strict_score["source"],
            "candidate_target_score": strict_score["candidate"],
            "target_score_delta": strict_score["delta"],
            "source_native_score": continuous_score["source"],
            "candidate_native_score": continuous_score["candidate"],
            "native_score_delta": continuous_score["delta"],
            "verifier_delta": _compact_value(requirement_delta),
        }
        experiments.append(
            {
                "schema_version": 2,
                "experiment_id": str(record.get("experiment_id") or ""),
                "surface": str(record.get("surface") or ""),
                "predicted_rank": record.get("predicted_rank"),
                "predicted_score": record.get("predicted_score"),
                "prediction": {
                    "predicted_rank": _first_present(raw_prediction, record, keys=("predicted_rank",)),
                    "predicted_score": _first_present(raw_prediction, record, keys=("predicted_score",)),
                    "candidate_patch_excerpt": _compact_text(
                        _first_present(raw_prediction, record, keys=("candidate_patch_excerpt",)),
                        2_000,
                    ),
                    "causal_intervention_contracts": _compact_value(contracts),
                },
                "observed_outcome": observed_outcome,
                "activation": _compact_value(activation),
                "causal_intervention_contracts": _compact_value(contracts),
                "verifier_delta": _compact_value(requirement_delta),
                "candidate_failure_diagnoses": _compact_candidate_failure_diagnoses(diagnoses),
            }
        )
    return {"case_id": str(feedback.get("case_id") or ""), "experiments": experiments}


def _first_present(*values: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """Return the first explicitly present alias, including false and zero."""
    for value in values:
        for key in keys:
            if key in value:
                return value.get(key)
    return None


def _compact_candidate_failure_diagnoses(diagnoses: Sequence[Any]) -> list[dict[str, Any]]:
    keys = (
        "summary",
        "root_cause",
        "target_ref",
        "recommendation",
        "decision_contract",
        "hypothesis_assessment",
        "prior_experiment_assessment",
    )
    compacted: list[dict[str, Any]] = []
    for diagnosis in diagnoses[:2]:
        if not isinstance(diagnosis, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in keys:
            if key in diagnosis:
                item[key] = _compact_value(diagnosis.get(key))
        compacted.append(item)
    return compacted


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _compact_score_comparison(
    nested: Any,
    *,
    source: Any,
    candidate: Any,
    delta: Any,
) -> dict[str, Any]:
    nested = nested if isinstance(nested, Mapping) else {}
    source_value = nested.get("source") if "source" in nested else source
    candidate_value = nested.get("candidate") if "candidate" in nested else candidate
    delta_value = nested.get("delta") if "delta" in nested else delta
    if not _is_number(delta_value) and _is_number(source_value) and _is_number(candidate_value):
        delta_value = float(candidate_value) - float(source_value)
    return {
        "source": source_value if _is_number(source_value) else None,
        "candidate": candidate_value if _is_number(candidate_value) else None,
        "delta": delta_value if _is_number(delta_value) else None,
    }


def _build_trial_record(
    trace: Mapping[str, Any],
    *,
    index: int,
    score: Any,
    passed: Any,
    exit_reason: Any,
    critical_evidence_terms: Sequence[str] = (),
) -> dict[str, Any]:
    trace_id = str(trace.get("trace_id") or f"trace_{index + 1}")
    role = str(trace.get("member_role") or trace.get("role") or "")
    messages = trace.get("messages")
    messages = messages if isinstance(messages, list) else []
    calls: list[dict[str, Any]] = []
    assistant_outputs: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        message_index = message.get("message_index", "")
        step_pointer = str(message.get("step_pointer") or "")
        content = str(message.get("content") or "").strip()
        if str(message.get("role") or "") == "assistant" and content:
            assistant_outputs.append(
                {
                    "message_index": message_index,
                    "step_pointer": step_pointer,
                    "content": content,
                }
            )
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for call_index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, Mapping):
                continue
            raw_input = raw_call.get("input", "")
            raw_output = raw_call.get("output", "")
            raw_output_text = str(raw_output or "")
            raw_error = str(raw_call.get("error") or "")
            evidence_id = f"{trace_id}:message_{message_index}:call_{call_index}"
            calls.append(
                {
                    "evidence_id": evidence_id,
                    "trace_id": trace_id,
                    "role": role,
                    "message_index": message_index,
                    "step_pointer": str(raw_call.get("step_pointer") or step_pointer),
                    "tool": str(raw_call.get("name") or ""),
                    "request": _parse_and_compact(raw_input),
                    "response": _parse_and_compact(raw_output),
                    "response_evidence": _response_evidence_view(
                        raw_output_text,
                        evidence_id=evidence_id,
                        critical_terms=critical_evidence_terms,
                    ),
                    "error": _compact_text(raw_error, 1_000),
                    "decision_context": _compact_text(content, 600),
                    "_raw_input": str(raw_input or ""),
                    "_raw_output": raw_output_text,
                }
            )
    selected = _select_calls(calls)
    public_selected = [_public_call(call) for call in selected]
    final_output = assistant_outputs[-1] if assistant_outputs else {}
    delivered_output, delivered_reference = _finish_delivery(calls)
    if delivered_output:
        final_output = {"content": delivered_output, **delivered_reference}
        delivery_channel = "finish_tool_request"
    else:
        delivery_channel = "assistant_message"
    sequence = [str(call.get("tool") or "") for call in calls]
    return {
        "trial_id": trace_id,
        "role": role,
        "passed": passed if isinstance(passed, bool) else None,
        "score": score if _is_number(score) else None,
        "exit_reason": str(exit_reason or ""),
        "tool_call_count": len(calls),
        "tool_sequence": sequence[:_MAX_TOOL_SEQUENCE],
        "tool_sequence_truncated": len(sequence) > _MAX_TOOL_SEQUENCE,
        "selected_actions": public_selected,
        "selection_coverage": {
            "policy": "decision_linked_dynamic_budget",
            "selected_count": len(selected),
            "omitted_count": max(0, len(calls) - len(selected)),
            "failed_call_count": sum(1 for call in calls if _call_failed(call)),
            "selected_failed_call_count": sum(1 for call in selected if _call_failed(call)),
            "state_mutation_call_count": sum(1 for call in calls if _call_mutates_state(call)),
            "selected_state_mutation_call_count": sum(1 for call in selected if _call_mutates_state(call)),
            "content_evidence_call_count": sum(1 for call in calls if _call_has_content_evidence(call)),
            "selected_content_evidence_call_count": sum(1 for call in selected if _call_has_content_evidence(call)),
        },
        "final_output": {
            **_output_summary(str(final_output.get("content") or "")),
            "delivery_channel": delivery_channel,
            "evidence_ref": {
                "trace_id": trace_id,
                "role": role,
                "message_index": final_output.get("message_index", ""),
                "step_pointer": final_output.get("step_pointer", ""),
            },
        },
        "_raw_message_count": len(messages),
        "_all_calls": calls,
    }


def _finish_delivery(calls: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for call in reversed(calls):
        if str(call.get("tool") or "").lower() != "finish":
            continue
        raw_input = str(call.get("_raw_input") or "")
        try:
            payload = json.loads(raw_input)
        except json.JSONDecodeError:
            continue
        answer = payload.get("answer") if isinstance(payload, Mapping) else None
        if isinstance(answer, str) and answer.strip():
            return answer, {
                "message_index": call.get("message_index", ""),
                "step_pointer": call.get("step_pointer", ""),
            }
    return "", {}


def _select_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    budget = min(
        _MAX_SELECTED_CALLS_PER_TRIAL,
        max(_MIN_SELECTED_CALLS_PER_TRIAL, (len(calls) * 2 + 2) // 3),
    )
    if len(calls) <= budget:
        for call in calls:
            call["selection_reasons"] = ["complete_trace"]
        return calls

    first_by_tool: dict[str, int] = {}
    last_by_tool: dict[str, int] = {}
    for index, call in enumerate(calls):
        tool = str(call.get("tool") or "")
        first_by_tool.setdefault(tool, index)
        last_by_tool[tool] = index

    first_indexes = set(first_by_tool.values())
    last_indexes = set(last_by_tool.values())

    def _reasons(index: int) -> list[str]:
        call = calls[index]
        reasons: list[str] = []
        if _call_failed(call):
            reasons.append("observed_failure")
        if _call_mutates_state(call):
            reasons.append("state_mutation")
        if _call_has_content_evidence(call):
            reasons.append("content_evidence")
        if index in first_indexes:
            reasons.append("first_use_of_tool")
        if index in last_indexes:
            reasons.append("last_use_of_tool")
        if index >= len(calls) - 3:
            reasons.append("terminal_window")
        return reasons

    ranked = sorted(
        range(len(calls)),
        key=lambda index: (
            not _call_failed(calls[index]),
            not _call_mutates_state(calls[index]),
            not _call_has_content_evidence(calls[index]),
            index not in first_indexes and index not in last_indexes,
            index < len(calls) - 3,
            -len(str(calls[index].get("_raw_output") or "")),
            index,
        ),
    )
    selected_indexes = set(ranked[:budget])
    for index in selected_indexes:
        calls[index]["selection_reasons"] = _reasons(index) or ["dynamic_budget"]
    return [calls[index] for index in sorted(selected_indexes)]


def _public_call(call: Mapping[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in call.items()
        if not key.startswith("_") and (value not in ("", {}, []) or key in {"evidence_id", "tool", "request"})
    }
    if not _call_failed(call) and not _call_mutates_state(call):
        public.pop("decision_context", None)
    return public


def _deduplicate_selected_payloads(trials: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for trial in trials:
        for call in trial.get("selected_actions", []):
            payload = {
                "tool": call.get("tool"),
                "request": call.get("request"),
                "response": call.get("response"),
                "error": call.get("error"),
            }
            fingerprint = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            original = seen.get(fingerprint)
            if original:
                call["duplicate_of"] = original
                call.pop("request", None)
                call.pop("response", None)
                call.pop("decision_context", None)
            else:
                seen[fingerprint] = str(call.get("evidence_id") or "")


def _tool_contract_observations(
    task_contract: Mapping[str, Any],
    calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    raw_schemas = task_contract.get("tool_schemas")
    if isinstance(raw_schemas, list):
        for schema in raw_schemas:
            if isinstance(schema, Mapping):
                name = str(schema.get("name") or "")
                if name:
                    schemas[name] = dict(schema)

    observed_request_fields: dict[str, set[str]] = defaultdict(set)
    observed_response_fields: dict[str, set[str]] = defaultdict(set)
    observed_response_leaf_fields: dict[str, set[str]] = defaultdict(set)
    call_counts: Counter[str] = Counter()
    for call in calls:
        tool = str(call.get("tool") or "")
        if not tool:
            continue
        call_counts[tool] += 1
        request = call.get("request")
        response = call.get("response")
        if isinstance(request, Mapping):
            observed_request_fields[tool].update(map(str, request))
        if isinstance(response, Mapping):
            observed_response_fields[tool].update(map(str, response))
            observed_response_leaf_fields[tool].update(_leaf_field_names(response))

    observations: list[dict[str, Any]] = []
    for tool in sorted(set(call_counts) | set(schemas)):
        schema = schemas.get(tool, {})
        allowed = set(map(str, schema.get("allowed_request_fields", [])))
        required = set(map(str, schema.get("required_request_fields", [])))
        request_fields = observed_request_fields[tool]
        response_fields = observed_response_fields[tool]
        response_leaf_fields = observed_response_leaf_fields[tool]
        observation = {
            "tool": tool,
            "call_count": call_counts[tool],
            "public_schema_available": bool(schema),
            "description": schema.get("description", ""),
            "allowed_request_fields": sorted(allowed),
            "required_request_fields": sorted(required),
            "request_field_contracts": schema.get("request_field_contracts", {}),
            "observed_request_fields": sorted(request_fields),
            "observed_response_fields": sorted(response_fields),
            "observed_response_leaf_fields": sorted(response_leaf_fields),
            "response_only_fields": sorted(response_fields - request_fields),
        }
        if schema:
            observation["response_fields_not_in_public_request_schema"] = sorted(response_fields - allowed)
            observation["response_leaf_fields_not_in_public_request_schema"] = sorted(response_leaf_fields - allowed)
            observation["observed_request_fields_outside_public_schema"] = sorted(request_fields - allowed)
        observations.append(observation)
    return observations


def _cross_trial_contrast(trials: list[dict[str, Any]]) -> dict[str, Any]:
    if not trials:
        return {"available": False, "reason": "no_normalized_traces"}
    sequences = [list(map(str, trial.get("tool_sequence", []))) for trial in trials]
    tool_sets = [set(sequence) for sequence in sequences]
    passed_ids = [trial["trial_id"] for trial in trials if trial.get("passed") is True]
    failed_ids = [trial["trial_id"] for trial in trials if trial.get("passed") is False]
    success_tools = set().union(
        *(tool_sets[index] for index, trial in enumerate(trials) if trial.get("passed") is True)
    )
    failure_tools = set().union(
        *(tool_sets[index] for index, trial in enumerate(trials) if trial.get("passed") is False)
    )
    stable_tools = set.intersection(*tool_sets) if tool_sets else set()
    return {
        "available": len(trials) > 1,
        "successful_trials": passed_ids,
        "failed_trials": failed_ids,
        "stable_tools": sorted(stable_tools),
        "success_only_tools": sorted(success_tools - failure_tools) if passed_ids and failed_ids else [],
        "failure_only_tools": sorted(failure_tools - success_tools) if passed_ids and failed_ids else [],
        "first_tool_sequence_divergence": _first_sequence_divergence(trials, sequences),
        "terminal_action_variants": _terminal_action_variants(trials),
        "final_output_comparison": [
            {
                "trial_id": trial["trial_id"],
                "passed": trial.get("passed"),
                "score": trial.get("score"),
                "character_count": trial.get("final_output", {}).get("character_count", 0),
                "line_count": trial.get("final_output", {}).get("line_count", 0),
                "delivery_channel": trial.get("final_output", {}).get("delivery_channel", ""),
                "artifact_mentions": trial.get("final_output", {}).get("artifact_mentions", []),
                "evidence_ref": trial.get("final_output", {}).get("evidence_ref", {}),
            }
            for trial in trials
        ],
    }


def _first_sequence_divergence(trials: list[dict[str, Any]], sequences: list[list[str]]) -> dict[str, Any]:
    if len(sequences) < 2:
        return {}
    max_length = max(map(len, sequences), default=0)
    for index in range(max_length):
        values = [sequence[index] if index < len(sequence) else "<end>" for sequence in sequences]
        if len(set(values)) > 1:
            return {
                "tool_index": index,
                "by_trial": {str(trial["trial_id"]): value for trial, value in zip(trials, values, strict=True)},
            }
    return {}


def _terminal_action_variants(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        actions = trial.get("selected_actions", [])
        for action in actions:
            tool = str(action.get("tool") or "")
            if _is_mutating_tool(tool):
                by_tool[tool].append(
                    {
                        "trial_id": trial["trial_id"],
                        "passed": trial.get("passed"),
                        "score": trial.get("score"),
                        "request": action.get("request"),
                        "evidence_id": action.get("evidence_id"),
                    }
                )
    return [{"tool": tool, "variants": variants} for tool, variants in sorted(by_tool.items())]


def _normalize_tool_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    function = schema.get("function")
    function = function if isinstance(function, Mapping) else schema
    name = str(function.get("name") or "")
    if not name:
        return {}
    parameters = function.get("parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    properties = parameters.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    return {
        "name": name,
        "description": _compact_text(function.get("description"), 500),
        "allowed_request_fields": sorted(map(str, properties)),
        "required_request_fields": sorted(map(str, parameters.get("required", []))),
        "request_field_contracts": {
            str(field): {
                key: _compact_value(spec.get(key))
                for key in ("type", "description", "enum", "items")
                if isinstance(spec, Mapping) and key in spec
            }
            for field, spec in properties.items()
        },
    }


def _judge_dimensions(metadata: Mapping[str, Any]) -> dict[str, Any]:
    detail = metadata.get("judge_detail")
    if not isinstance(detail, Mapping):
        return {}
    return {str(key): value for key, value in detail.items() if _is_scalar(value)}


def _leaf_field_names(value: Mapping[str, Any], *, depth: int = 0) -> set[str]:
    leaves: set[str] = set()
    for key, item in value.items():
        name = str(key)
        if isinstance(item, Mapping) and depth < 3:
            leaves.update(_leaf_field_names(item, depth=depth + 1))
        else:
            leaves.add(name)
    return leaves


def _output_summary(value: str) -> dict[str, Any]:
    text = str(value or "")
    artifacts = []
    for match in _ARTIFACT_PATTERN.finditer(text):
        candidate = match.group(1)
        if candidate not in artifacts:
            artifacts.append(candidate)
    return {
        "character_count": len(text),
        "line_count": len(text.splitlines()),
        "artifact_mentions": artifacts[:12],
        "excerpt": _head_tail(text, 1_600),
    }


def _failed_requirement_terms(metadata: Mapping[str, Any]) -> list[str]:
    """Extract bounded anchors from failed evaluator criteria.

    These terms are used only to retain exact public trajectory spans. They do
    not become conclusions and do not change the evaluator outcome.
    """
    normalized = metadata.get("judge_evidence")
    detail = normalized if isinstance(normalized, Mapping) else metadata.get("judge_detail")
    detail = detail if isinstance(detail, Mapping) else {}
    criteria = detail.get("criteria")
    criteria = criteria if isinstance(criteria, list) else []
    values: list[str] = []
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            continue
        score = criterion.get("score")
        if _is_number(score) and float(score) > 0:
            continue
        values.extend(
            str(criterion.get(key) or "") for key in ("criterion_id", "verifier_id", "rationale") if criterion.get(key)
        )

    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in _CRITICAL_EVIDENCE_TERM_PATTERN.findall(value):
            normalized_term = match.casefold()
            if normalized_term in _CRITICAL_EVIDENCE_STOPWORDS or normalized_term in seen:
                continue
            seen.add(normalized_term)
            terms.append(match)
            if len(terms) >= 48:
                return terms
    return terms


def extract_critical_evidence_spans(
    value: Any,
    terms: Sequence[str],
    *,
    max_spans: int = 3,
    max_total_chars: int = 6_000,
) -> list[dict[str, Any]]:
    """Return exact line windows tied to failed-requirement terms.

    Unlike the display excerpt, these windows are selected from the raw tool
    response before compaction. The returned text never uses an unlabelled
    omission marker.
    """
    text, projection = _readable_evidence_text(str(value or ""))
    normalized_terms = list(dict.fromkeys(str(term).casefold() for term in terms if str(term).strip()))
    if not text or not normalized_terms:
        return []
    if max_spans <= 0 or max_total_chars <= 0:
        return []

    lines = text.splitlines() or [text]
    ranked: list[tuple[int, int, list[str]]] = []
    for index, line in enumerate(lines):
        lowered = line.casefold()
        matched = [term for term in normalized_terms if term in lowered]
        if matched:
            ranked.append((len(set(matched)), index, matched))
    if not ranked:
        return []

    selected_lines = sorted(ranked, key=lambda item: (-item[0], item[1]))[:max_spans]
    windows: list[tuple[int, int, set[str]]] = []
    for _, index, matched in sorted(selected_lines, key=lambda item: item[1]):
        start = max(0, index - 3)
        end = min(len(lines), index + 4)
        if windows and start <= windows[-1][1]:
            previous_start, previous_end, previous_terms = windows[-1]
            windows[-1] = (previous_start, max(previous_end, end), previous_terms | set(matched))
        else:
            windows.append((start, end, set(matched)))

    spans: list[dict[str, Any]] = []
    remaining = max_total_chars
    for start, end, matched in windows:
        if remaining <= 0:
            break
        raw_span = "\n".join(lines[start:end])
        span_text, complete = _bounded_critical_span(raw_span, matched, remaining)
        if not span_text:
            continue
        spans.append(
            {
                "source": "raw_tool_response",
                "projection": projection,
                "line_start": start + 1,
                "line_end": end,
                "matched_terms": sorted(matched),
                "text": span_text,
                "window_complete": complete,
            }
        )
        remaining -= len(span_text)
    return spans


def _readable_evidence_text(text: str) -> tuple[str, str]:
    """Project serialized tool-result newlines without semantic summarization."""
    if "\n" not in text and "\\n" in text:
        return (
            text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t"),
            "escaped_newlines_normalized",
        )
    return text, "verbatim"


def _bounded_critical_span(text: str, matched_terms: set[str], limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, True
    lowered = text.casefold()
    positions = [lowered.find(term) for term in matched_terms if lowered.find(term) >= 0]
    center = min(positions) if positions else len(text) // 2
    headroom = max(0, limit // 3)
    start = max(0, center - headroom)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    return text[start:end], False


def _response_evidence_view(
    raw_output: str,
    *,
    evidence_id: str,
    critical_terms: Sequence[str],
) -> dict[str, Any]:
    source_chars = len(raw_output)
    return {
        "raw_evidence_ref": evidence_id,
        "source": "public_normalized_execution_trace.raw_tool_response",
        "source_char_count": source_chars,
        "display_excerpt_complete": source_chars <= 1_200,
        "display_omission_origin": "none" if source_chars <= 1_200 else "analyzer_evidence_compactor",
        "task_agent_observed_display_omission_marker": False,
        "critical_spans": extract_critical_evidence_spans(raw_output, critical_terms),
    }


def _parse_and_compact(value: Any) -> Any:
    if not isinstance(value, str):
        return _compact_value(value)
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _head_tail(text, 2_000)
    return _compact_value(parsed)


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return _compact_text(value, 500)
    if isinstance(value, Mapping):
        items = list(value.items())
        compact = {str(key): _compact_value(item, depth=depth + 1) for key, item in items[:30]}
        if len(items) > 30:
            compact["_omitted_field_count"] = len(items) - 30
        return compact
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = list(value)
        compact_items = [_compact_value(item, depth=depth + 1) for item in items[:30]]
        if len(items) > 30:
            compact_items.append({"_omitted_item_count": len(items) - 30})
        return compact_items
    if isinstance(value, str):
        return _head_tail(value, 1_200)
    if _is_scalar(value):
        return value
    return _compact_text(value, 500)


def _aligned_list(value: Any, expected_length: int) -> list[Any]:
    return list(value) if isinstance(value, list) and len(value) == expected_length else []


def _is_mutating_tool(tool: str) -> bool:
    return bool(_MUTATING_TOOL_PATTERN.search(tool))


def _call_failed(call: Mapping[str, Any]) -> bool:
    if str(call.get("error") or "").strip():
        return True
    return bool(_FAILED_RESPONSE_PATTERN.search(str(call.get("_raw_output") or call.get("response") or "")))


def _call_mutates_state(call: Mapping[str, Any]) -> bool:
    tool = str(call.get("tool") or "")
    if _is_mutating_tool(tool):
        return True
    if tool.lower() not in {"bash", "shell", "exec", "exec_command"}:
        return False
    return bool(_BASH_MUTATION_PATTERN.search(_call_command(call)))


def _call_has_content_evidence(call: Mapping[str, Any]) -> bool:
    if _call_failed(call):
        return False
    tool = str(call.get("tool") or "").lower()
    raw_output = str(call.get("_raw_output") or call.get("response") or "")
    if len(raw_output) < 300:
        return False
    if tool.startswith("read") or tool in {"open_file", "view_file"}:
        return True
    return tool in {"bash", "shell", "exec", "exec_command"} and bool(_BASH_CONTENT_PATTERN.search(_call_command(call)))


def _call_command(call: Mapping[str, Any]) -> str:
    raw_input = call.get("_raw_input") or call.get("request") or ""
    if isinstance(raw_input, Mapping):
        return str(raw_input.get("command") or raw_input.get("cmd") or "")
    if not isinstance(raw_input, str):
        return ""
    try:
        parsed = json.loads(raw_input)
    except json.JSONDecodeError:
        return raw_input
    if not isinstance(parsed, Mapping):
        return raw_input
    return str(parsed.get("command") or parsed.get("cmd") or "")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _compact_text(value: Any, limit: int) -> str:
    return _head_tail(str(value or ""), limit)


def _head_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.7))
    tail = max(1, limit - head)
    omitted = len(text) - head - tail
    marker = _COMPACTION_MARKER.format(omitted=omitted)
    return f"{text[:head]}\n{marker}\n{text[-tail:]}"


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}
