# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Filesystem readers for evaluation artifacts consumed by the analyzer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class EvaluationSummaryInput:
    """Aggregated evaluation summary used as analyzer context."""

    total_cases: int = 0
    passed_count: int = 0
    failed_count: int = 0
    average_score: float = 0.0
    evaluation_method: str = ""


@dataclass(frozen=True, slots=True)
class DeterministicSignals:
    """Zero-LLM pre-extracted signals from evaluation case outputs."""

    method: str = ""
    exec_failures: list[str] = field(default_factory=list)
    judge_failures: list[str] = field(default_factory=list)
    error_clusters: list[dict[str, Any]] = field(default_factory=list)
    method_specific: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CaseAnalysisInput:
    """Structured input for one evaluated case, assembled from result + trace files."""

    case_id: str
    status: str
    score: float
    input: str
    expected: str | None
    response: str
    error: str
    evaluation_method: str
    evaluation_passed: bool
    evaluation_reason: str
    evaluation_metadata: dict[str, Any]
    trace_path: str
    result_path: str
    trajectory_window_summary: dict[str, Any] = field(default_factory=dict)
    normalized_trace_summary: dict[str, Any] = field(default_factory=dict)
    training_signal: dict[str, Any] = field(default_factory=dict)
    benchmark_test_contract: dict[str, Any] = field(default_factory=dict)


class CaseReader:
    """Read structured evaluation artifacts from the filesystem."""

    @staticmethod
    def read_eval_ref(path: str) -> dict[str, Any]:
        """Load an eval_ref YAML file and return its contents as a mapping.

        Args:
            path: Filesystem path to the eval_ref YAML file.

        Returns:
            Parsed YAML content as a dictionary.

        Raises:
            ValueError: If the path does not exist.
        """
        p = Path(path)
        if not p.exists():
            raise ValueError(f"eval_ref path not found: {path}")
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    @staticmethod
    def read_summary(path: str) -> EvaluationSummaryInput:
        """Load a summary.json file and map its fields to EvaluationSummaryInput.

        Applies G4 field mapping: ``passed_cases`` → ``passed_count``,
        ``failed_cases`` → ``failed_count``.  Returns a zero-valued summary
        when ``path`` is empty or the file does not exist.

        Args:
            path: Filesystem path to the summary JSON file, or empty string.

        Returns:
            Populated EvaluationSummaryInput instance.
        """
        if not path:
            return EvaluationSummaryInput()
        p = Path(path)
        if not p.exists():
            return EvaluationSummaryInput()
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return EvaluationSummaryInput(
            total_cases=int(data.get("total_cases", 0)),
            passed_count=int(data.get("passed_cases", data.get("passed_count", 0))),
            failed_count=int(data.get("failed_cases", data.get("failed_count", 0))),
            average_score=float(data.get("average_score", 0.0)),
            evaluation_method=str(data.get("evaluation_method", "")),
        )

    @staticmethod
    def read_case_inputs(directory: str) -> list[CaseAnalysisInput]:
        """Scan a case results directory and build CaseAnalysisInput objects.

        For each ``*/result.json`` found under ``dir``, the method also reads
        the sibling ``trace.json`` to obtain ``input`` and ``response`` (G3).

        Args:
            directory: Path to the case results directory. Returns an empty list
                when the directory does not exist.

        Returns:
            List of CaseAnalysisInput objects, sorted by case directory name.
        """
        case_results_dir = Path(directory)
        if not case_results_dir.is_dir():
            return []

        results: list[CaseAnalysisInput] = []
        dataset_case_cache: dict[Path, dict[str, dict[str, Any]]] = {}
        for result_path in sorted(case_results_dir.glob("*/result.json")):
            case_dir = result_path.parent
            trace_path = case_dir / "trace.json"

            result_data: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
            trace_data: dict[str, Any] = {}
            if trace_path.exists():
                trace_data = json.loads(trace_path.read_text(encoding="utf-8"))

            evaluation: dict[str, Any] = result_data.get("evaluation") or {}
            eval_metadata: dict[str, Any] = evaluation.get("metadata") or {}
            result_metadata = result_data.get("metadata") or {}
            training_signal = result_metadata.get("training_signal") if isinstance(result_metadata, dict) else {}
            if not isinstance(training_signal, dict):
                training_signal = {}
            case_id = str(result_data.get("case_id", case_dir.name))
            benchmark_test_contract = _read_benchmark_test_contract(
                case_id=case_id,
                result_metadata=result_metadata,
                dataset_case_cache=dataset_case_cache,
            )

            results.append(
                CaseAnalysisInput(
                    case_id=case_id,
                    status=str(result_data.get("status", "")),
                    score=float(result_data.get("score") or 0.0),
                    input=str(trace_data.get("input", "")),
                    expected=None,
                    response=str(trace_data.get("response", result_data.get("result", ""))),
                    error=str(result_data.get("error") or ""),
                    evaluation_method=str(evaluation.get("method", "")),
                    evaluation_passed=bool(evaluation.get("passed", False)),
                    evaluation_reason=str(evaluation.get("reason", "")),
                    evaluation_metadata=eval_metadata,
                    trace_path=str(trace_path),
                    result_path=str(result_path),
                    trajectory_window_summary=_bounded_trajectory_window_summary(trace_data.get("behavior_trace", {})),
                    normalized_trace_summary=_bounded_normalized_trace_summary(
                        trace_data.get("behavior_trace", {}),
                        case_dir=case_dir,
                    ),
                    training_signal=training_signal,
                    benchmark_test_contract=benchmark_test_contract,
                )
            )

        return results


