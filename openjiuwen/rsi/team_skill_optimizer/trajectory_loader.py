# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline EvaluationCaseTraceRef trajectory loading."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_evolving.trajectory import (
    LegacyTrajectory,
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
    to_legacy_trajectory,
    trajectory_from_legacy,
)
from openjiuwen.rsi.team_skill_optimizer.signals import issue_ids

MAX_TRAJECTORY_CASES = 3
MAX_TEXT_CHARS = 2000


def load_offline_trajectory(eval_ref_path: str, issues: list[dict[str, Any]]) -> Trajectory | None:
    eval_ref_path_obj = Path(eval_ref_path).expanduser().resolve() if eval_ref_path else Path()
    eval_ref = _load_yaml_mapping(eval_ref_path_obj)
    if not eval_ref:
        return None
    trace_paths = _select_trace_paths(
        eval_ref,
        issues,
        eval_ref_dir=eval_ref_path_obj.parent,
    )
    if not trace_paths:
        return None

    steps: list[TrajectoryStep] = []
    loaded_paths: list[str] = []
    for trace_path in trace_paths[:MAX_TRAJECTORY_CASES]:
        payload = _load_json_mapping(trace_path)
        if not payload:
            continue
        loaded_paths.append(str(trace_path))
        steps.extend(_trajectory_steps_from_trace(payload))

    if not steps:
        return None
    return trajectory_from_legacy(
        LegacyTrajectory(
            execution_id=f"auto_team_skill_optimization_{uuid.uuid4().hex[:8]}",
            session_id=str(eval_ref.get("eval_id") or "auto_team_skill_optimization"),
            source="offline",
            steps=steps,
            meta={
                "trace_paths": loaded_paths,
                "source_eval_ref_path": eval_ref_path,
                "issue_ids": issue_ids(issues),
            },
        )
    )


def trajectory_trace_paths(trajectory: Trajectory | None) -> list[str]:
    if trajectory is None:
        return []
    trace_paths = to_legacy_trajectory(trajectory).meta.get("trace_paths")
    if isinstance(trace_paths, list):
        return [str(item) for item in trace_paths]
    return []


def _select_trace_paths(
    eval_ref: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    eval_ref_dir: Path,
) -> list[Path]:
    affected_case_ids = _affected_case_ids(issues)
    eval_dir = _resolve_ref_path(eval_ref.get("eval_dir") or ".", base_dir=eval_ref_dir)
    case_results_dir = Path(str(eval_ref.get("case_results_dir") or "")).expanduser()
    if not case_results_dir.is_absolute() and eval_dir:
        case_results_dir = eval_dir / case_results_dir

    paths: list[Path] = []
    cases = eval_ref.get("cases")
    if isinstance(cases, list):
        for case in cases:
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("case_id") or "")
            if affected_case_ids and case_id not in affected_case_ids:
                continue
            trace_path = _resolve_trace_path(
                case,
                case_results_dir=case_results_dir,
                eval_ref_dir=eval_ref_dir,
            )
            if trace_path is not None:
                paths.append(trace_path)

    if not paths and case_results_dir.is_dir():
        paths.extend(sorted(case_results_dir.glob("*/trace.json")))
    return _dedupe_paths(paths)


def _affected_case_ids(issues: list[dict[str, Any]]) -> set[str]:
    case_ids: set[str] = set()
    for issue in issues:
        affected_cases = issue.get("affected_cases")
        if isinstance(affected_cases, list):
            case_ids.update(str(case_id) for case_id in affected_cases if case_id)
        evidence = issue.get("evidence")
        items = evidence if isinstance(evidence, list) else [evidence]
        for item in items:
            if isinstance(item, dict) and item.get("case_id"):
                case_ids.add(str(item.get("case_id")))
    return case_ids


def _resolve_trace_path(
    case: dict[str, Any],
    *,
    case_results_dir: Path,
    eval_ref_dir: Path,
) -> Path | None:
    raw_trace_path = case.get("trace_path")
    if raw_trace_path:
        trace_path = Path(str(raw_trace_path)).expanduser()
        if trace_path.is_absolute():
            return trace_path
        candidates = [
            eval_ref_dir / trace_path,
            case_results_dir / trace_path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]
    case_id = str(case.get("case_id") or "")
    if case_id:
        return case_results_dir / case_id / "trace.json"
    return None


def _resolve_ref_path(value: Any, *, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else base_dir / path


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.expanduser().resolve())
        if resolved in seen or not Path(resolved).is_file():
            continue
        seen.add(resolved)
        deduped.append(Path(resolved))
    return deduped


def _trajectory_steps_from_trace(payload: dict[str, Any]) -> list[TrajectoryStep]:
    steps: list[TrajectoryStep] = []
    case_id = str(payload.get("case_id") or "")
    input_text = _to_text(payload.get("input"))
    response_text = _to_text(payload.get("response"))
    if input_text or response_text:
        steps.append(
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="auto_coordinating_harness_offline_trace",
                    messages=[{"role": "user", "content": _excerpt(input_text, MAX_TEXT_CHARS)}],
                    response={"role": "assistant", "content": _excerpt(response_text, MAX_TEXT_CHARS)},
                    meta={"case_id": case_id},
                ),
                meta={"case_id": case_id, "source": "trace.json"},
            )
        )

    behavior_trace = payload.get("behavior_trace")
    if not isinstance(behavior_trace, dict):
        return steps

    command_log = behavior_trace.get("command_log")
    if isinstance(command_log, list):
        for item in command_log[:20]:
            if not isinstance(item, dict):
                continue
            command = _excerpt(_to_text(item.get("command")), 500)
            stdout = _excerpt(_to_text(item.get("stdout_excerpt")), 1000)
            stderr = _excerpt(_to_text(item.get("stderr_excerpt")), 1000)
            steps.append(
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="command",
                        call_args=command,
                        call_result=f"exit={item.get('exit_code')} stdout={stdout} stderr={stderr}",
                    ),
                    meta={"case_id": case_id, "source": "behavior_trace.command_log"},
                )
            )

    window_summary = behavior_trace.get("trajectory_window_summary")
    recent_events = window_summary.get("recent_events") if isinstance(window_summary, dict) else None
    if isinstance(recent_events, list):
        for item in recent_events[:30]:
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("event_type") or "trajectory_event")
            steps.append(
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name=event_type,
                        call_args={"event_index": item.get("event_index")},
                        call_result=_excerpt(_to_text(item.get("summary")), 1200),
                    ),
                    meta={"case_id": case_id, "source": "behavior_trace.trajectory_window_summary"},
                )
            )
    return steps


def _load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    if not path:
        return {}
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        return {}
    with open(file_path, "r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return payload if isinstance(payload, dict) else {}


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _excerpt(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "... [truncated]"
