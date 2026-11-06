"""Operator pause / resume helpers for the manager loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    EXPERIMENTS_ROOT,
    manager_user_followup_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.artifacts import (
    append_event,
    save_state,
    write_pause_notes,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    OperatorFollowup,
    PersistedManagerState,
)

PAUSE_EXIT_CODE = 130

# Dedicated stderr logger for the Ctrl+C pause message: it must reach the
# operator's terminal immediately regardless of whatever run-scoped logging
# configuration (console=False, file-only handlers) is active for this run
# via common.logging.get_logger — unlike that logger, this one is never
# silenced by _silence_logger_tree("auto_research").
_interrupt_logger = logging.getLogger(f"{__name__}.interrupt")
_interrupt_logger.propagate = False
if not _interrupt_logger.handlers:
    _interrupt_handler = logging.StreamHandler(sys.stderr)
    _interrupt_handler.setFormatter(logging.Formatter("%(message)s"))
    _interrupt_logger.addHandler(_interrupt_handler)
    _interrupt_logger.setLevel(logging.ERROR)


class RunPaused(Exception):
    """Raised by run_asyncio when the coroutine is interrupted.

    Carries the intended process exit code. SystemExit belongs only at the
    true process entry point (e.g. scripts/run_manager.py's
    ``if __name__ == "__main__":`` block), not inside this reusable helper —
    the caller is expected to catch this and call ``sys.exit(exc.exit_code)``.
    """

    def __init__(self, exit_code: int = PAUSE_EXIT_CODE) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


def last_finished_round_index(state: PersistedManagerState) -> int:
    finished = 0
    for round_rec in state.rounds:
        if round_rec.report is not None or round_rec.decision.signal in {"DONE", "BLOCKED"}:
            finished = round_rec.round_index
    return finished


def last_finished_contract(state: PersistedManagerState):
    contract = None
    for round_rec in state.rounds:
        if round_rec.report is not None:
            contract = round_rec.contract
    return contract


def discard_in_flight_round(state: PersistedManagerState) -> dict[str, Any]:
    """Drop an unfinished module so resume restarts from the last finished round."""
    pending = state.task_state.pending_contract
    payload: dict[str, Any] = {
        "discarded_module": pending.module if pending is not None else "",
        "discarded_mode": pending.mode if pending is not None else "",
        "rounds_used_before": state.task_state.counters.rounds_used,
    }
    if pending is not None and pending.module == "experiment_design":
        state.task_state.design_session_epoch += 1
        payload["design_session_epoch"] = state.task_state.design_session_epoch
    finished = last_finished_round_index(state)
    state.task_state.pending_contract = None
    state.task_state.counters.rounds_used = finished
    state.task_state.last_contract = last_finished_contract(state)
    state.task_state.phase = "paused"
    payload["rounds_used_after"] = finished
    return payload


def inject_operator_followup(
    state: PersistedManagerState,
    text: str,
    *,
    source: Literal["cli", "file"] = "cli",
) -> OperatorFollowup | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    item = OperatorFollowup(text=cleaned, source=source)
    state.operator_followups.append(item)
    return item


def consume_workspace_followup(run_id: str) -> str:
    path = manager_user_followup_path(run_id)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    consumed = path.with_name("user_followup.consumed.md")
    if consumed.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        consumed = path.with_name(f"user_followup.consumed.{stamp}.md")
    path.replace(consumed)
    return text


def followup_from_file(path: str | Path) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"follow-up file not found: {file_path}")
    return file_path.read_text(encoding="utf-8").strip()


def combine_followup_texts(*parts: str) -> str:
    chunks = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(chunks)


def clear_terminal_for_followup(state: PersistedManagerState) -> None:
    state.terminal = None
    if state.task_state.phase == "terminal":
        state.task_state.phase = "paused"


def persist_pause(state: PersistedManagerState) -> dict[str, Any]:
    payload = discard_in_flight_round(state)
    save_state(state)
    write_pause_notes(state)
    append_event(state.task_state.run_id, "manager_paused", payload)
    return payload


def apply_resume_steering(
    state: PersistedManagerState,
    *,
    followup: str = "",
) -> bool:
    """Inject follow-ups and clear a terminal when the operator is steering.

    Returns True when the loop should continue (follow-up present or no terminal).
    """
    workspace_text = consume_workspace_followup(state.task_state.run_id)
    cli_text = (followup or "").strip()
    if cli_text:
        inject_operator_followup(state, cli_text, source="cli")
    if workspace_text:
        inject_operator_followup(state, workspace_text, source="file")
    steered = bool(cli_text or workspace_text)
    if steered and state.terminal is not None:
        clear_terminal_for_followup(state)
    if state.task_state.pending_contract is not None:
        payload = discard_in_flight_round(state)
        append_event(state.task_state.run_id, "manager_discard_in_flight", payload)
    save_state(state)
    return steered or state.terminal is None


def add_hitl_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--followup",
        default="",
        help="Operator instruction to inject when resuming a paused or finished run",
    )
    parser.add_argument(
        "--followup-file",
        default=None,
        help="Read an extra operator instruction from this file (in addition to --followup "
        "and experiments/<run_id>/manager/user_followup.md)",
    )
    return parser


def followup_from_args(args: argparse.Namespace) -> str:
    parts = [str(getattr(args, "followup", "") or "")]
    extra = getattr(args, "followup_file", None)
    if extra:
        parts.append(followup_from_file(extra))
    return combine_followup_texts(*parts)


def pause_hint(run_id: str) -> str:
    workspace = f"{EXPERIMENTS_ROOT}/{run_id}"
    return (
        f"Paused run_id={run_id}\n"
        f"Workspace: {workspace}\n"
        f"Resume: uv run python scripts/run_manager.py --run-id {run_id} --resume\n"
        f"Steer:  uv run python scripts/run_manager.py --run-id {run_id} --resume "
        f"--followup \"your instruction\"\n"
    )


def run_asyncio(coro, *, run_id: str | None = None):
    """Run a manager coroutine; map Ctrl+C to a pause hint and RunPaused."""
    try:
        return asyncio.run(coro)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        if run_id:
            _interrupt_logger.error(pause_hint(run_id).rstrip("\n"))
        else:
            _interrupt_logger.error("Paused. See experiments/<run_id>/manager/PAUSE.md for resume instructions.")
        raise RunPaused(PAUSE_EXIT_CODE) from exc
