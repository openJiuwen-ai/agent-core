# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Case execution for one evaluation case."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home, team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.evaluator.case_backend import (
    CaseExecutionBackend,
    CaseExecutionResult,
    _artifact_files_from_case,
    _is_runtime_workspace_metadata,
)
from openjiuwen.rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.evaluator.judger import EvaluationJudger, JudgeResult
from openjiuwen.rsi.evaluator.trajectory_paths import (
    ROLE_TRAJECTORY_DIR_NAME,
    TRAJECTORY_EVENTS_FILE_NAME,
)
from openjiuwen.rsi.schema import EvaluationCaseTraceRef

_MAX_ROLE_TRAJECTORY_FILE_BYTES = 2_000_000
_ROLE_TRAJECTORY_TAIL_BYTES = 64_000
_MAX_ROLE_TRACE_MESSAGES = 256
_CANONICAL_DELIVERABLE_FILES = ("index.html", "styles.css", "content_brief.md")
_PROOF_ARTIFACT_NAME_HINTS = (
    "check",
    "evidence",
    "report",
    "result",
    "summary",
    "validation",
    "validator",
    "verdict",
)
_PROOF_ARTIFACT_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}


class CaseRunner:
    """Run dataset cases through ``openjiuwen.agent_teams``."""

    def __init__(
        self,
        backend: CaseExecutionBackend | None = None,
        judger: EvaluationJudger | None = None,
    ) -> None:
        if backend is None:
            raise ValueError("CaseRunner requires an explicit backend; use build_backend() to construct one")
        self.backend: CaseExecutionBackend = backend
        self.judger: EvaluationJudger | None = judger

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> EvaluationCaseTraceRef:
        """Run one case and persist final trace/result artifacts.

        Execution order:
        1. ``configure_openjiuwen_home`` redirects the global home to a case-scoped
           runtime home. On Windows this uses a short temp root to avoid MAX_PATH
           failures while preserving per-case isolation. Other platforms keep
           ``case_dir`` as the runtime home.
           that ``team_home`` and ``stable_base`` workspace derivation resolve inside the
           case runtime boundary instead of ``~/.openjiuwen/.agent_teams``.
        2. ``backend.execute`` runs the team; workspaces auto-derive to
           ``<case_dir>/.agent_teams/{team}/workspaces/{member}_workspace/``.
        3. ``_harvest_artifacts`` copies ``team-workspace/artifacts/`` into
           ``case_dir/artifacts/`` (``EvalTeamRail`` pre-harvests if ``clean_team`` fires
           first; this call is the fallback for normal/error exit without ``clean_team``).
        4. ``judger.judge`` reads stable ``case_dir/artifacts/`` and ``case_dir/tr/``.
        5. ``trace.json`` and ``result.json`` are written with final evaluation fields.
        6. ``backend.cleanup``, ``_cleanup_scratch``, and ``reset_openjiuwen_home`` run in ``finally``.
        """
        case_id = _case_id(case)
        session_id = f"eval_{case_id}_{uuid4().hex}"
        case_dir = Path(output_dir).expanduser().resolve()
        _prepare_case_dir(case_dir)
        # Redirect global home so team_home() and stable_base path derivation
        # resolve under a case-scoped runtime home for this case only.
        runtime_home_dir = _runtime_home_dir(case_dir, session_id)
        runtime_home_dir.mkdir(parents=True, exist_ok=True)
        configure_openjiuwen_home(runtime_home_dir)
        result_path = case_dir / "result.json"
        trace_path = case_dir / "trace.json"
        started_at = datetime.now(UTC).astimezone()
        resolved_team_spec: TeamAgentSpec | None = None
        resolved_team_name = ""
        artifact_refs = {"harvested": [], "missing": []}
        response: Any = None
        body_error: BaseException | None = None

        try:
            execution_result = await self.backend.execute(
                case=case,
                output_dir=str(case_dir),
                session_id=session_id,
                team_skill_ref_path=team_skill_ref_path,
                harness_refs=harness_refs or {},
            )
            resolved_team_spec = execution_result.team_spec
            resolved_team_name = resolved_team_spec.team_name if resolved_team_spec is not None else ""
            response = execution_result.response
            finished_at = datetime.now(UTC).astimezone()

            runtime_workspace_dir = _execution_workspace_dir(execution_result, case_dir)
            expected_artifact_files = _expected_artifact_files(case)
            artifact_refs = _harvest_artifacts(
                workspace_dir=(
                    runtime_workspace_dir
                    if execution_result.workspace_dir
                    else _team_workspace_dir(case_dir, resolved_team_name)
                ),
                dest_dir=case_dir / "artifacts",
                expected_files=expected_artifact_files,
            )
            if execution_result.workspace_dir and not artifact_refs["harvested"]:
                artifact_refs = _harvest_changed_workspace_files(
                    workspace_dir=runtime_workspace_dir,
                    dest_dir=case_dir / "artifacts",
                    workspace_changes=execution_result.metadata.get("workspace_changes"),
                    expected_files=expected_artifact_files,
                )

            trajectory_dir = str(case_dir / ROLE_TRAJECTORY_DIR_NAME)
            behavior_trace = _behavior_trace(execution_result.metadata)
            behavior_trace["artifact_refs"] = artifact_refs
            trajectory_events = _build_pre_judge_trajectory_events(
                response=response,
                behavior_trace=behavior_trace,
                execution_status=execution_result.execution_status,
                execution_error=execution_result.error,
            )
            trajectory_events_path = Path(trajectory_dir) / TRAJECTORY_EVENTS_FILE_NAME
            _write_jsonl(trajectory_events_path, trajectory_events)
            behavior_trace["trajectory_events_path"] = str(trajectory_events_path)
            behavior_trace["trajectory_window_summary"] = _trajectory_window_summary(
                trajectory_events,
                None,
            )
            member_identity = _execution_member_identity(
                execution_result,
                team_name=resolved_team_name,
            )
            normalized_trace_path = case_dir / "judge" / "normalized_trace.json"
            role_traces = _normalized_role_traces_from_trajectory_dir(
                case_id=case_id,
                trajectory_dir=Path(trajectory_dir),
            )
            normalized_trace = _normalized_trace_from_events(
                case_id=case_id,
                events=trajectory_events,
                judge_result=None,
                member_id=member_identity["member_id"],
                member_role=member_identity["member_role"],
                role_traces=role_traces,
            )
            _write_json(normalized_trace_path, normalized_trace)
            behavior_trace["normalized_trace_path"] = str(normalized_trace_path)
            behavior_trace["normalized_trace_summary"] = normalized_trace

            judge_result = await self._judge(
                case=case,
                execution_result=execution_result,
                output_dir=str(case_dir),
            )
            final_status = _case_result_status(
                execution_status=execution_result.execution_status,
                judge_result=judge_result,
            )

            trajectory_events = _build_trajectory_events(
                response=response,
                behavior_trace=behavior_trace,
                judge_result=judge_result,
                execution_status=execution_result.execution_status,
                execution_error=execution_result.error,
            )
            trajectory_events_path = Path(trajectory_dir) / TRAJECTORY_EVENTS_FILE_NAME
            _write_jsonl(trajectory_events_path, trajectory_events)
            behavior_trace["trajectory_window_summary"] = _trajectory_window_summary(
                trajectory_events,
                judge_result,
            )
            normalized_trace = _normalized_trace_from_events(
                case_id=case_id,
                events=trajectory_events,
                judge_result=judge_result,
                member_id=member_identity["member_id"],
                member_role=member_identity["member_role"],
                role_traces=role_traces,
            )
            _write_json(normalized_trace_path, normalized_trace)
            behavior_trace["normalized_trace_summary"] = normalized_trace
            trace_payload = {
                "case_id": case_id,
                "session_id": session_id,
                "team_name": resolved_team_name,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "status": final_status,
                "execution_status": execution_result.execution_status,
                "input": _case_inputs(case),
                "response": response,
                "error": execution_result.error,
                "workspace_dir": str(runtime_workspace_dir),
                "trajectory_dir": trajectory_dir,
                "behavior_trace": behavior_trace,
                "evaluation": {
                    "method": judge_result.method,
                    "score": judge_result.score,
                    "passed": judge_result.passed,
                    "reason": judge_result.reason,
                    "metadata": judge_result.metadata,
                },
            }
            result_payload = {
                "case_id": case_id,
                "session_id": session_id,
                "status": final_status,
                "execution_status": execution_result.execution_status,
                "score": judge_result.score,
                "evaluation": {
                    "method": judge_result.method,
                    "passed": judge_result.passed,
                    "reason": judge_result.reason,
                    "metadata": judge_result.metadata,
                },
                "result": execution_result.response,
                "error": execution_result.error,
                "workspace_dir": str(runtime_workspace_dir),
                "trace_path": str(trace_path),
                "artifacts": artifact_refs,
                "metadata": {
                    "team_name": resolved_team_name,
                    "case_path": case.get("case_path", ""),
                    "case_metadata": case.get("metadata", {}),
                    "training_signal": case.get("training_signal", {}),
                    "execution": execution_result.metadata,
                },
            }
            _write_json(trace_path, trace_payload)
            _write_json(result_path, result_payload)

            return EvaluationCaseTraceRef(
                case_id=case_id,
                case_path=str(case.get("case_path", "")),
                trace_path=str(trace_path),
                result_path=str(result_path),
                status=final_status,
                score=judge_result.score,
                metadata={
                    "session_id": session_id,
                    "team_name": resolved_team_name,
                    "execution_status": execution_result.execution_status,
                    "evaluation_method": judge_result.method,
                    "evaluation_passed": judge_result.passed,
                },
            )
        except Exception as exc:
            if isinstance(exc, EvaluationInfrastructureError):
                body_error = exc
                raise
            body_error = exc
            return _write_error_case_artifacts(
                case=case,
                case_id=case_id,
                session_id=session_id,
                team_name=resolved_team_name,
                started_at=started_at,
                finished_at=datetime.now(UTC).astimezone(),
                trace_path=trace_path,
                result_path=result_path,
                artifact_refs=artifact_refs,
                response=response,
                error=str(exc),
            )
        finally:
            try:
                if resolved_team_name:
                    await self.backend.cleanup(resolved_team_name, session_id)
            except Exception as exc:
                if body_error is None:
                    raise
                logger.warning("case runtime cleanup failed after case error: {}", exc)
            finally:
                await _cleanup_scratch(case_dir, resolved_team_spec, runtime_home_dir)
                reset_openjiuwen_home()

    async def _judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str,
    ) -> JudgeResult:
        """Return the backend judge result, configured judge result, or default score."""
        if execution_result.judge_result is not None:
            return execution_result.judge_result
        if self.judger is not None:
            return await self.judger.judge(
                case=case,
                execution_result=execution_result,
                output_dir=output_dir,
            )
        return JudgeResult(
            method="none",
            score=1.0 if execution_result.execution_status == "passed" else 0.0,
            passed=execution_result.execution_status == "passed",
            reason="no judger configured",
        )


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or f"case_{case.get('case_index', 1):03d}")


