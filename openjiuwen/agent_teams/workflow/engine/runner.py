# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Top-level engine entrypoint.

``run_workflow`` loads a script, builds a :class:`~workflow.engine.runtime.Runtime`,
creates the concurrency semaphore **inside the running loop**, binds the run via
``contextvars``, and awaits the script's ``run(args)``. It brackets the run with
``WORKFLOW_STARTED`` / ``WORKFLOW_COMPLETED`` progress events.

Unlike the dw reference, there is no CLI ``main()`` here — the team integration
drives runs through ``workflow/runner.py:run_swarmflow`` (real worker backend)
or directly with ``MockBackend`` from tests; library code must not ``print``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from .admission import AgentAdmission
from .backends import MockBackend
from .backends.base import AgentBackend
from .budget import BudgetLedger
from .errors import BudgetExhausted, MetaError, WorkflowAborted
from .journal import Journal
from .loader import load_workflow_source
from .primitives import (
    _budget_snapshot,
    _fresh_holder,
    _invoke_loaded,
    _path,
    _preview,
    _rt,
    _seq,
    _top_phases,
    _wf_budget_snapshot,
)
from .progress import PhasePlan, ProgressKind, ProgressSink, WorkflowProgressEvent, noop_progress_sink
from .provider import ENGINE_PROVIDER
from .runtime import AbortSignal, Runtime
from .seam import reset_provider, use_provider


def _normalize_meta_phases(raw: list[Any] | None) -> list[PhasePlan] | None:
    """Normalize raw META ``phases`` (strings / dicts) to ``list[PhasePlan]``.

    Accepts the shapes that ``ast.literal_eval`` produces from a META dict:
    plain strings (``"Search"``) or dicts with ``title`` / ``name`` and
    optional ``description``.
    """
    if raw is None:
        return None
    result: list[PhasePlan] = []
    for item in raw:
        if isinstance(item, str):
            result.append(PhasePlan(title=item))
        elif isinstance(item, dict):
            title = str(item.get("title") or "?")
            desc = item.get("description")
            result.append(PhasePlan(title=title, description=str(desc) if desc is not None else None))
        else:
            result.append(PhasePlan(title=str(item)))
    return result


def _silent(_message: str) -> None:
    """No-op text sink (default ``log_sink``); never ``print`` in library code."""
    return None


async def _write_seal_record(rt, *, terminal_status: str) -> None:
    """Write a run-level seal record to the journal on a terminal exit.

    ``terminal_status`` is ``"completed"`` (normal finish) or ``"stopped"``
    (session budget exhausted / user stop — a true terminal that cannot be
    recovered by editing the script). The seal record carries the run's final
    per-run spent so a relaunch can detect (via ``find_run_record(run_id,
    "seal")``) that the prior run ended and force a fresh run_id. No-op when the
    journal has no WAL path (offline preview / no persistence).
    """
    if rt.journal is None or rt.journal.wal_path is None:
        return
    await rt.journal.write_run_record(
        rt.run_id or "", "seal",
        {"terminal_status": terminal_status, "final_spent": rt.workflow_budget.spent},
    )


async def _write_pause_record(rt, *, pause_reason: str) -> None:
    """Write a run-level pause record on a pause / early_return / workflow-budget hit.

    Records *which* agent was in-flight and how many tokens it had already
    billed (``spent - started_spent``), plus the pause reason and timestamp —
    NOT the run's total token snapshot (the resume re-bills spent by replaying
    cache hits). ``pause_reason`` is one of ``"paused"`` / ``"early_return"`` /
    ``"workflow_budget_exhausted"``. No-op without a journal (offline preview).
    """
    if rt.journal is None or rt.journal.wal_path is None:
        return
    paused_agent = None
    cur = rt.current_agent
    if cur is not None:
        paused_agent = {
            "agent_id": cur.get("agent_id"),
            "label": cur.get("label"),
            "tokens": rt.workflow_budget.spent - (cur.get("started_spent") or 0),
        }
    await rt.journal.write_run_record(
        rt.run_id or "", "pause",
        {
            "pause_reason": pause_reason,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "paused_agent": paused_agent,
        },
    )


