# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Input artifact loader for member optimization.

Loads and resolves upstream artifacts per feat_009 Section 4.
Handles bounded evidence excerpting, no-op detection, and
candidate role resolution from harness refs and Team Skill bindings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.member_optimizer.schema import MemberRoleCandidate
from openjiuwen.rsi.harness_rsi.schema import TeamIssue


@dataclass
class EvalRef:
    """Parsed content of eval_ref.yaml."""

    eval_id: str
    team_name: str
    team_skill_ref_path: str
    harness_refs_path: str
    eval_dir: Path
    case_results_dir: Path
    case_traces_dir: Path
    summary_path: Path | None
    cases: list[dict[str, Any]]

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_path: Path | None = None) -> "EvalRef":
        eval_dir = Path(data.get("eval_dir", "."))
        if base_path is not None and not eval_dir.is_absolute():
            eval_dir = base_path / eval_dir

        cases = data.get("cases", [])
        results_dir = Path(data.get("case_results_dir", "results"))
        traces_dir = Path(data.get("case_traces_dir", "traces"))
        if base_path is not None and not results_dir.is_absolute():
            results_dir = base_path / results_dir
        if base_path is not None and not traces_dir.is_absolute():
            traces_dir = base_path / traces_dir

        summary_path = data.get("summary_path")
        if summary_path:
            resolved_summary_path = Path(summary_path)
            if base_path is not None and not resolved_summary_path.is_absolute():
                resolved_summary_path = base_path / resolved_summary_path
            summary_path = resolved_summary_path

        return cls(
            eval_id=data.get("eval_id", ""),
            team_name=data.get("team_name", ""),
            team_skill_ref_path=data.get("team_skill_ref_path", ""),
            harness_refs_path=data.get("harness_refs_path", ""),
            eval_dir=eval_dir,
            case_results_dir=results_dir,
            case_traces_dir=traces_dir,
            summary_path=Path(summary_path) if summary_path else None,
            cases=cases,
        )


@dataclass
class AnalysisRef:
    """Parsed content of analysis_ref.yaml."""

    issues: list[dict[str, Any]]
    issues_path: str | None


@dataclass
class BoundedEvidenceBundle:
    """Bounded evidence for one TeamIssue used in attribution."""

    issue: dict[str, Any]
    case_results: list[dict[str, Any]]
    case_traces: list[dict[str, Any]]
    candidate_roles: list[MemberRoleCandidate]