def _case_inputs(case: dict[str, Any]) -> Any:
    for key in ("input", "inputs", "task_input", "query", "prompt"):
        if key in case:
            value = case[key]
            if key == "input" and isinstance(value, dict) and set(value) == {"user_message"}:
                return value["user_message"]
            return value
    return case


def _case_result_status(
    *,
    execution_status: str,
    judge_result: JudgeResult,
) -> str:
    """Return final evaluation status, keeping execution completion separate."""
    if execution_status != "passed":
        return "error"
    return "passed" if judge_result.passed else "failed"


def _expected_artifact_files(case: dict[str, Any]) -> list[str]:
    """Return explicit final deliverable filenames when the case defines them."""
    reference = case.get("reference")
    expected = _expected_artifact_files_from_mapping(reference) if isinstance(reference, dict) else []
    if expected:
        return expected
    raw_input = _case_inputs(case)
    task_text = raw_input if isinstance(raw_input, str) else json.dumps(raw_input, ensure_ascii=False)
    expected = _artifact_files_from_case(case, task_text)
    if expected:
        return expected
    text = json.dumps(case, ensure_ascii=False).lower()
    if all(filename in text for filename in _CANONICAL_DELIVERABLE_FILES):
        return list(_CANONICAL_DELIVERABLE_FILES)
    return []