def _resolve_workflow_budget(
    loaded,
    session_budget: BudgetLedger | None = None,
) -> BudgetLedger:
    """Build the per-run ledger from the script's ``META.workflow_token_limit``.

    The per-run ceiling is declared by the workflow script itself — the session
    budget is the team-wide total, while each workflow knows its own cost. A
    missing/invalid limit yields an unbounded ledger (``spent`` still counts for
    display, but no per-run ceiling is enforced) — this keeps hand-written
    scripts that do not declare a limit backwards-compatible (equivalent to the
    single-layer behaviour), while the generator enforces the field on new
    scripts.

    A declared per-run ceiling is validated against the session budget: the run
    is a sub-view of the team's shared total, so ``workflow_token_limit`` must
    not exceed the session ceiling — otherwise the run could never complete
    within its own declared budget while the shared pool holds the real limit.

    The ledger always starts at ``spent=0`` — on a resume the emit hooks re-bill
    it by replaying cache hits (each cache-hit agent adds its record's stored
    tokens back), so the tally is recomputed for the current script rather than
    restored from a stale snapshot.
    """
    meta = loaded.meta if isinstance(loaded.meta, dict) else {}
    limit = meta.get("workflow_token_limit")
    if isinstance(limit, int) and limit > 0:
        if session_budget is not None and session_budget.total is not None:
            if limit > session_budget.total:
                raise MetaError(
                    f"META.workflow_token_limit ({limit}) exceeds the session "
                    f"swarmflow_budget ({session_budget.total}); a per-run ceiling "
                    f"cannot exceed the team ceiling"
                )
        return BudgetLedger(total=limit)
    return BudgetLedger()


def _bind_workflow_budget(backend: AgentBackend, workflow_budget: BudgetLedger) -> None:
    """Hand the per-run ledger to the backend if it accepts one.

    ``AgentBackend.bind_budget`` binds the session-wide ledger; the per-run
    ledger is plumbed through a separate hook so a backend that does not
    override ``bind_workflow_budget`` (older implementations) simply ignores it.
    The engine's own ``_check_budget`` gate still reads ``rt.workflow_budget``
    directly, so enforcement does not depend on backend support.
    """
    fn = getattr(backend, "bind_workflow_budget", None)
    if callable(fn):
        fn(workflow_budget)