def _read_benchmark_test_contract(
    *,
    case_id: str,
    result_metadata: Any,
    dataset_case_cache: dict[Path, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Read the current case's acceptance tests without exposing its solution patch."""
    if not isinstance(result_metadata, dict):
        return {}
    raw_case_path = result_metadata.get("case_path")
    if not isinstance(raw_case_path, str) or not raw_case_path.strip():
        return {}

    dataset_path = Path(raw_case_path).expanduser()
    if not dataset_path.is_absolute():
        dataset_path = Path.cwd() / dataset_path
    dataset_path = dataset_path.resolve()

    cases_by_id = dataset_case_cache.get(dataset_path)
    if cases_by_id is None:
        cases_by_id = _read_dataset_cases(dataset_path)
        dataset_case_cache[dataset_path] = cases_by_id

    case_data = cases_by_id.get(case_id)
    if not isinstance(case_data, dict):
        return {}
    contract = case_data.get("verification_contract")
    if not isinstance(contract, dict):
        return {}

    fail_to_pass = _test_id_list(contract.get("fail_to_pass", contract.get("must_pass")))
    pass_to_pass = _test_id_list(contract.get("pass_to_pass", contract.get("regression")))
    test_patch = contract.get("test_patch", contract.get("acceptance_probe"))
    test_patch = test_patch if isinstance(test_patch, str) else ""
    if not fail_to_pass and not pass_to_pass and not test_patch:
        return {}

    return {
        "provenance": f"{dataset_path}#case_id={case_id}.verification_contract",
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "test_patch": test_patch,
    }


def _read_dataset_cases(dataset_path: Path) -> dict[str, dict[str, Any]]:
    try:
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_cases = dataset.get("cases") if isinstance(dataset, dict) else dataset
    if not isinstance(raw_cases, list):
        return {}
    cases: dict[str, dict[str, Any]] = {}
    for case in raw_cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", case.get("instance_id", ""))).strip()
        if case_id:
            cases[case_id] = case
    return cases


def _test_id_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        value = decoded if isinstance(decoded, list) else [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _bounded_trajectory_window_summary(behavior_trace: Any) -> dict[str, Any]:
    """Read only the bounded trajectory window summary from trace.json."""
    if not isinstance(behavior_trace, dict):
        return {}
    summary = behavior_trace.get("trajectory_window_summary")
    if not isinstance(summary, dict):
        return {}
    recent_events = summary.get("recent_events")
    bounded: dict[str, Any] = {
        "window_size": summary.get("window_size"),
        "event_count": summary.get("event_count"),
        "failure_signatures": _string_list(summary.get("failure_signatures"), 20),
    }
    if isinstance(recent_events, list):
        bounded["recent_events"] = [
            {
                "event_index": item.get("event_index"),
                "event_type": _excerpt(str(item.get("event_type", "")), 80),
                "summary": _excerpt(str(item.get("summary", "")), 1200),
            }
            for item in recent_events[:30]
            if isinstance(item, dict)
        ]
    return bounded


def _bounded_normalized_trace_summary(behavior_trace: Any, *, case_dir: Path) -> dict[str, Any]:
    """Read a bounded normalized trace summary for analyzer attribution."""
    trace_path = _resolve_normalized_trace_path(behavior_trace, case_dir=case_dir)
    if trace_path is None or not trace_path.is_file():
        return {}
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    traces = data.get("traces")
    bounded_traces: list[dict[str, Any]] = []
    if isinstance(traces, list):
        for trace in traces[:4]:
            if not isinstance(trace, dict):
                continue
            messages = trace.get("messages")
            bounded_messages: list[dict[str, Any]] = []
            if isinstance(messages, list):
                for message in messages[:40]:
                    if not isinstance(message, dict):
                        continue
                    bounded_messages.append(
                        {
                            "role": _excerpt(str(message.get("role", "")), 80),
                            "message_index": message.get("message_index"),
                            "content": _excerpt(str(message.get("content", "")), 1200),
                            "step_pointer": _excerpt(str(message.get("step_pointer", "")), 120),
                            "tool_calls": _bounded_tool_calls(message.get("tool_calls")),
                        }
                    )
            bounded_traces.append(
                {
                    "trace_id": _excerpt(str(trace.get("trace_id", "")), 240),
                    "member_id": _excerpt(str(trace.get("member_id", "")), 160),
                    "member_role": _excerpt(str(trace.get("member_role", "")), 160),
                    "execution_id": _excerpt(str(trace.get("execution_id", "")), 160),
                    "step_count": trace.get("step_count"),
                    "message_count": trace.get("message_count"),
                    "messages": bounded_messages,
                }
            )
    return {
        "case_id": _excerpt(str(data.get("case_id", "")), 160),
        "traces": bounded_traces,
    }


def _resolve_normalized_trace_path(behavior_trace: Any, *, case_dir: Path) -> Path | None:
    if isinstance(behavior_trace, dict):
        raw_path = behavior_trace.get("normalized_trace_path")
        if isinstance(raw_path, str) and raw_path:
            path = Path(raw_path)
            return path if path.is_absolute() else case_dir / path
    default_path = case_dir / "judge" / "normalized_trace.json"
    return default_path


def _bounded_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        calls.append(
            {
                "name": _excerpt(str(item.get("name", "")), 120),
                "input": _excerpt(str(item.get("input", "")), 1200),
                "output": _excerpt(str(item.get("output", "")), 1200),
                "error": _excerpt(str(item.get("error", "")), 1200),
                "step_pointer": _excerpt(str(item.get("step_pointer", "")), 120),
            }
        )
    return calls


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


def _excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


__all__ = [
    "CaseAnalysisInput",
    "CaseReader",
    "DeterministicSignals",
    "EvaluationSummaryInput",
]
