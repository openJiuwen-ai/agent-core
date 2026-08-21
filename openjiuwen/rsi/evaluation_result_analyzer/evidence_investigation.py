# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded, read-only evidence acquisition for causal RSI diagnosis."""

from __future__ import annotations

import ast
import json
import math
import re
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput

_ALLOWED_OPERATIONS = {
    "check_relation",
    "compare_numeric_change",
    "compare_runs",
    "inspect_artifact",
    "read_repository_file",
    "read_event",
    "search_repository",
    "search_trace",
}
_MAX_HYPOTHESES = 3
_MAX_REQUESTS = 12
_MAX_SEARCH_RESULTS = 5
_MAX_EVENT_CHARS = 12_000
_MAX_ARTIFACT_FILE_CHARS = 200_000
_MAX_ARTIFACT_FILES = 100
_MAX_REPOSITORY_FILES = 2_000
_MAX_STRUCTURED_CELLS = 20_000
_MAX_STRUCTURED_PAGES = 100
_TEXT_ARTIFACT_SUFFIXES = {
    ".csv",
    ".diff",
    ".json",
    ".log",
    ".md",
    ".patch",
    ".txt",
    ".yaml",
    ".yml",
}
_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{1,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,8}")
_NUMERIC_CHANGE_PATTERN = re.compile(
    r"(?:\b(?:pp|percentage\s+points?|numeric\s+delta|formula\s+delta)\b|%|百分点|数值差|公式)",
    re.IGNORECASE,
)
_STOPWORDS = {
    "about",
    "after",
    "agent",
    "before",
    "case",
    "evidence",
    "failure",
    "from",
    "into",
    "missing",
    "output",
    "result",
    "should",
    "task",
    "that",
    "the",
    "this",
    "tool",
    "trace",
    "with",
}


def normalize_causal_investigation(
    value: Mapping[str, Any] | None,
    *,
    failed_requirement_ids: Sequence[str] = (),
    max_requests: int = _MAX_REQUESTS,
    min_hypotheses: int = 1,
    require_evidence_per_hypothesis: bool = False,
) -> dict[str, Any] | None:
    """Validate and bound a model-proposed causal investigation plan."""
    if not isinstance(value, Mapping):
        return None
    raw = value.get("causal_investigation", value)
    if not isinstance(raw, Mapping):
        return None
    raw_hypotheses = raw.get("hypotheses")
    if not isinstance(raw_hypotheses, list) or not raw_hypotheses:
        return None

    allowed_requirements = {str(item) for item in failed_requirement_ids if str(item)}
    hypotheses: list[dict[str, Any]] = []
    hypothesis_ids: set[str] = set()
    hypothesis_semantics: set[tuple[str, str]] = set()
    raw_requests: list[tuple[str, Mapping[str, Any]]] = []
    for index, item in enumerate(raw_hypotheses[:_MAX_HYPOTHESES], start=1):
        if not isinstance(item, Mapping):
            continue
        claim = str(item.get("claim", "") or "").strip()
        falsified_if = str(item.get("falsified_if", "") or "").strip()
        if not claim or not falsified_if:
            continue
        semantics = (_normalized_semantics(claim), _normalized_semantics(falsified_if))
        if semantics in hypothesis_semantics:
            continue
        hypothesis_semantics.add(semantics)
        hypothesis_id = str(item.get("hypothesis_id", "") or f"h{index}").strip()
        if not hypothesis_id or hypothesis_id in hypothesis_ids:
            hypothesis_id = f"h{index}"
        hypothesis_ids.add(hypothesis_id)
        explains = _string_list(item.get("explains_requirement_ids"))
        if allowed_requirements:
            explains = [requirement_id for requirement_id in explains if requirement_id in allowed_requirements]
        hypotheses.append(
            {
                "hypothesis_id": hypothesis_id,
                "claim": claim,
                "explains_requirement_ids": explains,
                "current_support": _string_list(item.get("current_support"))[:8],
                "falsified_if": falsified_if,
                "numeric_change_check_required": bool(_NUMERIC_CHANGE_PATTERN.search(f"{claim}\n{falsified_if}")),
            }
        )
        requests = item.get("evidence_requests")
        if isinstance(requests, list):
            raw_requests.extend((hypothesis_id, request) for request in requests if isinstance(request, Mapping))

    top_level_requests = raw.get("evidence_requests")
    if isinstance(top_level_requests, list):
        raw_requests.extend(("", request) for request in top_level_requests if isinstance(request, Mapping))
    if len(hypotheses) < max(1, min(_MAX_HYPOTHESES, int(min_hypotheses))):
        return None

    request_limit = min(_MAX_REQUESTS, max(0, int(max_requests)))
    requests: list[dict[str, Any]] = []
    seen_requests: set[str] = set()
    for default_hypothesis_id, request in raw_requests:
        normalized = _normalize_request(
            request,
            default_hypothesis_id=default_hypothesis_id,
            known_hypothesis_ids=hypothesis_ids,
            index=len(requests) + 1,
        )
        if normalized is None:
            continue
        fingerprint = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if fingerprint in seen_requests:
            continue
        seen_requests.add(fingerprint)
        if request_limit == 0:
            break
        requests.append(normalized)
        if len(requests) >= request_limit:
            break

    if require_evidence_per_hypothesis:
        covered = {
            hypothesis_id for request in requests for hypothesis_id in _string_list(request.get("hypothesis_ids"))
        }
        if not hypothesis_ids.issubset(covered):
            return None

    return {
        "schema_version": 1,
        "hypotheses": hypotheses,
        "evidence_requests": requests,
        "ready_without_more_evidence": bool(raw.get("ready_without_more_evidence")) and not requests,
    }