async def _exec_loaded(loaded, rt: Runtime) -> Any:
    # Install the engine as the active provider so the public facade primitives
    # forward here for the lifetime of this run.
    tok_prov = use_provider(ENGINE_PROVIDER)
    tok_rt = _rt.set(rt)
    tok_p = _path.set(())
    tok_s = _seq.set(_fresh_holder())
    name = loaded.meta.get("name") if isinstance(loaded.meta, dict) else None
    description = loaded.meta.get("description") if isinstance(loaded.meta, dict) else None
    raw_phases = loaded.meta.get("phases") if isinstance(loaded.meta, dict) else None
    phases = _normalize_meta_phases(raw_phases)
    try:
        args_text = _preview(rt.args) or ""
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_STARTED,
            name=name,
            description=description,
            message=f"Workflow started, args: {args_text}",
            phases=phases,
            # Ledgers exist (possibly unbounded) before the run starts, so the
            # budget badges can render from the first event instead of waiting
            # for the first agent to complete.
            budget=_budget_snapshot(rt.budget),
            workflow_budget=_wf_budget_snapshot(rt),
        ))
        result = await _invoke_loaded(loaded, rt.args)
        # The rail force-finishes in-flight agents instead of raising, so a
        # drained ledger can end a run with the script simply returning — no
        # entry gate ever fired (e.g. the *last* agent blew the ceiling and
        # there is no next agent() to stop). That return is not a completion:
        # reroute it through the BudgetExhausted handler below (terminal
        # sidecar + WORKFLOW_FAILED + scope), so the UI reports the overrun
        # instead of "workflow completed" and a same-name relaunch gets a
        # fresh ceiling rather than resuming a dead tally.
        if rt.budget.exhausted:
            raise BudgetExhausted(
                f"session token budget exhausted: {rt.budget.spent}/{rt.budget.total}",
                scope="session", spent=rt.budget.spent, total=rt.budget.total,
                workflow_spent=rt.workflow_budget.spent,
                workflow_total=rt.workflow_budget.total,
                top_phases=_top_phases(rt),
            )
        if rt.workflow_budget.exhausted:
            raise BudgetExhausted(
                f"workflow token budget exhausted: {rt.workflow_budget.spent}/{rt.workflow_budget.total}",
                scope="workflow", spent=rt.workflow_budget.spent,
                total=rt.workflow_budget.total,
                workflow_spent=rt.workflow_budget.spent,
                workflow_total=rt.workflow_budget.total,
                top_phases=_top_phases(rt),
            )
        result_text = _preview(result) or ""
        await _write_seal_record(rt, terminal_status="completed")
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_COMPLETED,
            name=name,
            description=description,
            message=f"Workflow completed, result: {result_text}",
        ))
        return result
    except BudgetExhausted as exc:
        # Scope decides the terminal event: a workflow (per-run) ceiling hit is
        # FAILED — retryable by editing the script and re-running under the same
        # run_id (cache hits re-bill spent). A session ceiling hit is STOPPED —
        # not recoverable by any script edit, so it is sealed terminal.
        if exc.scope == "workflow":
            await _write_pause_record(rt, pause_reason="workflow_budget_exhausted")
            rt.progress_sink(WorkflowProgressEvent(
                kind=ProgressKind.WORKFLOW_FAILED,
                name=name,
                description=description,
                message=f"Workflow failed, exception: {exc}",
                budget=_budget_snapshot(rt.budget),
                workflow_budget=_wf_budget_snapshot(rt),
                budget_exhausted_scope=exc.scope,
            ))
        else:
            await _write_seal_record(rt, terminal_status="stopped")
            rt.progress_sink(WorkflowProgressEvent(
                kind=ProgressKind.WORKFLOW_STOPPED,
                name=name,
                description=description,
                message=f"Workflow stopped, exception: {exc}",
                budget=_budget_snapshot(rt.budget),
                workflow_budget=_wf_budget_snapshot(rt),
                budget_exhausted_scope=exc.scope,
            ))
        raise
    except WorkflowAborted as exc:
        # Cooperative control signal: pause / early_return (edit & rerun) are
        # resumable on the same run_id → pause record; stop is terminal → seal.
        if exc.reason in ("pause", "early_return"):
            await _write_pause_record(rt, pause_reason=exc.reason)
        else:  # "stop"
            await _write_seal_record(rt, terminal_status="stopped")
        raise
    except Exception as exc:
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_FAILED,
            name=name,
            description=description,
            message=f"Workflow failed, exception: {exc}"))
        raise
    except asyncio.CancelledError:
        # controller.stop/pause cancels the task mid-LLM-call (no abort checkpoint
        # was reached), so WorkflowAborted never fires and its record is never
        # written. Seal a stop here so a later re-run forces a fresh run_id
        # instead of resuming the terminated run. uncancel() lets the await
        # below run to completion instead of re-raising immediately.
        if rt.abort_event is not None and rt.abort_event.reason == "stop":
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            await _write_seal_record(rt, terminal_status="stopped")
        raise
    finally:
        _seq.reset(tok_s)
        _path.reset(tok_p)
        _rt.reset(tok_rt)
        reset_provider(tok_prov)


