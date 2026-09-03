"""Atomic persistence for manager state, events, rounds, and terminal reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import current_context
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    EXPERIMENTS_ROOT,
    ensure_manager_dir,
    manager_events_path,
    manager_pause_path,
    manager_report_path,
    manager_round_dir,
    manager_rounds_path,
    manager_state_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    ManagerSnapshot,
    PersistedManagerState,
    TerminalReport,
)

SCHEMA_VERSION = 1


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def save_state(state: PersistedManagerState) -> Path:
    path = manager_state_path(state.task_state.run_id)
    ensure_manager_dir(state.task_state.run_id)
    _atomic_write_text(path, state.model_dump_json(indent=2))
    return path


def load_state(run_id: str) -> PersistedManagerState:
    path = manager_state_path(run_id)
    if not path.is_file():
        raise FileNotFoundError(f"manager state not found for run_id={run_id!r}")
    return PersistedManagerState.model_validate_json(path.read_text(encoding="utf-8"))


def try_load_state(run_id: str) -> PersistedManagerState | None:
    path = manager_state_path(run_id)
    if not path.is_file():
        return None
    return PersistedManagerState.model_validate_json(path.read_text(encoding="utf-8"))


def append_event(run_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
    path = manager_events_path(run_id)
    ensure_manager_dir(run_id)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": event,
        "run_id": run_id,
    }
    ctx = current_context()
    if ctx is not None:
        for key, value in ctx.as_dict().items():
            record.setdefault(key, value)
    if payload:
        record.update(payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def append_round(state: PersistedManagerState) -> None:
    if not state.rounds:
        return
    path = manager_rounds_path(state.task_state.run_id)
    ensure_manager_dir(state.task_state.run_id)
    latest = state.rounds[-1]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(latest.model_dump_json() + "\n")
    round_dir = manager_round_dir(state.task_state.run_id, latest.round_index)
    round_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(round_dir / "decision.json", latest.decision.model_dump_json(indent=2))
    if latest.report is not None:
        _atomic_write_text(
            round_dir / "report.json", latest.report.model_dump_json(indent=2)
        )


def write_manager_round_snapshot(
    run_id: str,
    round_index: int,
    snapshot: ManagerSnapshot,
    query: str,
) -> None:
    """Persist the exact manager view used for this round's decision."""
    round_dir = manager_round_dir(run_id, round_index)
    round_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(round_dir / "snapshot.json", snapshot.model_dump_json(indent=2))
    _atomic_write_text(round_dir / "query.txt", query)


def write_terminal_report(state: PersistedManagerState) -> Path:
    if state.terminal is None:
        raise ValueError("cannot write terminal report without TerminalReport")
    path = manager_report_path(state.task_state.run_id)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": state.terminal.status,
        "run_id": state.terminal.run_id,
        "rounds_run": state.terminal.rounds_run,
        "abort_reason": state.terminal.abort_reason,
        "failure_reason": state.terminal.failure_reason,
        "summary": state.terminal.summary,
        "completion_satisfied": state.terminal.completion_satisfied,
        "topic": state.original_task.topic,
        "phase": state.task_state.phase,
        "unresolved_issues": list(state.task_state.unresolved_issues),
    }
    _atomic_write_text(path, _json_dump(payload))
    save_state(state)
    append_event(
        state.task_state.run_id,
        "manager_terminal",
        payload,
    )
    return path


def write_pause_notes(state: PersistedManagerState) -> Path:
    """Write a human-readable pause note into the manager folder."""
    run_id = state.task_state.run_id
    ensure_manager_dir(run_id)
    path = manager_pause_path(run_id)
    workspace = f"{EXPERIMENTS_ROOT}/{run_id}"
    text = (
        f"This run was paused by the operator (Ctrl+C).\n"
        f"The in-flight module was discarded; resume starts from the last finished module.\n"
        f"\n"
        f"run_id: {run_id}\n"
        f"workspace: {workspace}\n"
        f"rounds_finished: {state.task_state.counters.rounds_used}\n"
        f"\n"
        f"Resume:\n"
        f"  uv run python scripts/run_manager.py --run-id {run_id} --resume\n"
        f"\n"
        f"Steer:\n"
        f"  uv run python scripts/run_manager.py --run-id {run_id} --resume "
        f"--followup \"your instruction\"\n"
        f"\n"
        f"Or write `{workspace}/manager/user_followup.md` and resume with `--resume`.\n"
    )
    _atomic_write_text(path, text)
    return path


def write_crash_terminal(
    *,
    run_id: str,
    status: str,
    reason: str,
    existing: PersistedManagerState | None = None,
    exception_type: str = "",
) -> TerminalReport:
    terminal = TerminalReport(
        status=status,  # type: ignore[arg-type]
        run_id=run_id,
        rounds_run=len(existing.rounds) if existing is not None else 0,
        abort_reason=status,
        failure_reason=reason,
        summary=reason,
        completion_satisfied=False,
    )
    if existing is not None:
        existing.terminal = terminal
        existing.task_state.phase = "terminal"
        write_terminal_report(existing)
        return terminal

    ensure_manager_dir(run_id)
    path = manager_report_path(run_id)
    _atomic_write_text(
        path,
        _json_dump(
            {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "run_id": run_id,
                "rounds_run": 0,
                "abort_reason": status,
                "failure_reason": reason,
                "summary": reason,
                "completion_satisfied": False,
                "exception_type": exception_type,
            }
        ),
    )
    append_event(run_id, "manager_crash", {"status": status, "reason": reason})
    return terminal


def bounded_text(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 15)].rstrip() + "\n...[truncated]"