def execute_causal_investigation(
    case: CaseAnalysisInput,
    investigation: Mapping[str, Any],
    *,
    prior_candidate_feedback: Mapping[str, Any] | None = None,
    evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Execute only controller-owned evidence operations for one public case."""
    trace_data = _read_json(Path(case.result_path).parent / "judge" / "normalized_trace.json")
    events = _trace_events(trace_data)
    repository_dir = _repository_dir(evidence_root)
    discovered_repository_paths: set[str] = set()
    results: list[dict[str, Any]] = []
    for request in investigation.get("evidence_requests", []):
        if not isinstance(request, Mapping):
            continue
        operation = str(request.get("operation", "") or "")
        if operation == "check_relation":
            evidence = _check_relation(request)
        elif operation == "compare_numeric_change":
            evidence = _compare_numeric_change(request)
        elif operation == "search_trace":
            evidence = _search_trace(events, request)
        elif operation == "read_event":
            evidence = _read_event(events, request)
        elif operation == "inspect_artifact":
            evidence = _inspect_artifact(case, request)
        elif operation == "search_repository":
            evidence = _search_repository(repository_dir, request)
            discovered_repository_paths.update(
                str(item.get("relative_path", "") or "")
                for item in evidence.get("files", [])
                if isinstance(item, Mapping) and str(item.get("relative_path", "") or "")
            )
        elif operation == "read_repository_file":
            evidence = _read_repository_file(
                repository_dir,
                request,
                discovered_paths=discovered_repository_paths,
            )
        elif operation == "compare_runs":
            evidence = _compare_runs(prior_candidate_feedback, request)
        else:
            continue
        results.append(
            {
                "request_id": str(request.get("request_id", "") or ""),
                "hypothesis_ids": _string_list(request.get("hypothesis_ids")),
                "operation": operation,
                "purpose": str(request.get("purpose", "") or ""),
                **evidence,
            }
        )

    return {
        "schema_version": 1,
        "policy": {
            "controller_owned": True,
            "read_only": True,
            "arbitrary_shell_or_path_access": False,
            "display_omissions_are_not_task_agent_observations": True,
        },
        "hypotheses": list(investigation.get("hypotheses", [])),
        "request_count": len(investigation.get("evidence_requests", [])),
        "completed_request_count": len(results),
        "results": results,
    }


def _normalize_request(
    request: Mapping[str, Any],
    *,
    default_hypothesis_id: str,
    known_hypothesis_ids: set[str],
    index: int,
) -> dict[str, Any] | None:
    operation = str(request.get("operation", "") or "").strip().casefold()
    if operation not in _ALLOWED_OPERATIONS:
        return None
    query = str(request.get("query", "") or "").strip()
    if operation in {"search_trace", "inspect_artifact", "search_repository"} and not query:
        return None
    if operation == "read_repository_file" and not _safe_relative_path(request.get("relative_path")):
        return None
    if operation == "check_relation" and not str(request.get("expression", "") or "").strip():
        return None
    if operation == "compare_numeric_change" and not all(
        str(request.get(key, "") or "").strip() for key in ("before_expression", "after_expression")
    ):
        return None
    hypothesis_ids = _string_list(request.get("hypothesis_ids"))
    if default_hypothesis_id:
        hypothesis_ids.append(default_hypothesis_id)
    hypothesis_ids = list(dict.fromkeys(item for item in hypothesis_ids if item in known_hypothesis_ids))
    normalized: dict[str, Any] = {
        "request_id": str(request.get("request_id", "") or f"q{index}").strip() or f"q{index}",
        "hypothesis_ids": hypothesis_ids,
        "operation": operation,
        "purpose": str(request.get("purpose", "") or "").strip(),
    }
    if query:
        normalized["query"] = query[:1_000]
    if operation == "check_relation":
        expected = _finite_number(request.get("expected"))
        if expected is None:
            return None
        normalized.update(
            {
                "expression": str(request.get("expression", "") or "").strip()[:200],
                "operator": str(request.get("operator", "approximately_equal") or "approximately_equal")
                .strip()
                .casefold(),
                "expected": expected,
                "tolerance": _bounded_tolerance(request.get("tolerance")),
            }
        )
    if operation == "compare_numeric_change":
        expected_delta = _finite_number(request.get("expected_delta"))
        if expected_delta is None:
            return None
        normalized.update(
            {
                "before_expression": str(request.get("before_expression", "") or "").strip()[:200],
                "after_expression": str(request.get("after_expression", "") or "").strip()[:200],
                "expected_delta": expected_delta,
                "tolerance": _bounded_tolerance(request.get("tolerance")),
            }
        )
    if operation == "search_trace":
        normalized["max_results"] = min(
            _MAX_SEARCH_RESULTS,
            max(1, _positive_int(request.get("max_results"), default=3)),
        )
    if operation == "search_repository":
        normalized["max_results"] = min(
            _MAX_SEARCH_RESULTS,
            max(1, _positive_int(request.get("max_results"), default=3)),
        )
    if operation == "read_repository_file":
        normalized["relative_path"] = _safe_relative_path(request.get("relative_path"))
    if operation == "read_event":
        message_index = _optional_nonnegative_int(request.get("message_index"))
        if message_index is None:
            return None
        normalized["trace_id"] = str(request.get("trace_id", "") or "").strip()
        normalized["message_index"] = message_index
        tool_call_index = _optional_nonnegative_int(request.get("tool_call_index"))
        if tool_call_index is not None:
            normalized["tool_call_index"] = tool_call_index
    return normalized


def _check_relation(request: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a small numeric discriminator without executing model code."""
    expression = str(request.get("expression", "") or "").strip()
    expected = _finite_number(request.get("expected"))
    operator = str(request.get("operator", "approximately_equal") or "approximately_equal").casefold()
    tolerance = _bounded_tolerance(request.get("tolerance"))
    if expected is None:
        return {"availability": "invalid", "reason": "expected_must_be_a_finite_number"}
    try:
        value = _safe_numeric_expression(expression)
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return {
            "availability": "invalid",
            "expression": expression,
            "reason": f"invalid_numeric_expression:{type(exc).__name__}",
        }

    if operator in {"approximately_equal", "equal"}:
        holds = math.isclose(value, expected, rel_tol=0.0, abs_tol=tolerance)
    elif operator == "not_equal":
        holds = not math.isclose(value, expected, rel_tol=0.0, abs_tol=tolerance)
    elif operator == "less_than":
        holds = value < expected
    elif operator == "less_than_or_equal":
        holds = value <= expected or math.isclose(value, expected, rel_tol=0.0, abs_tol=tolerance)
    elif operator == "greater_than":
        holds = value > expected
    elif operator == "greater_than_or_equal":
        holds = value >= expected or math.isclose(value, expected, rel_tol=0.0, abs_tol=tolerance)
    else:
        return {
            "availability": "invalid",
            "expression": expression,
            "value": value,
            "reason": "unsupported_relation_operator",
        }
    return {
        "availability": "available",
        "expression": expression,
        "value": value,
        "operator": operator,
        "expected": expected,
        "tolerance": tolerance,
        "holds": holds,
    }


def _compare_numeric_change(request: Mapping[str, Any]) -> dict[str, Any]:
    """Compute after-before so formula changes are compared to their real baseline."""
    before_expression = str(request.get("before_expression", "") or "").strip()
    after_expression = str(request.get("after_expression", "") or "").strip()
    expected_delta = _finite_number(request.get("expected_delta"))
    tolerance = _bounded_tolerance(request.get("tolerance"))
    if expected_delta is None:
        return {"availability": "invalid", "reason": "expected_delta_must_be_a_finite_number"}
    try:
        before = _safe_numeric_expression(before_expression)
        after = _safe_numeric_expression(after_expression)
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return {
            "availability": "invalid",
            "before_expression": before_expression,
            "after_expression": after_expression,
            "reason": f"invalid_numeric_expression:{type(exc).__name__}",
        }
    delta = after - before
    return {
        "availability": "available",
        "before_expression": before_expression,
        "after_expression": after_expression,
        "before_value": before,
        "after_value": after,
        "computed_delta": delta,
        "expected_delta": expected_delta,
        "tolerance": tolerance,
        "holds": math.isclose(delta, expected_delta, rel_tol=0.0, abs_tol=tolerance),
    }


def _safe_numeric_expression(expression: str) -> float:
    """Evaluate only finite numeric literals and bounded arithmetic operators."""
    if not expression or len(expression) > 200:
        raise ValueError("expression_length")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 40:
        raise ValueError("expression_complexity")

    def _evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = _evaluate(node.operand)
            value = operand if isinstance(node.op, ast.UAdd) else -operand
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = _evaluate(node.left)
            right = _evaluate(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            else:
                value = left / right
        else:
            raise ValueError("unsupported_expression_node")
        if not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("non_finite_or_unbounded_result")
        return value

    return _evaluate(tree)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bounded_tolerance(value: Any) -> float:
    number = _finite_number(value)
    if number is None:
        return 1e-9
    return min(max(number, 0.0), 1.0)


def _trace_events(trace_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    traces = trace_data.get("traces")
    if not isinstance(traces, list):
        return events
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        trace_id = str(trace.get("trace_id", "") or "")
        messages = trace.get("messages")
        if not isinstance(messages, list):
            continue
        for sequence, message in enumerate(messages):
            if not isinstance(message, Mapping):
                continue
            raw_index = message.get("message_index", sequence)
            message_index = _optional_nonnegative_int(raw_index)
            if message_index is None:
                message_index = sequence
            tool_calls = [dict(call) for call in message.get("tool_calls", []) if isinstance(call, Mapping)]
            events.append(
                {
                    "trace_id": trace_id,
                    "message_index": message_index,
                    "step_pointer": str(message.get("step_pointer", "") or ""),
                    "role": str(message.get("role", "") or ""),
                    "content": str(message.get("content", "") or ""),
                    "tool_calls": tool_calls,
                }
            )
    return events


def _search_trace(events: list[dict[str, Any]], request: Mapping[str, Any]) -> dict[str, Any]:
    query = str(request.get("query", "") or "")
    terms = _query_terms(query)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for sequence, event in enumerate(events):
        searchable = json.dumps(event, ensure_ascii=False, separators=(",", ":")).casefold()
        matched = [term for term in terms if term in searchable]
        if matched:
            ranked.append((len(set(matched)), sequence, event))
    limit = min(_MAX_SEARCH_RESULTS, max(1, _positive_int(request.get("max_results"), default=3)))
    selected = sorted(ranked, key=lambda item: (-item[0], item[1]))[:limit]
    return {
        "availability": "available" if selected else "not_found",
        "query": query,
        "matched_event_count": len(ranked),
        "events": [_search_event_view(event, terms) for _, _, event in selected],
    }


def _read_event(events: list[dict[str, Any]], request: Mapping[str, Any]) -> dict[str, Any]:
    trace_id = str(request.get("trace_id", "") or "")
    message_index = _optional_nonnegative_int(request.get("message_index"))
    matches = [
        event
        for event in events
        if event["message_index"] == message_index and (not trace_id or event["trace_id"] == trace_id)
    ]
    if not matches:
        return {
            "availability": "not_found",
            "trace_id": trace_id,
            "message_index": message_index,
        }
    if not trace_id and len(matches) > 1:
        return {
            "availability": "ambiguous",
            "reason": "trace_id_required_for_non_unique_message_index",
            "message_index": message_index,
            "candidate_trace_ids": sorted({str(item.get("trace_id", "") or "") for item in matches}),
        }
    event = matches[0]
    tool_call_index = _optional_nonnegative_int(request.get("tool_call_index"))
    calls = event["tool_calls"]
    if tool_call_index is not None:
        calls = calls[tool_call_index : tool_call_index + 1]
    return {
        "availability": "available",
        "event": {
            "trace_id": event["trace_id"],
            "message_index": event["message_index"],
            "step_pointer": event["step_pointer"],
            "role": event["role"],
            "content": _bounded_exact_text(event["content"], _MAX_EVENT_CHARS),
            "tool_calls": [
                {
                    "tool_call_index": index,
                    "name": str(call.get("name", "") or ""),
                    "input": _bounded_exact_text(str(call.get("input", "") or ""), _MAX_EVENT_CHARS),
                    "output": _bounded_exact_text(str(call.get("output", "") or ""), _MAX_EVENT_CHARS),
                    "error": _bounded_exact_text(str(call.get("error", "") or ""), 4_000),
                }
                for index, call in enumerate(calls)
            ],
        },
    }


def _inspect_artifact(case: CaseAnalysisInput, request: Mapping[str, Any]) -> dict[str, Any]:
    query = str(request.get("query", "") or "")
    terms = _query_terms(query)
    case_dir = Path(case.result_path).parent.resolve()
    sources: list[tuple[str, str]] = [
        ("case.evaluation_metadata", json.dumps(case.evaluation_metadata, ensure_ascii=False, indent=2)),
        ("case.result", _read_text(Path(case.result_path), _MAX_ARTIFACT_FILE_CHARS)),
    ]
    artifacts_dir = case_dir / "artifacts"
    if artifacts_dir.is_dir():
        files = [
            path
            for path in sorted(artifacts_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in _TEXT_ARTIFACT_SUFFIXES
        ][:_MAX_ARTIFACT_FILES]
        for path in files:
            resolved = path.resolve()
            if not resolved.is_relative_to(case_dir):
                continue
            sources.append(
                (f"artifacts/{path.relative_to(artifacts_dir).as_posix()}", _read_text(path, _MAX_ARTIFACT_FILE_CHARS))
            )
        structured_files = [
            path
            for path in sorted(artifacts_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in {".docx", ".pdf", ".pptx", ".xlsx"}
        ][:_MAX_ARTIFACT_FILES]
        for path in structured_files:
            resolved = path.resolve()
            if not resolved.is_relative_to(case_dir):
                continue
            text = _structured_artifact_text(path)
            if text:
                sources.append((f"artifacts/{path.relative_to(artifacts_dir).as_posix()}", text))

    matches: list[tuple[int, str, str]] = []
    for source, text in sources:
        lowered = text.casefold()
        matched = [term for term in terms if term in lowered]
        if matched:
            matches.append((len(set(matched)), source, text))
    selected = sorted(matches, key=lambda item: (-item[0], item[1]))[:_MAX_SEARCH_RESULTS]
    return {
        "availability": "available" if selected else "not_found",
        "query": query,
        "matches": [
            {
                "source": source,
                "exact_spans": _exact_match_spans(text, terms, max_spans=3),
            }
            for _, source, text in selected
        ],
    }


def _repository_dir(evidence_root: str | Path | None) -> Path | None:
    if evidence_root is None:
        return None
    root = Path(evidence_root).expanduser().resolve()
    repository = (root / "repository").resolve()
    return repository if repository.is_dir() and repository.is_relative_to(root) else None


def _search_repository(repository_dir: Path | None, request: Mapping[str, Any]) -> dict[str, Any]:
    if repository_dir is None:
        return {"availability": "not_available", "reason": "repository_snapshot_not_available"}
    query = str(request.get("query", "") or "")
    terms = _query_terms(query)
    ranked: list[tuple[int, str, str]] = []
    for path in _repository_text_files(repository_dir):
        text = _read_text(path, _MAX_ARTIFACT_FILE_CHARS)
        lowered = text.casefold()
        matched = [term for term in terms if term in lowered]
        if matched:
            relative = path.relative_to(repository_dir).as_posix()
            ranked.append((len(set(matched)), relative, text))
    limit = min(_MAX_SEARCH_RESULTS, max(1, _positive_int(request.get("max_results"), default=3)))
    selected = sorted(ranked, key=lambda item: (-item[0], item[1]))[:limit]
    return {
        "availability": "available" if selected else "not_found",
        "query": query,
        "matched_file_count": len(ranked),
        "files": [
            {"relative_path": relative, "exact_spans": _exact_match_spans(text, terms, max_spans=3)}
            for _, relative, text in selected
        ],
    }


def _read_repository_file(
    repository_dir: Path | None,
    request: Mapping[str, Any],
    *,
    discovered_paths: set[str],
) -> dict[str, Any]:
    if repository_dir is None:
        return {"availability": "not_available", "reason": "repository_snapshot_not_available"}
    relative_path = _safe_relative_path(request.get("relative_path"))
    if not relative_path:
        return {"availability": "invalid", "reason": "invalid_relative_path"}
    if relative_path not in discovered_paths:
        return {
            "availability": "invalid",
            "reason": "relative_path_not_returned_by_prior_search_repository",
            "relative_path": relative_path,
        }
    path = (repository_dir / relative_path).resolve()
    if not path.is_relative_to(repository_dir) or not path.is_file():
        return {"availability": "not_found", "relative_path": relative_path}
    if path.suffix.casefold() not in _TEXT_ARTIFACT_SUFFIXES | {
        ".c",
        ".cc",
        ".cpp",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".py",
        ".rs",
        ".sh",
        ".ts",
        ".tsx",
    }:
        return {"availability": "unsupported", "relative_path": relative_path}
    return {
        "availability": "available",
        "relative_path": relative_path,
        "content": _bounded_exact_text(_read_text(path, _MAX_ARTIFACT_FILE_CHARS), _MAX_EVENT_CHARS),
    }


def _repository_text_files(repository_dir: Path) -> list[Path]:
    suffixes = _TEXT_ARTIFACT_SUFFIXES | {
        ".c",
        ".cc",
        ".cpp",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".py",
        ".rs",
        ".sh",
        ".ts",
        ".tsx",
    }
    return [
        path
        for path in sorted(repository_dir.rglob("*"))
        if path.is_file() and path.suffix.casefold() in suffixes and path.resolve().is_relative_to(repository_dir)
    ][:_MAX_REPOSITORY_FILES]


def _safe_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return ""
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _normalized_semantics(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.casefold()))


def _structured_artifact_text(path: Path) -> str:
    try:
        suffix = path.suffix.casefold()
        if suffix == ".xlsx":
            return _xlsx_text(path)
        if suffix == ".docx":
            return _docx_text(path)
        if suffix == ".pdf":
            return _pdf_text(path)
        if suffix == ".pptx":
            return _pptx_text(path)
    except Exception:  # malformed optional artifacts are unavailable evidence
        return ""
    return ""


def _xlsx_text(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    lines: list[str] = []
    total_chars = 0
    cell_count = 0
    try:
        for worksheet in workbook.worksheets:
            lines.append(f"[sheet:{worksheet.title}]")
            for row in worksheet.iter_rows(
                max_row=min(worksheet.max_row, 2_000),
                max_col=min(worksheet.max_column, 200),
            ):
                for cell in row:
                    if cell.value is not None:
                        line = f"{cell.coordinate}={cell.value}"
                        lines.append(line)
                        total_chars += len(line)
                        cell_count += 1
                        if total_chars >= _MAX_ARTIFACT_FILE_CHARS or cell_count >= _MAX_STRUCTURED_CELLS:
                            return "\n".join(lines)
    finally:
        workbook.close()
    return "\n".join(lines)


def _docx_text(path: Path) -> str:
    from docx import Document

    document = Document(path)
    lines: list[str] = []
    total_chars = 0
    for paragraph in document.paragraphs:
        if not paragraph.text:
            continue
        lines.append(paragraph.text)
        total_chars += len(paragraph.text)
        if total_chars >= _MAX_ARTIFACT_FILE_CHARS:
            return "\n".join(lines)[:_MAX_ARTIFACT_FILE_CHARS]
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            values = [cell.text for cell in row.cells]
            line = f"[table:{table_index}:row:{row_index}] " + " | ".join(values)
            lines.append(line)
            total_chars += len(line)
            if total_chars >= _MAX_ARTIFACT_FILE_CHARS:
                return "\n".join(lines)[:_MAX_ARTIFACT_FILE_CHARS]
    return "\n".join(lines)[:_MAX_ARTIFACT_FILE_CHARS]


def _pdf_text(path: Path) -> str:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(path) as document:
        for page_index, page in enumerate(document.pages[:_MAX_STRUCTURED_PAGES], start=1):
            lines.append(f"[page:{page_index}]")
            lines.append(page.extract_text() or "")
            if sum(len(item) for item in lines) >= _MAX_ARTIFACT_FILE_CHARS:
                break
    return "\n".join(lines)[:_MAX_ARTIFACT_FILE_CHARS]


def _pptx_text(path: Path) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))[
            :_MAX_STRUCTURED_PAGES
        ]
        for slide_name in slide_names:
            root = ElementTree.fromstring(archive.read(slide_name))
            texts = [element.text or "" for element in root.iter() if element.tag.endswith("}t")]
            lines.append(f"[{slide_name}] " + " ".join(texts))
    return "\n".join(lines)[:_MAX_ARTIFACT_FILE_CHARS]


def _compare_runs(
    prior_candidate_feedback: Mapping[str, Any] | None,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if not prior_candidate_feedback:
        return {"availability": "not_available", "reason": "no_paired_candidate_feedback"}
    rendered = json.dumps(prior_candidate_feedback, ensure_ascii=False, indent=2)
    query = str(request.get("query", "") or "")
    terms = _query_terms(query) if query else []
    return {
        "availability": "available",
        "query": query,
        "paired_feedback": (
            {"exact_spans": _exact_match_spans(rendered, terms, max_spans=5)}
            if terms
            else _bounded_exact_text(rendered, _MAX_EVENT_CHARS)
        ),
    }


def _search_event_view(event: Mapping[str, Any], terms: Sequence[str]) -> dict[str, Any]:
    return {
        "trace_id": event.get("trace_id"),
        "message_index": event.get("message_index"),
        "step_pointer": event.get("step_pointer"),
        "role": event.get("role"),
        "content_spans": _exact_match_spans(str(event.get("content", "") or ""), terms, max_spans=2),
        "tool_calls": [
            {
                "tool_call_index": index,
                "name": str(call.get("name", "") or ""),
                "input_spans": _exact_match_spans(str(call.get("input", "") or ""), terms, max_spans=2),
                "output_spans": _exact_match_spans(str(call.get("output", "") or ""), terms, max_spans=3),
                "error_spans": _exact_match_spans(str(call.get("error", "") or ""), terms, max_spans=2),
            }
            for index, call in enumerate(event.get("tool_calls", []))
        ],
    }


def _exact_match_spans(
    text: str,
    terms: Sequence[str],
    *,
    max_spans: int,
    span_chars: int = 2_000,
) -> list[dict[str, Any]]:
    if not text or not terms:
        return []
    lowered = text.casefold()
    ranked: list[tuple[int, str]] = []
    for term in terms:
        position = lowered.find(term)
        if position >= 0:
            ranked.append((position, term))
    spans: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for position, term in sorted(ranked)[: max_spans * 2]:
        start = max(0, position - span_chars // 3)
        end = min(len(text), start + span_chars)
        start = max(0, end - span_chars)
        if any(start < old_end and end > old_start for old_start, old_end in occupied):
            continue
        occupied.append((start, end))
        spans.append(
            {
                "source_char_start": start,
                "source_char_end": end,
                "source_char_count": len(text),
                "matched_term": term,
                "text": text[start:end],
                "window_complete": start == 0 and end == len(text),
                "omission_origin": "controller_search_window" if start or end < len(text) else "none",
            }
        )
        if len(spans) >= max_spans:
            break
    return spans


def _bounded_exact_text(text: str, limit: int) -> dict[str, Any]:
    if len(text) <= limit:
        return {
            "text": text,
            "source_char_count": len(text),
            "complete": True,
            "omission_origin": "none",
        }
    return {
        "text": text[:limit],
        "source_char_count": len(text),
        "complete": False,
        "omission_origin": "controller_bound",
        "omitted_source_chars": len(text) - limit,
    }


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TERM_PATTERN.findall(query):
        normalized = match.casefold()
        if normalized in _STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= 24:
            break
    return terms


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path, limit: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


__all__ = [
    "execute_causal_investigation",
    "normalize_causal_investigation",
]