def _expected_artifact_files_from_mapping(mapping: dict[str, Any]) -> list[str]:
    for key in (
        "expected_artifacts",
        "expected_files",
        "required_files",
        "output_files",
        "deliverables",
    ):
        values = mapping.get(key)
        files = _artifact_file_list(values)
        if files:
            return files
    contract = mapping.get("artifact_contract")
    if isinstance(contract, dict):
        for key in ("final_files", "expected_files", "required_files", "deliverables"):
            files = _artifact_file_list(contract.get(key))
            if files:
                return files
    return []


def _artifact_file_list(values: Any) -> list[str]:
    result: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str):
                name = Path(value).name
            elif isinstance(value, dict):
                name = Path(str(value.get("path") or value.get("file") or "")).name
            else:
                continue
            if name and name not in result:
                result.append(name)
    return result


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, default=str)


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=False, default=str))
            file.write("\n")


def _execution_workspace_dir(execution_result: CaseExecutionResult, case_dir: Path) -> Path:
    if execution_result.workspace_dir:
        return Path(execution_result.workspace_dir).expanduser().resolve()
    return case_dir / "workspace"


def _runtime_home_dir(case_dir: Path, session_id: str) -> Path:
    """Return the case-scoped runtime home for agent_teams scratch state."""
    if not _needs_short_runtime_home(case_dir):
        return case_dir
    key = f"{case_dir.resolve()}::{session_id}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "ach_team_runtime" / digest


def _prepare_case_dir(case_dir: Path) -> None:
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)


def _needs_short_runtime_home(case_dir: Path) -> bool:
    """Return whether agent_teams scratch should avoid the deep eval tree."""
    _ = case_dir
    return os.name == "nt"