async def run_workflow(
    path: str,
    *,
    args: Any = None,
    backend: AgentBackend | None = None,
    resume: str | None = None,
    journal_path: str | None = None,
    strict: bool = False,
    log_sink: Callable[[str], None] | None = None,
    progress_sink: ProgressSink | None = None,
    cap: int | None = None,
    agent_gate: AgentAdmission | None = None,
    budget: BudgetLedger | None = None,
    workflow_budget: BudgetLedger | None = None,
    abort_event: AbortSignal | None = None,
    run_id: str | None = None,
) -> Any:
    # The ``swarmflow`` name a script imports the primitives under is registered
    # in ``sys.modules`` once at facade import time; the mapping is fixed for the
    # process and there is nothing to install or tear down per run. See
    # ``workflow.engine.facade._register_aliases``.
    log = log_sink or _silent
    loaded = load_workflow_source(path)
    for w in loaded.warnings:
        log(f"[lint] {w}")
    if strict and loaded.warnings:
        from .errors import LintError

        raise LintError(f"{len(loaded.warnings)} lint warning(s) in strict mode")

    # The WAL is a sidecar of the canonical journal write-path (``<journal>.wal``):
    # fresh records are appended to it as they complete, so a mid-run crash (no
    # save) stays recoverable, and a residual WAL is replayed on the next load.
    # Derived in-engine from the given path (no agent_teams import — engine stays
    # business-agnostic); the journal path itself comes from the caller (paths.py).
    wal_path = f"{journal_path}.wal" if journal_path else None
    journal = await Journal.load(resume, wal_path=wal_path)
    log(f"[wf] journal loaded: prior_records={len(journal.prior)} path={resume} wal={wal_path}")
    # Per-run budget on resume is NOT restored from a snapshot — the ledger
    # starts at spent=0 and the emit hooks re-bill it by replaying cache hits:
    # every cache-hit agent adds its record's stored ``tokens`` back, so the
    # tally is recomputed agent-by-agent for the *current* script structure.
    # A paused-but-unfinished agent (no call record) re-runs live and rebills,
    # which is exactly the "redesign and relaunch" semantics: unchanged agents
    # replay free (re-billed from their record), changed ones bill afresh.
    rt = Runtime(
        backend=backend or MockBackend(),
        journal=journal,
        args=args,
        log_sink=log,
        progress_sink=progress_sink or noop_progress_sink,
        strict=strict,
        cap_override=cap,
        budget=budget if budget is not None else BudgetLedger(),
        workflow_budget=workflow_budget if workflow_budget is not None else _resolve_workflow_budget(
            loaded,
            session_budget=budget,
        ),
        abort_event=abort_event,
        agent_gate=agent_gate,
        run_id=run_id,
    )
    # Hand the ledger to the backend: it is the only layer that sees what a call
    # really costs, so it does the accounting and the engine only reads. The
    # per-run ledger is bound too so the rail bills both (session-wide + per-run).
    rt.backend.bind_budget(rt.budget)
    _bind_workflow_budget(rt.backend, rt.workflow_budget)
    # Hand the progress sink to the backend too so it can emit live mid-call
    # activity (worker tool calls) alongside the engine's start/end hooks.
    rt.backend.bind_progress_sink(rt.progress_sink)
    try:
        result = await _exec_loaded(loaded, rt)
    finally:
        # Close any stateful sessions the backend opened during the run. Best
        # effort — a teardown error must never mask the run's own outcome.
        try:
            await rt.backend.aclose()
        except Exception as exc:  # noqa: BLE001 - teardown is best-effort
            log(f"[wf] backend.aclose() failed: {exc}")
    if journal_path:
        # Reached only when the workflow ran to normal completion (any
        # interrupt / crash / cancellation re-raises and skips this line, leaving
        # the WAL for recovery). finalize = atomic journal write + terminal WAL
        # removal; a future mid-run checkpoint must call save() (keeps the WAL).
        # The seal record was already written by _exec_loaded's completed branch.
        await rt.journal.finalize(journal_path)
    return result