def load_eval_ref(eval_ref_path: str | Path) -> EvalRef:
    """Load and parse eval_ref.yaml.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if required fields are missing.
    """
    path = Path(eval_ref_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"eval_ref not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    eval_dir = Path(data.get("eval_dir", "."))
    if not eval_dir.is_absolute():
        eval_dir = path.parent / eval_dir

    return EvalRef.from_dict(data, base_path=path.parent)


def load_analysis_ref(analysis_result_path: str | Path) -> AnalysisRef:
    """Load and parse analysis_ref.yaml.

    Per spec Section 2.6.2, issues are resolved in this order:
    1. inline `analysis_ref.issues` list (if non-empty)
    2. `analysis_ref.issues_path` file (if set)
    3. empty list

    Returns an AnalysisRef with resolved issues (not TeamIssue dataclasses;
    caller converts to TeamIssue).
    """
    path = Path(analysis_result_path).expanduser().resolve()
    if not path.is_file():
        return AnalysisRef(issues=[], issues_path=None)

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    inline_issues = data.get("issues", [])
    issues_path_str = data.get("issues_path")

    if isinstance(inline_issues, list) and inline_issues:
        return AnalysisRef(issues=inline_issues, issues_path=issues_path_str)

    if issues_path_str:
        issues_path = Path(str(issues_path_str)).expanduser()
        issues_file = issues_path if issues_path.is_absolute() else path.parent / issues_path
        if issues_file.is_file():
            with open(issues_file, encoding="utf-8") as f:
                issues_data = yaml.safe_load(f) or {}
            resolved_issues = issues_data.get("issues", [])
            if isinstance(resolved_issues, list) and resolved_issues:
                return AnalysisRef(issues=resolved_issues, issues_path=str(issues_file))

    return AnalysisRef(issues=[], issues_path=None)


def resolve_team_issues(analysis_ref: AnalysisRef) -> list[TeamIssue]:
    """Convert raw issue dicts into TeamIssue dataclasses."""
    issues = []
    for item in analysis_ref.issues:
        if not isinstance(item, dict):
            continue
        optimization_target = str(item.get("optimization_target", "") or "").strip().lower().replace("-", "_")
        if optimization_target != "member_harness":
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        for key in ("affected_component", "component", "signals_method"):
            if key in item and key not in metadata:
                metadata[key] = item[key]
        issues.append(
            TeamIssue(
                issue_id=item.get("issue_id", ""),
                category=item.get("category", ""),
                severity=item.get("severity", ""),
                summary=item.get("summary", ""),
                affected_cases=item.get("affected_cases", []),
                evidence=item.get("evidence", []),
                suspected_team_scope=item.get("suspected_team_scope", ""),
                optimization_target=optimization_target,
                target_members=item.get("target_members", []),
                recommendation=item.get("recommendation", ""),
                metadata=metadata,
            )
        )
    return issues


def _normalize_harness_refs_path(harness_refs_path: str | Path | None) -> Path | None:
    if not harness_refs_path:
        return None
    p = Path(harness_refs_path).expanduser().resolve()
    return p if p.exists() else None


def resolve_candidate_roles(
    harness_refs_path: str,
    eval_ref: EvalRef,
    team_skill_ref_path: str | None = None,
) -> list[MemberRoleCandidate]:
    """Resolve candidate roles from harness refs and optional Team Skill spec.

    Resolution order per spec Section 2.6.3:
    1. direct `harness_refs_path` argument
    2. fallback `eval_ref.harness_refs_path`
    3. empty list (caller decides no-op or failure)
    """
    candidates: list[MemberRoleCandidate] = []

    refs_path = _normalize_harness_refs_path(harness_refs_path)
    if refs_path is None:
        refs_path = _normalize_harness_refs_path(eval_ref.harness_refs_path)

    if refs_path is None:
        return candidates

    if refs_path.is_file():
        candidates = _parse_harness_refs_file(refs_path)
    elif refs_path.is_dir():
        candidates = _parse_harness_refs_dir(refs_path)

    if not candidates and team_skill_ref_path:
        candidates = _supplement_from_team_skill(team_skill_ref_path, eval_ref.eval_dir)

    return candidates


def _resolve_harness_ref_path(ref: Any, refs_path: Path) -> str:
    """Resolve a harness ref relative to the refs file location."""
    if not isinstance(ref, str) or not ref.strip():
        return ""
    path = Path(ref).expanduser()
    if path.is_absolute():
        return str(path)
    return str((refs_path.parent / path).resolve())


def _parse_harness_refs_file(refs_path: Path) -> list[MemberRoleCandidate]:
    """Parse harness_refs.yaml / .json file into MemberRoleCandidates."""
    with open(refs_path, encoding="utf-8") as f:
        if refs_path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(f) or {}
        else:
            data = json.load(f)

    candidates: list[MemberRoleCandidate] = []

    if "roles" in data and isinstance(data["roles"], list):
        for role_entry in data["roles"]:
            if not isinstance(role_entry, dict):
                continue
            candidates.append(
                MemberRoleCandidate(
                    role=role_entry.get("role", ""),
                    member_name=role_entry.get("member_name", ""),
                    harness_ref_path=_resolve_harness_ref_path(
                        role_entry.get("harness_ref_path", ""),
                        refs_path,
                    ),
                    description=role_entry.get("description", ""),
                    metadata=role_entry.get("metadata", {}),
                )
            )
        return candidates

    harness_refs = data.get("harness_refs", {})
    if isinstance(harness_refs, dict):
        for role, ref in harness_refs.items():
            if not isinstance(ref, str):
                continue
            candidates.append(
                MemberRoleCandidate(
                    role=role,
                    member_name=role,
                    harness_ref_path=_resolve_harness_ref_path(ref, refs_path),
                    description="",
                    metadata={"source": "harness_refs"},
                )
            )
        return candidates

    for role, ref in data.items():
        if isinstance(ref, str) and role not in ("version", "source"):
            candidates.append(
                MemberRoleCandidate(
                    role=role,
                    member_name=role,
                    harness_ref_path=_resolve_harness_ref_path(ref, refs_path),
                    description="",
                    metadata={"source": "harness_refs"},
                )
            )
    return candidates


def _parse_harness_refs_dir(refs_path: Path) -> list[MemberRoleCandidate]:
    """Parse a harness refs directory: subdirectory names become roles."""
    candidates: list[MemberRoleCandidate] = []
    for entry in sorted(refs_path.iterdir()):
        if entry.is_dir():
            candidates.append(
                MemberRoleCandidate(
                    role=entry.name,
                    member_name=entry.name,
                    harness_ref_path=str(entry),
                    description="",
                    metadata={"source": "directory"},
                )
            )
    return candidates


def _supplement_from_team_skill(
    team_skill_ref_path: str,
    base_path: Path,
) -> list[MemberRoleCandidate]:
    """Supplement candidate roles from TeamAgentSpec bindings."""
    candidates: list[MemberRoleCandidate] = []
    path = base_path / team_skill_ref_path if not Path(team_skill_ref_path).is_absolute() else Path(team_skill_ref_path)

    if not path.is_file():
        return candidates

    with open(path, encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(f) or {}
        else:
            data = json.load(f)

    leader = data.get("leader", {})
    member_name = leader.get("member_name", "")
    if member_name:
        candidates.append(
            MemberRoleCandidate(
                role=member_name,
                member_name=member_name,
                harness_ref_path="",
                description="Team leader",
                metadata={"source": "team_skill_binding"},
            )
        )

    for member in data.get("predefined_members", []):
        mname = member.get("member_name", "")
        if mname:
            candidates.append(
                MemberRoleCandidate(
                    role=mname,
                    member_name=mname,
                    harness_ref_path=member.get("harness_ref_path", ""),
                    description=member.get("description", ""),
                    metadata={"source": "team_skill_binding"},
                )
            )

    return candidates


def _excerpt(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, adding ellipsis if truncated."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def load_case_evidence(
    eval_ref: EvalRef,
    case_ids: list[str],
    max_cases: int = 3,
    max_trace_chars: int = 4000,
    max_result_chars: int = 2000,
) -> dict[str, dict[str, Any]]:
    """Load bounded case result and trace evidence for the given case IDs.

    Returns a dict mapping case_id -> {"result": ..., "trace": ...}.
    Results and trace content are bounded to max_chars.
    """
    if not case_ids:
        return {}

    evidence: dict[str, dict[str, Any]] = {}
    cases_to_load = case_ids[:max_cases]

    for case_id in cases_to_load:
        case_entry = next(
            (c for c in eval_ref.cases if c.get("case_id") == case_id),
            None,
        )

        result_data: dict[str, Any] = {}
        trace_data: dict[str, Any] = {}

        if case_entry:
            result_path = case_entry.get("result_path")
            trace_path = case_entry.get("trace_path")
            if result_path:
                p = _resolve_case_artifact_path(result_path, eval_ref.eval_dir)
                if p.is_file():
                    result_data = _load_json_or_yaml(p)
            if trace_path:
                p = _resolve_case_artifact_path(trace_path, eval_ref.eval_dir)
                if p.is_file():
                    trace_data = _load_json_or_yaml(p)

        result_data.setdefault("case_id", case_id)
        trace_data.setdefault("case_id", case_id)

        result_data["response"] = _excerpt(_extract_response_text(result_data), max_result_chars)
        result_data["result"] = _excerpt(_extract_result_text(result_data), max_result_chars)
        trace_data["response"] = _excerpt(_extract_response_text(trace_data), max_trace_chars)
        behavior_trace = trace_data.get("behavior_trace")
        if isinstance(behavior_trace, dict):
            trace_data["behavior_trace"] = _bound_behavior_trace(behavior_trace)

        evidence[case_id] = {"result": result_data, "trace": trace_data}

    return evidence


def _resolve_case_artifact_path(value: str | Path, eval_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return eval_dir / path


def _extract_response_text(data: dict[str, Any]) -> str:
    """Read a response summary from formal result/trace fields without mutating artifacts."""
    for key in ("response", "output", "answer"):
        value = data.get(key)
        if value:
            return _stringify_evidence_value(value)

    result = data.get("result")
    if isinstance(result, dict):
        for key in ("output", "response", "answer", "result"):
            value = result.get(key)
            if value:
                return _stringify_evidence_value(value)
    elif result:
        return _stringify_evidence_value(result)
    return ""


def _extract_result_text(data: dict[str, Any]) -> str:
    result = data.get("result")
    if result:
        return _stringify_evidence_value(result)
    return _extract_response_text(data)


def _stringify_evidence_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    """Load a file as YAML or JSON."""
    with open(path, encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(f) or {}
        return json.load(f)


def _bound_behavior_trace(behavior_trace: dict[str, Any]) -> dict[str, Any]:
    command_log = behavior_trace.get("command_log")
    workspace_changes = behavior_trace.get("workspace_changes")
    trajectory_events_path = behavior_trace.get("trajectory_events_path")
    trajectory_window_summary = behavior_trace.get("trajectory_window_summary")
    normalized_trace_path = behavior_trace.get("normalized_trace_path")
    normalized_trace_summary = behavior_trace.get("normalized_trace_summary")
    bounded: dict[str, Any] = {}
    if isinstance(command_log, list):
        bounded["command_log"] = [
            {
                "command": _excerpt(str(item.get("command", "")), 600),
                "cwd": str(item.get("cwd", "")),
                "exit_code": item.get("exit_code"),
                "stdout_excerpt": _excerpt(str(item.get("stdout_excerpt", "")), 1200),
                "stderr_excerpt": _excerpt(str(item.get("stderr_excerpt", "")), 1200),
                "timeout_sec": item.get("timeout_sec"),
                "background": bool(item.get("background", False)),
            }
            for item in command_log[:40]
            if isinstance(item, dict)
        ]
    if isinstance(workspace_changes, dict):
        bounded["workspace_changes"] = {
            "added": _string_list(workspace_changes.get("added"), 80),
            "modified": _string_list(workspace_changes.get("modified"), 80),
            "removed": _string_list(workspace_changes.get("removed"), 80),
        }
    if isinstance(trajectory_events_path, str):
        bounded["trajectory_events_path"] = trajectory_events_path
    if isinstance(trajectory_window_summary, dict):
        bounded["trajectory_window_summary"] = _bound_trajectory_window_summary(trajectory_window_summary)
    if isinstance(normalized_trace_path, str):
        bounded["normalized_trace_path"] = normalized_trace_path
    if isinstance(normalized_trace_summary, dict):
        bounded["normalized_trace_summary"] = _bound_normalized_trace_summary(normalized_trace_summary)
    return bounded


def _bound_trajectory_window_summary(summary: dict[str, Any]) -> dict[str, Any]:
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


def _bound_normalized_trace_summary(summary: dict[str, Any]) -> dict[str, Any]:
    traces = summary.get("traces")
    bounded_traces: list[dict[str, Any]] = []
    if isinstance(traces, list):
        for trace in traces[:4]:
            if not isinstance(trace, dict):
                continue
            messages = trace.get("messages")
            bounded_messages: list[dict[str, Any]] = []
            if isinstance(messages, list):
                for message in messages[:30]:
                    if not isinstance(message, dict):
                        continue
                    bounded_messages.append(
                        {
                            "message_index": message.get("message_index"),
                            "role": _excerpt(str(message.get("role", "")), 80),
                            "step_pointer": _excerpt(str(message.get("step_pointer", "")), 120),
                            "tool_calls": _bound_normalized_tool_calls(message.get("tool_calls")),
                        }
                    )
            bounded_traces.append(
                {
                    "trace_id": _excerpt(str(trace.get("trace_id", "")), 240),
                    "member_role": _excerpt(str(trace.get("member_role", "")), 160),
                    "messages": bounded_messages,
                }
            )
    return {
        "case_id": _excerpt(str(summary.get("case_id", "")), 160),
        "traces": bounded_traces,
    }


def _bound_normalized_tool_calls(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        calls.append(
            {
                "name": _excerpt(str(item.get("name", "")), 120),
                "input": _excerpt(str(item.get("input", "")), 800),
                "output": _excerpt(str(item.get("output", "")), 800),
                "error": _excerpt(str(item.get("error", "")), 800),
                "step_pointer": _excerpt(str(item.get("step_pointer", "")), 120),
            }
        )
    return calls


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


__all__ = [
    "AnalysisRef",
    "BoundedEvidenceBundle",
    "EvalRef",
    "load_analysis_ref",
    "load_case_evidence",
    "load_eval_ref",
    "resolve_candidate_roles",
    "resolve_team_issues",
]