def _behavior_trace(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return bounded behavior evidence derived from backend metadata."""
    return {
        "command_log": list(metadata.get("command_log") or []),
        "workspace_changes": metadata.get("workspace_changes")
        or {
            "added": [],
            "modified": [],
            "removed": [],
        },
    }


def _execution_member_identity(
    execution_result: CaseExecutionResult,
    *,
    team_name: str = "",
) -> dict[str, str]:
    metadata = execution_result.metadata or {}
    member_role = str(metadata.get("member_role") or metadata.get("role") or "").strip()
    member_id = str(metadata.get("member_id") or member_role or "").strip()
    if not member_role:
        member_role = "team" if team_name else "unattributed"
    if not member_id:
        member_id = team_name or member_role
    return {
        "member_id": member_id,
        "member_role": member_role,
    }


def _build_trajectory_events(
    *,
    response: Any,
    behavior_trace: dict[str, Any],
    judge_result: JudgeResult,
    execution_status: str,
    execution_error: str,
) -> list[dict[str, Any]]:
    """Build a bounded case-level event stream for online harness analysis."""
    events: list[dict[str, Any]] = []
    for command in behavior_trace.get("command_log", []):
        if not isinstance(command, dict):
            continue
        events.append(
            _trajectory_event(
                "tool_call",
                summary=_command_summary(command),
                data={
                    "command": command.get("command", ""),
                    "cwd": command.get("cwd", ""),
                    "exit_code": command.get("exit_code"),
                    "stdout_excerpt": command.get("stdout_excerpt", ""),
                    "stderr_excerpt": command.get("stderr_excerpt", ""),
                    "timeout_sec": command.get("timeout_sec"),
                    "background": command.get("background", False),
                },
            )
        )

    workspace_changes = behavior_trace.get("workspace_changes")
    if isinstance(workspace_changes, dict) and any(
        workspace_changes.get(key) for key in ("added", "modified", "removed")
    ):
        events.append(
            _trajectory_event(
                "workspace_change",
                summary=_workspace_change_summary(workspace_changes),
                data=workspace_changes,
            )
        )

    if response not in (None, ""):
        events.append(
            _trajectory_event(
                "agent_response",
                summary=_excerpt(str(response), 600),
                data={"response_excerpt": _excerpt(str(response), 2000)},
            )
        )

    artifact_refs = behavior_trace.get("artifact_refs")
    if isinstance(artifact_refs, dict) and artifact_refs.get("harvested"):
        events.append(
            _trajectory_event(
                "artifact_harvest",
                summary=_artifact_refs_summary(artifact_refs),
                data=artifact_refs,
            )
        )

    events.append(
        _trajectory_event(
            "verifier_result",
            summary=(
                f"{judge_result.method}: "
                f"passed={judge_result.passed}, score={judge_result.score}, "
                f"reason={_excerpt(judge_result.reason, 600)}"
            ),
            data={
                "method": judge_result.method,
                "score": judge_result.score,
                "passed": judge_result.passed,
                "reason": judge_result.reason,
                "metadata": judge_result.metadata,
            },
        )
    )
    events.append(
        _trajectory_event(
            "case_outcome",
            summary=(f"execution_status={execution_status}, passed={judge_result.passed}, score={judge_result.score}"),
            data={
                "execution_status": execution_status,
                "execution_error": execution_error,
                "passed": judge_result.passed,
                "score": judge_result.score,
            },
        )
    )
    for index, event in enumerate(events, start=1):
        event["event_index"] = index
    return events


def _build_pre_judge_trajectory_events(
    *,
    response: Any,
    behavior_trace: dict[str, Any],
    execution_status: str,
    execution_error: str,
) -> list[dict[str, Any]]:
    """Build stable execution evidence before the configured judger runs."""
    events: list[dict[str, Any]] = []
    for command in behavior_trace.get("command_log", []):
        if not isinstance(command, dict):
            continue
        events.append(
            _trajectory_event(
                "tool_call",
                summary=_command_summary(command),
                data={
                    "command": command.get("command", ""),
                    "cwd": command.get("cwd", ""),
                    "exit_code": command.get("exit_code"),
                    "stdout_excerpt": command.get("stdout_excerpt", ""),
                    "stderr_excerpt": command.get("stderr_excerpt", ""),
                    "timeout_sec": command.get("timeout_sec"),
                    "background": command.get("background", False),
                },
            )
        )

    workspace_changes = behavior_trace.get("workspace_changes")
    if isinstance(workspace_changes, dict) and any(
        workspace_changes.get(key) for key in ("added", "modified", "removed")
    ):
        events.append(
            _trajectory_event(
                "workspace_change",
                summary=_workspace_change_summary(workspace_changes),
                data=workspace_changes,
            )
        )

    artifact_refs = behavior_trace.get("artifact_refs")
    if isinstance(artifact_refs, dict) and artifact_refs.get("harvested"):
        events.append(
            _trajectory_event(
                "artifact_harvest",
                summary=_artifact_refs_summary(artifact_refs),
                data=artifact_refs,
            )
        )

    if response not in (None, ""):
        events.append(
            _trajectory_event(
                "agent_response",
                summary=_excerpt(str(response), 600),
                data={"response_excerpt": _excerpt(str(response), 2000)},
            )
        )

    events.append(
        _trajectory_event(
            "case_outcome",
            summary=(
                f"execution_status={execution_status}, judge_status=pending, "
                f"execution_error={_excerpt(execution_error, 600)}"
            ),
            data={
                "execution_status": execution_status,
                "execution_error": execution_error,
                "judge_status": "pending",
            },
        )
    )
    for index, event in enumerate(events, start=1):
        event["event_index"] = index
    return events


def _trajectory_event(event_type: str, *, summary: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "summary": summary,
        "data": data,
    }


def _trajectory_window_summary(
    events: list[dict[str, Any]],
    judge_result: JudgeResult | None,
    *,
    window_size: int = 20,
) -> dict[str, Any]:
    recent_events = events[-window_size:]
    return {
        "window_size": window_size,
        "event_count": len(events),
        "failure_signatures": _failure_signatures(events, judge_result) if judge_result is not None else [],
        "recent_events": [
            {
                "event_index": event.get("event_index"),
                "event_type": event.get("event_type", ""),
                "summary": event.get("summary", ""),
            }
            for event in recent_events
        ],
    }


def _failure_signatures(
    events: list[dict[str, Any]],
    judge_result: JudgeResult,
) -> list[str]:
    if judge_result.passed:
        return []
    signatures: list[str] = []
    if any(
        event.get("event_type") == "tool_call" and event.get("data", {}).get("exit_code") not in (0, None)
        for event in events
    ):
        signatures.append("tool_execution_failure")
    has_workspace_change = any(event.get("event_type") == "workspace_change" for event in events)
    if has_workspace_change:
        signatures.append("patch_quality_gap")
    else:
        signatures.append("workspace_edit_gap")
    return signatures


def _normalized_trace_from_events(
    *,
    case_id: str,
    events: list[dict[str, Any]],
    judge_result: JudgeResult | None,
    member_id: str = "unattributed",
    member_role: str = "unattributed",
    role_traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert bounded case events into the analyzer's normalized trace contract."""
    messages: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = str(event.get("event_type", ""))
        event_index = event.get("event_index") or index + 1
        message: dict[str, Any] = {
            "role": "assistant",
            "message_index": index,
        }
        if event_type == "tool_call":
            data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
            exit_code = data.get("exit_code")
            message["tool_calls"] = [
                {
                    "name": "shell",
                    "input": _excerpt(str(data.get("command", "")), 1200),
                    "output": _excerpt(str(data.get("stdout_excerpt", "")), 1200),
                    "error": _excerpt(str(data.get("stderr_excerpt", "")), 1200) if exit_code not in (0, None) else "",
                    "step_pointer": f"step_{event_index}",
                }
            ]
        elif event_type == "verifier_result":
            data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
            message["tool_calls"] = [
                {
                    "name": "verifier",
                    "input": str(data.get("method", judge_result.method if judge_result is not None else "")),
                    "output": _excerpt(
                        str(data.get("reason", judge_result.reason if judge_result is not None else "")),
                        1200,
                    ),
                    "error": ""
                    if data.get("passed", judge_result.passed if judge_result is not None else True)
                    else _excerpt(str(data.get("reason", "")), 1200),
                    "step_pointer": f"step_{event_index}",
                }
            ]
        else:
            message["content"] = _excerpt(str(event.get("summary", "")), 1200)
            message["step_pointer"] = f"step_{event_index}"
        messages.append(message)

    traces = list(role_traces or [])
    traces.append(
        {
            "trace_id": f"{case_id}__{member_id}__case",
            "member_id": member_id,
            "member_role": member_role,
            "execution_id": case_id,
            "step_count": len(events),
            "message_count": len(messages),
            "messages": messages,
        }
    )
    return {
        "case_id": case_id,
        "traces": traces,
    }


def _normalized_role_traces_from_trajectory_dir(
    *,
    case_id: str,
    trajectory_dir: Path,
) -> list[dict[str, Any]]:
    """Build bounded normalized traces from per-role trajectory stores."""
    if not trajectory_dir.is_dir():
        return []

    traces: list[dict[str, Any]] = []
    role_counts: dict[str, int] = {}
    for role, trajectory_path in _iter_role_trajectory_files(trajectory_dir):
        role_counts[role] = role_counts.get(role, 0) + 1
        trace_role_id = role if role_counts[role] == 1 else f"{role}_{trajectory_path.stem}"
        messages = _messages_from_role_trajectory_file(trajectory_path)
        if not messages:
            messages = [
                {
                    "role": "assistant",
                    "message_index": 0,
                    "content": (
                        f"trajectory file exists for role {role} but has no parsable steps; "
                        f"file={trajectory_path.name}; bytes={trajectory_path.stat().st_size}"
                    ),
                    "step_pointer": "trajectory_summary",
                }
            ]
        traces.append(
            {
                "trace_id": f"{case_id}__{trace_role_id}__trajectory",
                "member_id": role,
                "member_role": role,
                "execution_id": case_id,
                "step_count": len(messages),
                "message_count": len(messages),
                "messages": messages,
            }
        )
    return traces


def _iter_role_trajectory_files(trajectory_dir: Path) -> list[tuple[str, Path]]:
    """Return flat and legacy nested per-role trajectory JSONL files."""
    files: list[tuple[str, Path]] = []
    for trajectory_path in sorted(path for path in trajectory_dir.glob("*.jsonl") if path.is_file()):
        if trajectory_path.name == TRAJECTORY_EVENTS_FILE_NAME:
            continue
        files.append((trajectory_path.stem, trajectory_path))

    for trajectory_path in sorted(path for path in trajectory_dir.glob("*/*.jsonl") if path.is_file()):
        if trajectory_path.name == TRAJECTORY_EVENTS_FILE_NAME:
            continue
        files.append((trajectory_path.parent.name, trajectory_path))
    return files


def _messages_from_role_trajectory_file(path: Path) -> list[dict[str, Any]]:
    """Read a small trajectory JSONL file into bounded normalized messages."""
    try:
        if path.stat().st_size > _MAX_ROLE_TRAJECTORY_FILE_BYTES:
            return _messages_from_large_role_trajectory_file(path)
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            trajectory = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(trajectory, dict):
            return _messages_from_role_trajectory(trajectory)
    return []


def _messages_from_large_role_trajectory_file(path: Path) -> list[dict[str, Any]]:
    """Read only the file tail for huge JSONL trajectories."""
    try:
        size = path.stat().st_size
        read_size = min(size, _ROLE_TRAJECTORY_TAIL_BYTES)
        with open(path, "rb") as file:
            file.seek(max(0, size - read_size))
            raw_tail = file.read(read_size)
    except OSError:
        return []

    tail = raw_tail.decode("utf-8", errors="ignore").strip()
    if not tail:
        return []
    return _index_messages(
        [
            {
                "role": "assistant",
                "content": (f"bounded tail excerpt from large role trajectory: {_excerpt(tail, 2400)}"),
                "step_pointer": "trajectory_tail",
            }
        ]
    )


def _messages_from_role_trajectory(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a compact causal timeline from cumulative ReAct snapshots.

    Each LLM trajectory step contains a cumulative ``messages`` history. Adding
    every copy makes old context dominate the bounded trace and drops the middle
    decisions that diagnosis needs. Keep each distinct control message once,
    then preserve every model response and tool observation in execution order.
    """
    messages: list[dict[str, Any]] = []
    seen_control_messages: set[str] = set()
    for step_index, step in enumerate(trajectory.get("steps") or []):
        if not isinstance(step, dict):
            continue
        detail = step.get("detail")
        if not isinstance(detail, dict):
            continue
        response_message = _normalize_trajectory_message(detail.get("response"))
        legacy_assistant_message: dict[str, Any] = {}
        for message in detail.get("messages") or []:
            normalized = _normalize_trajectory_message(message)
            if not normalized:
                continue
            if normalized.get("role") == "assistant":
                legacy_assistant_message = normalized
                continue
            if normalized.get("role") not in {"system", "user"}:
                continue
            identity = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if identity in seen_control_messages:
                continue
            seen_control_messages.add(identity)
            normalized["step_pointer"] = f"trajectory_step_{step_index + 1}:input"
            messages.append(normalized)
        response_message = response_message or legacy_assistant_message
        if response_message:
            response_message["step_pointer"] = f"trajectory_step_{step_index + 1}:response"
            messages.append(response_message)
        tool_name = detail.get("tool_name")
        if tool_name:
            call_args = detail.get("call_args", detail.get("args", {}))
            call_result = detail.get("call_result", detail.get("result", {}))
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": str(tool_name),
                            "input": _excerpt(json.dumps(call_args, ensure_ascii=False), 1200),
                            "output": _excerpt(json.dumps(call_result, ensure_ascii=False), 1200),
                            "error": _excerpt(json.dumps(step.get("error", ""), ensure_ascii=False), 1200)
                            if step.get("error")
                            else "",
                        }
                    ],
                    "step_pointer": f"trajectory_step_{step_index + 1}:tool",
                }
            )
    return _index_messages(_bounded_role_trace_messages(messages))


def _bounded_role_trace_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep both startup context and completion evidence in bounded traces."""
    if len(messages) <= _MAX_ROLE_TRACE_MESSAGES:
        return messages
    head_count = _MAX_ROLE_TRACE_MESSAGES // 2
    tail_count = _MAX_ROLE_TRACE_MESSAGES - head_count
    return [*messages[:head_count], *messages[-tail_count:]]


def _normalize_trajectory_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    normalized: dict[str, Any] = {
        "role": str(message.get("role") or "unknown"),
    }
    content = str(message.get("content") or "").strip()
    reasoning = str(message.get("reasoning_content") or "").strip()
    if reasoning:
        reasoning_block = f"[reasoning]\n{reasoning}"
        content = f"{content}\n\n{reasoning_block}" if content else reasoning_block
    if content:
        normalized["content"] = _excerpt(content, 1200)
    if "tool_calls" in message:
        normalized["tool_calls"] = message.get("tool_calls")
    if len(normalized) == 1:
        return {}
    return normalized


def _index_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for index, message in enumerate(messages[:_MAX_ROLE_TRACE_MESSAGES]):
        item = dict(message)
        item["message_index"] = index
        item.setdefault("step_pointer", f"trajectory_step_{index + 1}")
        indexed.append(item)
    return indexed


def _command_summary(command: dict[str, Any]) -> str:
    exit_code = command.get("exit_code")
    status = "timed out" if exit_code is None else f"exited with {exit_code}"
    stderr = str(command.get("stderr_excerpt", "") or "")
    detail = f"; stderr={_excerpt(stderr, 240)}" if stderr else ""
    return f"{command.get('command', '')} {status}{detail}"


def _workspace_change_summary(workspace_changes: dict[str, Any]) -> str:
    added = workspace_changes.get("added") or []
    modified = workspace_changes.get("modified") or []
    removed = workspace_changes.get("removed") or []
    return f"added={list(added)[:20]}, modified={list(modified)[:20]}, removed={list(removed)[:20]}"


def _artifact_refs_summary(artifact_refs: dict[str, Any]) -> str:
    harvested = list(artifact_refs.get("harvested") or [])
    missing = list(artifact_refs.get("missing") or [])
    return f"artifact_harvest: harvested={harvested[:20]}, missing={missing[:20]}"


def _excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _team_workspace_dir(case_dir: Path, team_name: str) -> Path:
    workspace_dir = team_home(team_name) / "team-workspace"
    if (workspace_dir / "artifacts").is_dir():
        return workspace_dir
    return case_dir / "workspace"


def _write_error_case_artifacts(
    *,
    case: dict[str, Any],
    case_id: str,
    session_id: str,
    team_name: str,
    started_at: datetime,
    finished_at: datetime,
    trace_path: Path,
    result_path: Path,
    artifact_refs: dict[str, list[str]],
    response: Any,
    error: str,
) -> EvaluationCaseTraceRef:
    """Persist minimal artifacts for a case-scoped execution error."""
    trajectory_dir = trace_path.parent / ROLE_TRAJECTORY_DIR_NAME
    behavior_trace = _behavior_trace({})
    trajectory_events = _build_pre_judge_trajectory_events(
        response=response,
        behavior_trace=behavior_trace,
        execution_status="error",
        execution_error=error,
    )
    trajectory_events_path = trajectory_dir / TRAJECTORY_EVENTS_FILE_NAME
    _write_jsonl(trajectory_events_path, trajectory_events)
    behavior_trace["trajectory_events_path"] = str(trajectory_events_path)
    behavior_trace["trajectory_window_summary"] = _trajectory_window_summary(
        trajectory_events,
        None,
    )
    normalized_trace_path = trace_path.parent / "judge" / "normalized_trace.json"
    normalized_trace = _normalized_trace_from_events(
        case_id=case_id,
        events=trajectory_events,
        judge_result=None,
    )
    _write_json(normalized_trace_path, normalized_trace)
    behavior_trace["normalized_trace_path"] = str(normalized_trace_path)
    behavior_trace["normalized_trace_summary"] = normalized_trace
    trace_payload = {
        "case_id": case_id,
        "session_id": session_id,
        "team_name": team_name,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": "error",
        "input": _case_inputs(case),
        "response": response,
        "error": error,
        "trajectory_dir": str(trajectory_dir),
        "behavior_trace": behavior_trace,
        "evaluation": {
            "method": "error",
            "score": 0.0,
            "passed": False,
            "reason": error,
            "metadata": {},
        },
    }
    result_payload = {
        "case_id": case_id,
        "session_id": session_id,
        "status": "error",
        "score": 0.0,
        "evaluation": {
            "method": "error",
            "passed": False,
            "reason": error,
            "metadata": {},
        },
        "result": response,
        "error": error,
        "trace_path": str(trace_path),
        "artifacts": artifact_refs,
        "metadata": {
            "team_name": team_name,
            "case_path": case.get("case_path", ""),
            "case_metadata": case.get("metadata", {}),
            "training_signal": case.get("training_signal", {}),
        },
    }
    _write_json(trace_path, trace_payload)
    _write_json(result_path, result_payload)

    return EvaluationCaseTraceRef(
        case_id=case_id,
        case_path=str(case.get("case_path", "")),
        trace_path=str(trace_path),
        result_path=str(result_path),
        status="error",
        score=0.0,
        metadata={
            "session_id": session_id,
            "team_name": team_name,
            "evaluation_method": "error",
            "evaluation_passed": False,
        },
    )


def _harvest_artifacts(
    workspace_dir: Path,
    dest_dir: Path,
    expected_files: list[str] | None = None,
) -> dict[str, list[str]]:
    """Copy ``<team-workspace>/artifacts/`` into ``case_dir/artifacts/``.

    ``workspace_dir`` is the shared team-workspace directory
    (``team_home(team_name)/team-workspace``).  Its ``artifacts/`` subdirectory
    holds the team's deliverables.

    ``EvalTeamRail`` pre-harvests into ``dest_dir`` before ``clean_team`` fires,
    because ``clean_team`` deletes the entire ``team_home`` subtree (including
    team-workspace) via ``register_cleanup_path``.  This helper is still required
    as a fallback for normal exits without ``clean_team`` and for partial failures
    where the workspace is still intact when this function runs.
    """
    expected = _normalize_expected_files(expected_files)
    src = workspace_dir / "artifacts"
    sources = _artifact_harvest_sources(workspace_dir)
    if dest_dir.is_dir():
        _normalize_deliverable_artifacts(dest_dir)
        if expected:
            missing = _filter_expected_artifacts(dest_dir, expected)
        else:
            missing = []
        pre_harvested = sorted(path.relative_to(dest_dir).as_posix() for path in dest_dir.rglob("*") if path.is_file())
        if pre_harvested:
            if not src.is_dir():
                logger.info(
                    "[CaseRunner] artifacts already harvested by rail ({} file(s)); workspace unavailable",
                    len(pre_harvested),
                )
                return {"harvested": pre_harvested, "missing": missing}
            logger.info(
                "[CaseRunner] refreshing {} pre-harvested artifact(s) from workspace",
                len(pre_harvested),
            )

    if not src.is_dir():
        logger.info("[CaseRunner] no artifacts directory in workspace")
        return {"harvested": [], "missing": []}

    dest_dir.mkdir(parents=True, exist_ok=True)
    if expected:
        _copy_artifact_trees(sources, dest_dir)
        _normalize_deliverable_artifacts(dest_dir)
        missing = _filter_expected_artifacts(dest_dir, expected)
    else:
        _, missing = _copy_artifact_trees(sources, dest_dir)
        _normalize_deliverable_artifacts(dest_dir)

    harvested = sorted(path.relative_to(dest_dir).as_posix() for path in dest_dir.rglob("*") if path.is_file())
    for rel_path in harvested:
        logger.debug("artifact harvested: {} -> {}", rel_path, dest_dir / rel_path)

    return {"harvested": harvested, "missing": missing}


def _artifact_harvest_sources(workspace_dir: Path) -> list[Path]:
    sources: list[Path] = []
    src = workspace_dir / "artifacts"
    if src.is_dir():
        sources.append(src)
    team_root = workspace_dir.parent
    team_name = team_root.name
    member_root = team_root / "workspaces"
    if member_root.is_dir():
        for member_workspace in sorted(member_root.glob("*_workspace")):
            for candidate in (
                member_workspace / ".team" / team_name / "artifacts",
                member_workspace / "artifacts",
            ):
                if not candidate.is_dir():
                    continue
                if any(_same_path(candidate, existing) for existing in sources):
                    continue
                sources.append(candidate)
    return sources


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve() == right.resolve()


def _harvest_changed_workspace_files(
    *,
    workspace_dir: Path,
    dest_dir: Path,
    workspace_changes: Any,
    expected_files: list[str] | None = None,
) -> dict[str, list[str]]:
    """Copy changed standalone-harness workspace outputs into case artifacts."""
    if not workspace_dir.is_dir() or not isinstance(workspace_changes, dict):
        return {"harvested": [], "missing": []}

    candidate_paths: list[str] = []
    for key in ("added", "modified"):
        values = workspace_changes.get(key) or []
        if isinstance(values, list):
            candidate_paths.extend(str(value) for value in values)

    harvested: list[str] = []
    missing: list[str] = []
    for rel in candidate_paths[:200]:
        source = (workspace_dir / rel).resolve()
        try:
            source.relative_to(workspace_dir.resolve())
        except ValueError:
            missing.append(rel)
            continue
        if not source.is_file() or _skip_harvest_path(rel):
            continue
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except FileNotFoundError:
            missing.append(rel)
            continue
        harvested.append(Path(rel).as_posix())

    expected = _normalize_expected_files(expected_files)
    _normalize_deliverable_artifacts(dest_dir)
    if expected:
        missing.extend(_filter_expected_artifacts(dest_dir, expected))
    harvested = sorted(path.relative_to(dest_dir).as_posix() for path in dest_dir.rglob("*") if path.is_file())
    return {"harvested": harvested, "missing": missing}


def _copy_artifact_tree(src: Path, dest_dir: Path) -> tuple[list[str], list[str]]:
    """Copy an artifact tree file by file so nested parents always exist."""
    harvested: list[str] = []
    missing: list[str] = []
    for source in sorted(path for path in src.rglob("*") if path.is_file()):
        rel_path = source.relative_to(src)
        target = dest_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _should_copy_artifact(source, target):
                shutil.copy2(source, target)
        except FileNotFoundError:
            missing.append(rel_path.as_posix())
            continue
        harvested.append(rel_path.as_posix())
    return harvested, missing


def _copy_artifact_trees(sources: list[Path], dest_dir: Path) -> tuple[list[str], list[str]]:
    harvested: list[str] = []
    missing: list[str] = []
    for src in sources:
        copied, missed = _copy_artifact_tree(src, dest_dir)
        harvested.extend(copied)
        missing.extend(missed)
    return harvested, missing


def _copy_expected_artifacts_from_sources(
    sources: list[Path],
    dest_dir: Path,
    expected_files: list[str],
) -> tuple[list[str], list[str]]:
    harvested: list[str] = []
    missing: list[str] = []
    for filename in expected_files:
        source = _find_named_file_in_sources(sources, filename)
        if source is None:
            missing.append(filename)
            continue
        target = dest_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except FileNotFoundError:
            missing.append(filename)
            continue
        harvested.append(filename)
    return harvested, missing


def _copy_expected_artifacts(
    src: Path,
    dest_dir: Path,
    expected_files: list[str],
) -> tuple[list[str], list[str]]:
    """Copy only declared final deliverables into the artifact root."""
    harvested: list[str] = []
    missing: list[str] = []
    for filename in expected_files:
        source = _find_named_file(src, filename)
        if source is None:
            missing.append(filename)
            continue
        target = dest_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except FileNotFoundError:
            missing.append(filename)
            continue
        harvested.append(filename)
    return harvested, missing


def _copy_proof_artifacts(sources: list[Path], dest_dir: Path) -> list[str]:
    harvested: list[str] = []
    for src in sources:
        for source in sorted(path for path in src.rglob("*") if path.is_file()):
            rel_path = source.relative_to(src)
            if not _is_proof_artifact_path(rel_path):
                continue
            target = dest_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if _should_copy_artifact(source, target):
                    shutil.copy2(source, target)
            except FileNotFoundError:
                continue
            harvested.append(rel_path.as_posix())
    return harvested


def _filter_expected_artifacts(dest_dir: Path, expected_files: list[str]) -> list[str]:
    """Validate expected deliverables without discarding supporting evidence.

    ``expected_files`` is a completion contract, not an artifact allowlist.  Team
    members may also produce diagnoses, verification results, manifests, or other
    evidence explicitly requested by the case.  Removing those files here makes
    the judge observe a different delivery from the one the team completed.
    """
    missing: list[str] = []
    for filename in expected_files:
        root_target = dest_dir / filename
        source = _find_named_file(dest_dir, filename)
        if source is None:
            missing.append(filename)
            continue
        if not _same_path(source, root_target):
            root_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, root_target)
    expected_set = set(expected_files)
    for path in sorted(dest_dir.rglob("*"), reverse=True):
        if not path.is_file():
            continue
        rel_path = path.relative_to(dest_dir)
        if path.name in expected_set and rel_path.as_posix() != path.name:
            path.unlink()
    for path in sorted(dest_dir.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return missing


def _normalize_expected_files(expected_files: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in expected_files or []:
        filename = Path(str(item)).name
        if filename and filename not in result:
            result.append(filename)
    return result


def _find_named_file(root: Path, filename: str) -> Path | None:
    candidates: list[Path] = []
    direct = root / filename
    if direct.is_file():
        candidates.append(direct)
    candidates.extend(source for source in sorted(root.rglob(filename)) if source.is_file() and source != direct)
    if not candidates:
        return None
    return max(candidates, key=_artifact_quality_key)


def _find_named_file_in_sources(sources: list[Path], filename: str) -> Path | None:
    candidates = [source for source in (_find_named_file(src, filename) for src in sources) if source]
    if not candidates:
        return None
    return max(candidates, key=_artifact_quality_key)


def _is_proof_artifact_path(path: Path) -> bool:
    if path.suffix.lower() not in _PROOF_ARTIFACT_SUFFIXES:
        return False
    normalized = "_".join(part.lower().replace("-", "_") for part in path.parts)
    return any(hint in normalized for hint in _PROOF_ARTIFACT_NAME_HINTS)


def _normalize_deliverable_artifacts(dest_dir: Path) -> None:
    """Expose common deliverables at artifacts root while preserving originals."""
    if not dest_dir.is_dir():
        return
    for filename in _CANONICAL_DELIVERABLE_FILES:
        root_target = dest_dir / filename
        if root_target.is_file():
            continue
        source = _find_nested_artifact(dest_dir, filename)
        if source is None:
            continue
        root_target.parent.mkdir(parents=True, exist_ok=True)
        if _should_copy_artifact(source, root_target):
            shutil.copy2(source, root_target)


def _find_nested_artifact(dest_dir: Path, filename: str) -> Path | None:
    candidates = [
        source for source in sorted(dest_dir.rglob(filename)) if source.is_file() and source.parent != dest_dir
    ]
    if not candidates:
        return None
    return max(candidates, key=_artifact_quality_key)


def _should_copy_artifact(source: Path, target: Path) -> bool:
    if not target.exists():
        return True
    if source.name in _CANONICAL_DELIVERABLE_FILES:
        return _artifact_quality_key(source) > _artifact_quality_key(target)
    return True


def _artifact_quality_key(path: Path) -> tuple[int, int, int]:
    try:
        size = path.stat().st_size
    except OSError:
        return (0, 0, 0)
    if path.name == "index.html":
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            text = ""
        complete = int("</body>" in text and "</html>" in text)
        return (complete, text.count("<section"), size)
    return (int(size > 0), 0, size)


def _skip_harvest_path(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return True
    if _is_runtime_workspace_metadata(path):
        return True
    if parts[0] in {
        ".git",
        ".agent_teams",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "context",
        "memory",
        "messages",
        "todo",
    }:
        return True
    if any(part in {"node_modules", ".venv", "venv"} for part in parts):
        return True
    return Path(path).suffix.lower() in {".pyc", ".pyo", ".log"}


async def _cleanup_scratch(
    case_dir: Path,
    team_spec: TeamAgentSpec | None,
    runtime_home_dir: Path | None = None,
) -> None:
    """Delete case-scoped scratch files after runtime cleanup.

    ``clean_team`` already deletes ``<case_dir>/.agent_teams/{team}`` via
    ``register_cleanup_path``.  This function removes any residual
    ``.agent_teams/`` subtree (e.g. when ``clean_team`` was not called due to an
    exception) and the case-scoped ``team.db*`` files.
    """
    if team_spec is not None:
        await _close_case_team_db(team_spec)

    scratch_roots = []
    if runtime_home_dir is not None:
        scratch_roots.append(runtime_home_dir)
    if runtime_home_dir is None or runtime_home_dir.resolve() != case_dir.resolve():
        scratch_roots.append(case_dir)

    for scratch_root in scratch_roots:
        agent_teams_dir = scratch_root / ".agent_teams"
        if agent_teams_dir.exists():
            shutil.rmtree(agent_teams_dir, ignore_errors=True)
            logger.info(".agent_teams scratch removed: {}", agent_teams_dir)

    if runtime_home_dir is not None and runtime_home_dir.resolve() != case_dir.resolve():
        shutil.rmtree(runtime_home_dir, ignore_errors=True)
        logger.info("runtime home scratch removed: {}", runtime_home_dir)

    legacy_workspace_dir = case_dir / "workspace"
    if legacy_workspace_dir.exists():
        shutil.rmtree(legacy_workspace_dir, ignore_errors=True)
        logger.info("legacy workspace scratch removed: {}", legacy_workspace_dir)

    for suffix in ("", "-shm", "-wal"):
        db_path = case_dir / f"team.db{suffix}"
        if not db_path.exists():
            continue
        try:
            db_path.unlink()
            logger.debug("team.db scratch removed: {}", db_path)
        except OSError as exc:
            logger.warning("failed to remove {}: {}", db_path.name, exc)


async def _close_case_team_db(team_spec: TeamAgentSpec) -> None:
    """Close the case-scoped sqlite engine before deleting its files."""
    db_config = team_spec.resolve_db_config()
    if db_config.db_type != "sqlite":
        return
    try:
        db = get_shared_db(db_config)
        close = getattr(db, "close", None)
        if close is not None:
            await close()
    except Exception as exc:
        logger.warning("failed to close case-scoped team db before scratch cleanup: {}", exc)


__all__ = [
    "CaseRunner",
]
